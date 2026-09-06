"""Background task: fills in TMDB metadata (tmdb_id, canonical title/year,
poster_path, overview) for media_items rows that were auto-adopted from the
filesystem (library_adopt.py) rather than archived through the normal
TMDB-matched preview/confirm flow, which already has metadata by save time.
Also backfills vote_average, and per-episode episode_title/air_date for TV
rows, onto already-matched rows that predate those fields, so a library
archived (or adopted) before they existed picks them up on its own instead
of needing a manual "Refresh Metadata" click -- unmatched items are drained
first, then vote_average gaps, then episode_title gaps, each one row at a
time.

Runs one lookup at a time via asyncio.to_thread so TMDBScraper's own
internal rate limiting (a blocking time.sleep) doesn't block the event loop.
No extra pacing is added here beyond that -- looping tightly while there's a
backlog is fine since the scraper already throttles actual HTTP requests.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

from app.core.tmdb_client import TMDBClient, genres_for, resolve_season_episodes, vote_average_for
from app.core.tvmaze_client import TVmazeClient
from app.database import Database
from app.dependencies import get_database, get_tmdb_client, get_tvmaze_client

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 15
ERROR_BACKOFF_SECONDS = 5


def match_one(db: Database, tmdb: TMDBClient, tvmaze: TVmazeClient) -> bool:
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
        metadata = {
            "poster_path": m.poster_path,
            "overview": m.overview,
            "vote_average": vote_average_for(m),
            "genres": genres_for(m),
        }
        # Episode name/air_date: an adopted TV file only ever got here via
        # library_adopt.py (no archive/preview flow, which is the only other
        # place these get set -- see archiver.py), so without this an
        # adopted show never has episode_title and the detail pane's
        # "Show episode names" toggle (which needs at least one) never
        # appears for it.
        if row["media_type"] == "tv" and m.tmdb_id is not None and row["season_number"] is not None:
            season_episodes = resolve_season_episodes(tmdb, tvmaze, m.tmdb_id, row["season_number"])
            ep = next((e for e in season_episodes if e.get("episode_number") == row["episode_number"]), None)
            if ep:
                metadata["episode_title"] = ep.get("name")
                metadata["air_date"] = ep.get("air_date")
        db.update_media_item(
            row["id"],
            tmdb_id=m.tmdb_id,
            title=m.title,
            year=m.year if m.year is not None else row["year"],
            metadata=metadata,
            match_attempted_at=now,
        )
        logger.info("Matched adopted item %r -> tmdb_id=%s", row["title"], m.tmdb_id)
    else:
        db.update_media_item(row["id"], match_attempted_at=now)
        logger.info("No TMDB match found for adopted item %r; will retry later", row["title"])

    return True


def refresh_vote_average_one(db: Database, tmdb: TMDBClient) -> bool:
    """Backfills vote_average onto one already-matched item that predates
    that field (see list_items_missing_vote_average). Existing metadata
    keys (ffprobe fields, episode_title, ...) are preserved -- only the
    TMDB-sourced ones are refreshed, same as the manual "Refresh Metadata"
    button. Returns True if there was a row to process, False if the queue
    is empty."""
    rows = db.list_items_missing_vote_average(limit=1)
    if not rows:
        return False
    row = rows[0]

    media = (
        tmdb.refresh_tv_details(row["tmdb_id"])
        if row["media_type"] == "tv"
        else tmdb.refresh_movie_details(row["tmdb_id"])
    )

    now = datetime.now(timezone.utc).isoformat()
    if media is None:
        db.update_media_item(row["id"], match_attempted_at=now)
        logger.info("Could not refresh TMDB details for %r; will retry later", row["title"])
        return True

    try:
        existing_meta = json.loads(row["metadata"]) if row["metadata"] else {}
    except json.JSONDecodeError:
        existing_meta = {}
    if not isinstance(existing_meta, dict):
        existing_meta = {}

    db.update_media_item(
        row["id"],
        metadata={
            **existing_meta,
            "poster_path": media.poster_path,
            "overview": media.overview,
            "vote_average": vote_average_for(media),
            "genres": genres_for(media),
        },
        match_attempted_at=now,
    )
    logger.info("Backfilled vote_average for %r (tmdb_id=%s)", row["title"], row["tmdb_id"])
    return True


def refresh_episode_title_one(db: Database, tmdb: TMDBClient, tvmaze: TVmazeClient) -> bool:
    """Backfills episode_title/air_date onto one already-matched TV episode
    row that doesn't have one yet (see list_tv_episodes_missing_title) --
    covers a show adopted from the filesystem before match_one learned to
    fetch per-episode names (older library), or one archived when TMDB/TVmaze
    had no title for that episode at the time. All other metadata keys are
    preserved untouched. Returns True if there was a row to process, False
    if the queue is empty."""
    rows = db.list_tv_episodes_missing_title(limit=1)
    if not rows:
        return False
    row = rows[0]

    now = datetime.now(timezone.utc).isoformat()
    season_episodes = resolve_season_episodes(tmdb, tvmaze, row["tmdb_id"], row["season_number"])
    ep = next((e for e in season_episodes if e.get("episode_number") == row["episode_number"]), None)
    if ep is None:
        db.update_media_item(row["id"], match_attempted_at=now)
        logger.info("No episode title available yet for %r S%02dE%02d; will retry later",
                    row["title"], row["season_number"], row["episode_number"])
        return True

    try:
        existing_meta = json.loads(row["metadata"]) if row["metadata"] else {}
    except json.JSONDecodeError:
        existing_meta = {}
    if not isinstance(existing_meta, dict):
        existing_meta = {}

    db.update_media_item(
        row["id"],
        metadata={
            **existing_meta,
            "episode_title": ep.get("name"),
            "air_date": ep.get("air_date") or existing_meta.get("air_date"),
        },
        match_attempted_at=now,
    )
    logger.info("Backfilled episode_title for %r S%02dE%02d", row["title"], row["season_number"], row["episode_number"])
    return True


async def run_metadata_backfill() -> None:
    while True:
        try:
            db, tmdb, tvmaze = get_database(), get_tmdb_client(), get_tvmaze_client()
            found = await asyncio.to_thread(match_one, db, tmdb, tvmaze)
            if not found:
                found = await asyncio.to_thread(refresh_vote_average_one, db, tmdb)
            if not found:
                found = await asyncio.to_thread(refresh_episode_title_one, db, tmdb, tvmaze)
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
