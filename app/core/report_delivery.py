"""Periodic push of the Reports tab's own summary through the same
Discord/Telegram/Pushover/generic-webhook channels notifications.* already
uses -- reuses app.api.routes.reports.build_report_summary (the same
computation GET /api/reports/summary calls) rather than duplicating it, and
tracker.py's post_discord/post_telegram/post_pushover transports rather than
adding a new one. Driven by app.core.scheduler.run_periodic_report_delivery.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests

from app.config_loader import AppConfig
from app.core.tracker import post_discord, post_pushover, post_telegram
from app.database import Database
from app.models import ReportSummaryOut

logger = logging.getLogger(__name__)


def _iso_week_label(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def previous_complete_period(frequency: str, today: date) -> tuple[date, date, str]:
    """Returns (start, end, label) for the most recently completed period as
    of `today` -- e.g. called on any day in March, "monthly" gives all of
    February. `label` is a short string identifying the period ("2026-02",
    "2026-Q1", "2026-W07") so the scheduler can tell whether a given period
    has already been sent without needing a DB column for it.
    """
    if frequency == "weekly":
        # Most recent full Mon-Sun week -- today's own week is still in
        # progress, so back up to the Sunday before this week started.
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return start, end, _iso_week_label(start)

    if frequency == "quarterly":
        current_quarter = (today.month - 1) // 3 + 1
        quarter = current_quarter - 1 if current_quarter > 1 else 4
        year = today.year if current_quarter > 1 else today.year - 1
        start_month = (quarter - 1) * 3 + 1
        start = date(year, start_month, 1)
        end_month = start_month + 3
        end = date(year, end_month, 1) - timedelta(days=1) if end_month <= 12 else date(year, 12, 31)
        return start, end, f"{year}-Q{quarter}"

    # "monthly" (also the fallback for an unrecognized value -- same
    # tradeoff scheduler.py's _seconds_until makes for a bad cron_time)
    first_of_this_month = today.replace(day=1)
    end = first_of_this_month - timedelta(days=1)
    start = end.replace(day=1)
    return start, end, f"{start.year}-{start.month:02d}"


def _format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def build_digest_message(summary: ReportSummaryOut) -> str:
    g, w, t = summary.growth, summary.watch_activity, summary.tracker_activity
    return (
        f"Media Manager report {summary.start_date} to {summary.end_date}: "
        f"+{g.movies_added} movie(s), +{g.tv_episodes_added} TV episode(s) "
        f"({_format_bytes(g.total_size_bytes_added)}); "
        f"watched {w.movies_watched} movie(s), {w.tv_episodes_watched} episode(s); "
        f"{t.notifications_sent} tracker notification(s)."
    )


def _fire_webhook(webhook_url: str, summary: ReportSummaryOut) -> None:
    try:
        requests.post(webhook_url, json=summary.model_dump(), timeout=10)
    except requests.RequestException as exc:
        logger.warning("Report webhook POST to %s failed: %s", webhook_url, exc)


def deliver_report(config: AppConfig, summary: ReportSummaryOut) -> bool:
    """Fires through whichever channels are configured. Returns True if at
    least one channel was configured (for the scheduler's own logging), not
    whether the HTTP call actually succeeded -- same as the tracker/low-disk
    alert code this mirrors, none of which retries a failed push itself."""
    notif = config.notifications
    message = build_digest_message(summary)
    sent = False
    if notif.webhook_url:
        _fire_webhook(notif.webhook_url, summary)
        sent = True
    if notif.discord_webhook_url:
        post_discord(notif.discord_webhook_url, message)
        sent = True
    if notif.telegram_bot_token and notif.telegram_chat_id:
        post_telegram(notif.telegram_bot_token, notif.telegram_chat_id, message)
        sent = True
    if notif.pushover_api_token and notif.pushover_user_key:
        post_pushover(notif.pushover_api_token, notif.pushover_user_key, message, title="Periodic report")
        sent = True
    return sent


def generate_and_deliver(config: AppConfig, db: Database, today: date | None = None) -> str | None:
    """Builds the report for the most recently completed period and pushes
    it out. Returns the period label on success (for the scheduler to record
    as "last sent"), None if no channel is configured to receive it."""
    from app.api.routes.reports import build_report_summary  # local: reports.py -> status.py ->
    # scheduler.py -> report_delivery.py would otherwise be circular at import time

    today = today or datetime.now().date()
    start, end, label = previous_complete_period(config.reports.frequency, today)
    summary = build_report_summary(db, start, end)
    if not deliver_report(config, summary):
        logger.info("Periodic report for %s generated but no notification channel is configured", label)
        return None
    logger.info("Periodic report for %s delivered", label)
    return label
