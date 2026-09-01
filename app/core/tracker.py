"""Detects new TV seasons and movie sequels/releases for tracked titles."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from app.core.tmdb_client import TMDBClient
from app.core.tvmaze_client import TVmazeClient
from app.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def maybe_auto_track(
    db: Database,
    auto_track_enabled: bool,
    tmdb_id: int | None,
    media_type: str,
    title: str,
    season: int | None = None,
) -> None:
    """Adds a freshly-archived title to the tracker automatically, when
    tracker.auto_track_new is on -- opt-in, since not every archived title
    is something the user wants season/sequel alerts for. For TV,
    current_season_archived only ever moves forward: archiving an old
    episode out of order shouldn't make the tracker think the show
    regressed and re-flag a season the user already has.
    """
    if not auto_track_enabled or tmdb_id is None:
        return
    fields: dict = {}
    if media_type == "tv" and season is not None:
        existing = db.get_tracker(tmdb_id, media_type)
        current_max = existing["current_season_archived"] if existing else None
        if current_max is None or season > current_max:
            fields["current_season_archived"] = season
    db.upsert_tracker(tmdb_id=tmdb_id, media_type=media_type, title=title, **fields)


def _is_due(row: dict, now: datetime) -> bool:
    """Whether a scheduled check_for_updates run should touch this title --
    "Check Now" in the UI bypasses this entirely and always checks. A
    snooze always wins over a custom interval (snoozing is a deliberate,
    time-boxed "not now" from the user)."""
    if row["snoozed_until"]:
        if now < datetime.fromisoformat(row["snoozed_until"]):
            return False
    if row["check_interval_hours"] and row["last_checked"]:
        next_due = datetime.fromisoformat(row["last_checked"]) + timedelta(hours=row["check_interval_hours"])
        if now < next_due:
            return False
    return True


def check_tv_show(
    db: Database, tmdb: TMDBClient, tracker_row: dict, tvmaze: TVmazeClient | None = None
) -> bool:
    """Returns True if this check just flipped pending_notification on (as
    opposed to it already being pending, or staying not-pending) -- used by
    check_for_updates to fire a webhook only on the transition, not on every
    check of an already-pending title.

    `tvmaze` is optional (defaults to None, not required) since this is
    called from ~25 existing tests plus scheduler.py/cron_job.py that predate
    it -- when given and enabled, it only enriches the tracker row with a
    next-episode air date; it never changes the pending_notification trigger
    above, which stays purely TMDB-season-count-based.
    """
    details = tmdb.get_tv_details(tracker_row["tmdb_id"])
    latest_known_season = None
    if details:
        latest_known_season = details.raw.get("number_of_seasons")

    current = tracker_row["current_season_archived"] or 0
    pending = bool(latest_known_season and latest_known_season > current)
    newly_pending = pending and not tracker_row["pending_notification"]

    next_episode_air_date = None
    if tvmaze is not None and tvmaze.enabled:
        imdb_id = tmdb.get_external_imdb_id(tracker_row["tmdb_id"], "tv")
        if imdb_id:
            show_info = tvmaze.get_show_info_by_imdb(imdb_id)
            if show_info:
                next_episode_air_date = show_info.next_episode_air_date

    db.upsert_tracker(
        tmdb_id=tracker_row["tmdb_id"],
        media_type="tv",
        title=tracker_row["title"],
        latest_known_season=latest_known_season,
        last_checked=_now(),
        pending_notification=1 if pending else tracker_row["pending_notification"],
        next_episode_air_date=next_episode_air_date,
    )
    if pending:
        logger.info("New season detected for %s: season %s", tracker_row["title"], latest_known_season)
    return newly_pending


def check_movie_collection(db: Database, tmdb: TMDBClient, tracker_row: dict) -> bool:
    details = tmdb.get_movie_details(tracker_row["tmdb_id"])
    collection_id = details.raw.get("belongs_to_collection", {}).get("id") if details and details.raw else None

    status = tracker_row["movie_release_status"]
    pending = False
    if collection_id:
        collection = tmdb.get_collection_movies(collection_id)
        known_ids = {tracker_row["tmdb_id"]}
        new_entries = [m for m in collection if m.tmdb_id not in known_ids]
        if new_entries:
            status = f"{len(new_entries)} related title(s) found in collection"
            pending = True

    newly_pending = pending and not tracker_row["pending_notification"]

    db.upsert_tracker(
        tmdb_id=tracker_row["tmdb_id"],
        media_type="movie",
        title=tracker_row["title"],
        movie_release_status=status,
        last_checked=_now(),
        pending_notification=1 if pending else tracker_row["pending_notification"],
    )
    if pending:
        logger.info("Sequel/related release detected for %s", tracker_row["title"])
    return newly_pending


def _message_for(row: dict) -> str:
    if row["media_type"] == "tv":
        base = f"New season available for {row['title']}"
        next_air_date = row.get("next_episode_air_date")
        return f"{base} (next: {next_air_date})" if next_air_date else base
    return f"{row['title']}: {row.get('movie_release_status') or 'new release detected'}"


def _digest_message(rows: list[dict]) -> str:
    return "; ".join(_message_for(row) for row in rows)


def _fire_webhook(webhook_url: str, rows: list[dict]) -> None:
    """One POST per check_for_updates run, not one per newly-pending title --
    a run that flips five titles at once (e.g. after being offline a while)
    should send one digest, not fire the webhook five times back to back."""
    if len(rows) == 1:
        row = rows[0]
        payload = {"title": row["title"], "media_type": row["media_type"], "message": _message_for(row)}
    else:
        payload = {"count": len(rows), "titles": [row["title"] for row in rows], "message": _digest_message(rows)}
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Tracker webhook POST to %s failed: %s", webhook_url, exc)


def post_discord(discord_webhook_url: str, content: str) -> None:
    """Reused by low_disk_alert.py as well as the tracker's own digest --
    both just need to push a plain text message to the same three
    providers, so the transport lives here rather than being duplicated."""
    try:
        requests.post(discord_webhook_url, json={"content": content}, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Discord webhook POST failed: %s", exc)


def post_telegram(bot_token: str, chat_id: str, text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Telegram sendMessage failed: %s", exc)


def post_pushover(api_token: str, user_key: str, message: str, title: str = "Media Manager") -> None:
    try:
        requests.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": api_token, "user": user_key, "title": title, "message": message},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Pushover message POST failed: %s", exc)


def _send_discord(discord_webhook_url: str, rows: list[dict]) -> None:
    post_discord(discord_webhook_url, _digest_message(rows))


def _send_telegram(bot_token: str, chat_id: str, rows: list[dict]) -> None:
    post_telegram(bot_token, chat_id, _digest_message(rows))


def _send_pushover(api_token: str, user_key: str, rows: list[dict]) -> None:
    post_pushover(api_token, user_key, _digest_message(rows))


def check_for_updates(
    db: Database,
    tmdb: TMDBClient,
    webhook_url: str | None = None,
    discord_webhook_url: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    pushover_api_token: str | None = None,
    pushover_user_key: str | None = None,
    digest_mode: bool = False,
    tvmaze: TVmazeClient | None = None,
) -> int:
    """Main tracker entry point. Returns the number of items now pending notification.

    digest_mode=False (default) fires a notification the moment a title
    newly becomes pending -- real-time, one push per check_for_updates run
    that found something new. digest_mode=True instead sends nothing here
    per newly-pending title; the caller (the scheduler's own periodic loop)
    is expected to call send_digest() separately on whatever cadence it
    wants (daily/weekly), covering every title still pending, not just
    ones that changed in this particular run -- so a user who ignores a
    push still gets reminded on the next digest instead of only once.
    """
    tracked = db.list_tracked()
    now = datetime.now(timezone.utc)
    newly_pending_rows: list[dict] = []
    for row in tracked:
        if not _is_due(row, now):
            continue
        try:
            if row["media_type"] == "tv":
                newly_pending = check_tv_show(db, tmdb, row, tvmaze=tvmaze)
                # Refresh so a newly-fired message (below) reflects what
                # check_tv_show just persisted (e.g. next_episode_air_date),
                # not the pre-check snapshot from list_tracked() above.
                row = db.get_tracker(row["tmdb_id"], "tv") or row
            else:
                newly_pending = check_movie_collection(db, tmdb, row)
            db.log_operation(operation_type="tracker_check", status="success", details={"tmdb_id": row["tmdb_id"]})
            if newly_pending and not row["muted"]:
                newly_pending_rows.append(row)
                db.log_notification(row["id"], row["tmdb_id"], row["media_type"], row["title"], _message_for(row))
        except Exception as exc:  # noqa: BLE001 - one bad title shouldn't abort the whole run
            logger.error("Tracker check failed for tmdb_id=%s: %s", row["tmdb_id"], exc)
            db.log_operation(
                operation_type="tracker_check",
                status="failed",
                details={"tmdb_id": row["tmdb_id"]},
                error_message=str(exc),
            )

    if newly_pending_rows and not digest_mode:
        _fire_all_channels(
            newly_pending_rows, webhook_url, discord_webhook_url,
            telegram_bot_token, telegram_chat_id, pushover_api_token, pushover_user_key,
        )

    return len(db.list_pending_notifications())


def _fire_all_channels(
    rows: list[dict],
    webhook_url: str | None,
    discord_webhook_url: str | None,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
    pushover_api_token: str | None,
    pushover_user_key: str | None,
) -> None:
    if webhook_url:
        _fire_webhook(webhook_url, rows)
    if discord_webhook_url:
        _send_discord(discord_webhook_url, rows)
    if telegram_bot_token and telegram_chat_id:
        _send_telegram(telegram_bot_token, telegram_chat_id, rows)
    if pushover_api_token and pushover_user_key:
        _send_pushover(pushover_api_token, pushover_user_key, rows)


def send_digest(
    db: Database,
    webhook_url: str | None = None,
    discord_webhook_url: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    pushover_api_token: str | None = None,
    pushover_user_key: str | None = None,
) -> int:
    """Sends one batch notification covering every currently-pending (muted
    titles already excluded by list_pending_notifications), unacknowledged
    title -- the digest-mode counterpart to check_for_updates()'s real-time
    per-newly-pending push. Meant to be called on its own periodic cadence
    (see scheduler.py), independent of how often check_for_updates() itself
    runs. Returns the number of titles the digest covered (0 sends nothing).
    """
    pending = db.list_pending_notifications()
    if not pending:
        return 0
    _fire_all_channels(
        pending, webhook_url, discord_webhook_url,
        telegram_bot_token, telegram_chat_id, pushover_api_token, pushover_user_key,
    )
    return len(pending)
