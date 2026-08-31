"""In-process daily tracker check — replaces host cron when running in Docker.

Re-fetches config/db/tmdb-client singletons on every wake rather than
capturing them once at startup, so a TMDB key or path change made later via
the Settings UI takes effect on the next scheduled run without a restart.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

from app.core import backup
from app.core.tracker import check_for_updates, send_digest
from app.dependencies import get_config, get_database, get_tmdb_client

logger = logging.getLogger(__name__)

# In-memory only -- a container restart just means the next digest fires on
# the next cron_time wake instead of waiting out the rest of the interval,
# which is an acceptable reset for a homelab reminder feature, not worth a
# DB column or a migration to persist.
_last_digest_sent: datetime | None = None

# Last-run bookkeeping for the health widget (GET /api/status/tasks) --
# in-memory only, same tradeoff as _last_digest_sent above: neither backup
# nor maintenance has its own operation_log entry type, and a restart
# resetting "last ran" to unknown is a fine, honest answer for a homelab
# dashboard (it really doesn't know until the next cycle completes).
_task_status: dict[str, dict] = {
    "backup": {"last_run_at": None, "last_error": None},
    "maintenance": {"last_run_at": None, "last_error": None},
}


def get_task_status() -> dict[str, dict]:
    return _task_status


def _seconds_until(hhmm: str) -> float:
    try:
        hour, minute = (int(p) for p in hhmm.split(":"))
    except ValueError:
        logger.warning("Invalid tracker.cron_time %r, defaulting to 06:00", hhmm)
        hour, minute = 6, 0

    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_daily_tracker_check() -> None:
    while True:
        try:
            delay = _seconds_until(get_config().tracker.cron_time)
            logger.info("Next tracker check in %.0f seconds", delay)
            await asyncio.sleep(delay)

            db = get_database()
            tmdb = get_tmdb_client()
            config = get_config()
            notifications = config.notifications
            digest_mode = config.tracker.digest_mode
            pending = await asyncio.to_thread(
                check_for_updates,
                db,
                tmdb,
                notifications.webhook_url or None,
                notifications.discord_webhook_url or None,
                notifications.telegram_bot_token or None,
                notifications.telegram_chat_id or None,
                notifications.pushover_api_token or None,
                notifications.pushover_user_key or None,
                digest_mode,
            )
            logger.info("Scheduled tracker check complete: %d item(s) pending notification", pending)

            global _last_digest_sent
            if digest_mode:
                now = datetime.now(timezone.utc)
                due = _last_digest_sent is None or (now - _last_digest_sent).days >= config.tracker.digest_interval_days
                if due:
                    sent_count = await asyncio.to_thread(
                        send_digest,
                        db,
                        notifications.webhook_url or None,
                        notifications.discord_webhook_url or None,
                        notifications.telegram_bot_token or None,
                        notifications.telegram_chat_id or None,
                        notifications.pushover_api_token or None,
                        notifications.pushover_user_key or None,
                    )
                    if sent_count:
                        _last_digest_sent = now
                        logger.info("Sent digest covering %d pending title(s)", sent_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled tracker check failed; retrying in 1 hour")
            await asyncio.sleep(3600)


def start() -> asyncio.Task:
    return asyncio.ensure_future(run_daily_tracker_check())


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


MAINTENANCE_INTERVAL_SECONDS = 7 * 24 * 3600


async def run_weekly_maintenance() -> None:
    """WAL checkpoint + VACUUM on a fixed weekly interval (not tied to a
    time-of-day like the tracker check -- there's no reason this needs to
    run at a specific hour, just regularly)."""
    while True:
        try:
            await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
            db = get_database()
            await asyncio.to_thread(db.maintenance_checkpoint_and_vacuum)
            logger.info("Weekly DB maintenance (WAL checkpoint + VACUUM) complete")
            _task_status["maintenance"] = {"last_run_at": datetime.now(timezone.utc).isoformat(), "last_error": None}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Weekly DB maintenance failed; retrying next cycle")
            _task_status["maintenance"] = {"last_run_at": datetime.now(timezone.utc).isoformat(), "last_error": str(exc)}


def start_maintenance() -> asyncio.Task:
    return asyncio.ensure_future(run_weekly_maintenance())


async def stop_maintenance(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


BACKUP_INTERVAL_SECONDS = 24 * 3600


async def run_daily_backup() -> None:
    """Backs up the DB + config.yaml once every 24h. Skipped entirely, but
    still looping to notice a later config change, when backup.enabled is
    off. Same sleep-then-act shape as the tracker check and maintenance
    loops, for the same reason: re-reads config fresh on every wake so a
    Settings-tab change to backup.enabled/retention_days takes effect on
    the very next cycle without a restart."""
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL_SECONDS)
            config = get_config()
            if config.backup.enabled:
                await asyncio.to_thread(backup.run_backup, config)
                removed = await asyncio.to_thread(backup.prune_old_backups, config, config.backup.retention_days)
                logger.info("Daily backup complete (pruned %d expired backup(s))", removed)
                _task_status["backup"] = {"last_run_at": datetime.now(timezone.utc).isoformat(), "last_error": None}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Daily backup failed; retrying next cycle")
            _task_status["backup"] = {"last_run_at": datetime.now(timezone.utc).isoformat(), "last_error": str(exc)}


def start_backup() -> asyncio.Task:
    return asyncio.ensure_future(run_daily_backup())


async def stop_backup(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
