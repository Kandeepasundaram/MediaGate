"""Background task: fills in TMDB metadata (tmdb_id, canonical title/year,
poster_path, overview) for media_items rows that were auto-adopted from the
filesystem (library_adopt.py) rather than archived through the normal
TMDB-matched preview/confirm flow, which already has metadata by save time.

Runs one lookup at a time via asyncio.to_thread so TMDBScraper's own
internal rate limiting (a blocking time.sleep) doesn't block the event loop.
No extra pacing is added here beyond that -- looping tightly while there's a
backlog is fine since the scraper already throttles actual HTTP requests.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone

from app.core.tmdb_client import TMDBClient, genres_for, vote_average_for
from app.database import Database
from app.dependencies import get_database, get_tmdb_client

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 15
ERROR_BACKOFF_SECONDS = 5


def match_one(db: Database, tmdb: TMDBClient) -> bool:
    """Attempts to match a single unmatched item. Returns True if there was
    an item to process (matched or not), False if the queue is empty."""
    rows = db.list_unmatched_media_items(limit=1)
    if not rows:
        return False
    row = rows[0]

    if row["media_type"] == "movie":
        matches = tmdb.search_movie(row["title"], row["year"])
    else:
        matches = tmdb.search_tv(row["title"])

    now = datetime.now(timezone.utc).isoformat()
    if matches:
        m = matches[0]
        db.update_media_item(
            row["id"],
            tmdb_id=m.tmdb_id,
            title=m.title,
            year=m.year if m.year is not None else row["year"],
            metadata={
                "poster_path": m.poster_path,
                "overview": m.overview,
                "vote_average": vote_average_for(m),
                "genres": genres_for(m),
            },
            match_attempted_at=now,
        )
        logger.info("Matched adopted item %r -> tmdb_id=%s", row["title"], m.tmdb_id)
    else:
        db.update_media_item(row["id"], match_attempted_at=now)
        logger.info("No TMDB match found for adopted item %r; will retry later", row["title"])

    return True


async def run_metadata_backfill() -> None:
    while True:
        try:
            found = await asyncio.to_thread(match_one, get_database(), get_tmdb_client())
            if not found:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Metadata backfill step failed; retrying shortly")
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)


def start() -> asyncio.Task:
    return asyncio.ensure_future(run_metadata_backfill())


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
