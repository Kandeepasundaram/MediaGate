"""Single-page view of every tracked/owned TV show, with two alert
sections: titles the tracker has flagged as having new content not yet
archived (reuses archive_tracker.pending_notification, the same signal the
Notifications tab already surfaces), and TV shows with archived episodes
the user hasn't watched yet (a pure library query -- independent of
tracking, since an owned-but-unwatched episode needs no tracker entry to
matter).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes.library_common import _metadata_dict
from app.database import Database
from app.dependencies import get_database
from app.models import (
    TrackerNotificationOut,
    WatchlistResponse,
    WatchlistUnwatchedEpisodeOut,
    WatchlistUnwatchedShowOut,
)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _pending_out(row: dict) -> TrackerNotificationOut:
    return TrackerNotificationOut(
        id=row["id"],
        tmdb_id=row["tmdb_id"],
        media_type=row["media_type"],
        title=row["title"],
        current_season_archived=row["current_season_archived"],
        latest_known_season=row["latest_known_season"],
        movie_release_status=row["movie_release_status"],
        pending_notification=bool(row["pending_notification"]),
        muted=bool(row["muted"]),
        last_checked=row["last_checked"],
        snoozed_until=row["snoozed_until"],
        check_interval_hours=row["check_interval_hours"],
        next_episode_air_date=row["next_episode_air_date"],
        poster_path=row["poster_path"],
        overview=row["overview"],
        watched_through_season=row["watched_through_season"],
        watched_through_episode=row["watched_through_episode"],
    )


def _episode_sort_key(row: dict) -> tuple[int, int]:
    # Unnumbered rows (no season/episode parsed) sort last within a show,
    # rather than colliding at (0, 0) with a real S00E00 special.
    season = row["season_number"] if row["season_number"] is not None else 10_000
    episode = row["episode_number"] if row["episode_number"] is not None else 10_000
    return (season, episode)


def _unwatched_show_out(tmdb_id: int, rows: list[dict]) -> WatchlistUnwatchedShowOut | None:
    unwatched = [r for r in rows if not r["watched"]]
    if not unwatched:
        return None
    unwatched.sort(key=_episode_sort_key)
    next_row = unwatched[0]
    next_meta = _metadata_dict(next_row)
    first_meta = _metadata_dict(rows[0])
    return WatchlistUnwatchedShowOut(
        tmdb_id=tmdb_id,
        title=rows[0]["title"],
        poster_path=first_meta.get("poster_path"),
        unwatched_count=len(unwatched),
        total_count=len(rows),
        next_up=WatchlistUnwatchedEpisodeOut(
            season_number=next_row["season_number"],
            episode_number=next_row["episode_number"],
            episode_title=next_meta.get("episode_title"),
            air_date=next_meta.get("air_date"),
        ),
    )


@router.get("", response_model=WatchlistResponse)
def get_watchlist(db: Database = Depends(get_database)) -> WatchlistResponse:
    needs_download = [_pending_out(r) for r in db.list_pending_notifications()]

    by_tmdb_id: dict[int, list[dict]] = {}
    for row in db.list_media_items(media_type="tv"):
        if row["tmdb_id"] is None:
            continue
        by_tmdb_id.setdefault(row["tmdb_id"], []).append(row)

    needs_watching = [
        out
        for tmdb_id, rows in by_tmdb_id.items()
        if (out := _unwatched_show_out(tmdb_id, rows)) is not None
    ]
    needs_watching.sort(key=lambda s: -s.unwatched_count)

    return WatchlistResponse(needs_download=needs_download, needs_watching=needs_watching)
