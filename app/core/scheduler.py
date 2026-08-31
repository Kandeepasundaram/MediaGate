"""In-process daily tracker check — replaces host cron when running in Docker.

Re-fetches config/db/tmdb-client singletons on every wake rather than
capturing them once at startup, so a TMDB key or path change made later via
the Settings UI takes effect on the next scheduled run without a restart.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from app.core import backup
from app.core.tracker import check_for_updates
from app.dependencies import get_config, get_database, get_tmdb_client

logger = logging.getLogger(__name__)


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
            notifications = get_config().notifications
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
            )
            logger.info("Scheduled tracker check complete: %d item(s) pending notification", pending)
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
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Weekly DB maintenance failed; retrying next cycle")


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
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily backup failed; retrying next cycle")


def start_backup() -> asyncio.Task:
    return asyncio.ensure_future(run_daily_backup())


async def stop_backup(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
