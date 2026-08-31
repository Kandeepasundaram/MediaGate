"""Opt-in push notification when a configured media path's free space
drops below a threshold -- reuses the same Discord/Telegram/Pushover/
generic-webhook channels tracker.py already sends through, just with a
disk-space message instead of a new-release one.

Checked from GET /api/status/storage (see status.py), the same place
storage_snapshots gets recorded -- both are opportunistic, tied to
whenever the Settings tab (or anything else) happens to poll storage,
rather than a dedicated scheduler task.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config_loader import AppConfig
from app.core.tracker import post_discord, post_pushover, post_telegram

logger = logging.getLogger(__name__)

# label -> ISO date string of the last alert sent for it. In-memory and
# reset on restart, same tradeoff scheduler.py's own _task_status makes for
# backup/maintenance -- worst case after a restart is one extra alert,
# which beats a DB migration just to dedupe a once-a-day push.
_last_alerted: dict[str, str] = {}


def _fire(config: AppConfig, message: str) -> None:
    notif = config.notifications
    if notif.discord_webhook_url:
        post_discord(notif.discord_webhook_url, message)
    if notif.telegram_bot_token and notif.telegram_chat_id:
        post_telegram(notif.telegram_bot_token, notif.telegram_chat_id, message)
    if notif.pushover_api_token and notif.pushover_user_key:
        post_pushover(notif.pushover_api_token, notif.pushover_user_key, message, title="Low disk space")


def check_low_disk(config: AppConfig, label: str, free_bytes: int) -> bool:
    """Returns True if an alert was actually sent (for tests / logging),
    False if disabled, above threshold, or already alerted today."""
    notif = config.notifications
    if not notif.low_disk_alert_enabled:
        return False

    threshold_bytes = notif.low_disk_threshold_gb * 1024**3
    today = datetime.now(timezone.utc).date().isoformat()

    if free_bytes >= threshold_bytes:
        _last_alerted.pop(label, None)  # recovered -- next drop below threshold alerts again
        return False

    if _last_alerted.get(label) == today:
        return False

    _last_alerted[label] = today
    free_gb = free_bytes / 1024**3
    message = f"Low disk space: {label} has {free_gb:.1f} GB free (threshold {notif.low_disk_threshold_gb:g} GB)."
    _fire(config, message)
    logger.warning("Low disk alert fired for %s: %.1f GB free", label, free_gb)
    return True
