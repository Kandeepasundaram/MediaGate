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
            pending = await asyncio.to_thread(check_for_updates, db, tmdb)
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
