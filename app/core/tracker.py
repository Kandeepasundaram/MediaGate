"""Detects new TV seasons and movie sequels/releases for tracked titles."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.tmdb_client import TMDBClient
from app.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_tv_show(db: Database, tmdb: TMDBClient, tracker_row: dict) -> None:
    details = tmdb.get_tv_details(tracker_row["tmdb_id"])
    latest_known_season = None
    if details:
        latest_known_season = details.raw.get("number_of_seasons")

    current = tracker_row["current_season_archived"] or 0
    pending = bool(latest_known_season and latest_known_season > current)

    db.upsert_tracker(
        tmdb_id=tracker_row["tmdb_id"],
        media_type="tv",
        title=tracker_row["title"],
        latest_known_season=latest_known_season,
        last_checked=_now(),
        pending_notification=1 if pending else tracker_row["pending_notification"],
    )
    if pending:
        logger.info("New season detected for %s: season %s", tracker_row["title"], latest_known_season)


def check_movie_collection(db: Database, tmdb: TMDBClient, tracker_row: dict) -> None:
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


def check_for_updates(db: Database, tmdb: TMDBClient) -> int:
    """Main tracker entry point. Returns the number of items now pending notification."""
    tracked = db.list_tracked()
    for row in tracked:
        try:
            if row["media_type"] == "tv":
                check_tv_show(db, tmdb, row)
            else:
                check_movie_collection(db, tmdb, row)
            db.log_operation(operation_type="tracker_check", status="success", details={"tmdb_id": row["tmdb_id"]})
        except Exception as exc:  # noqa: BLE001 - one bad title shouldn't abort the whole run
            logger.error("Tracker check failed for tmdb_id=%s: %s", row["tmdb_id"], exc)
            db.log_operation(
                operation_type="tracker_check",
                status="failed",
                details={"tmdb_id": row["tmdb_id"]},
                error_message=str(exc),
            )

    return len(db.list_pending_notifications())
