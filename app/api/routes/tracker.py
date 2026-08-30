from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.tmdb_client import TMDBClient
from app.core.tracker import check_movie_collection, check_tv_show
from app.database import Database
from app.dependencies import get_database, get_tmdb_client
from app.models import (
    TrackedListResponse,
    TrackerAcknowledgeRequest,
    TrackerAddRequest,
    TrackerMuteRequest,
    TrackerNotificationOut,
    TrackerNotificationsResponse,
    TrackerStatusResponse,
)

router = APIRouter(prefix="/api/tracker", tags=["tracker"])


def _to_out(row: dict) -> TrackerNotificationOut:
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
    )


@router.get("/notifications", response_model=TrackerNotificationsResponse)
def get_notifications(db: Database = Depends(get_database)) -> TrackerNotificationsResponse:
    rows = db.list_pending_notifications()
    return TrackerNotificationsResponse(notifications=[_to_out(r) for r in rows])


@router.post("/acknowledge")
def acknowledge_notification(payload: TrackerAcknowledgeRequest, db: Database = Depends(get_database)) -> dict:
    db.acknowledge_notification(payload.tracker_id)
    return {"acknowledged": True}


@router.post("/add")
def add_tracker(payload: TrackerAddRequest, db: Database = Depends(get_database)) -> dict:
    db.upsert_tracker(
        tmdb_id=payload.tmdb_id,
        media_type=payload.media_type,
        title=payload.title,
        current_season_archived=payload.current_season_archived,
        last_checked=datetime.now(timezone.utc).isoformat(),
    )
    row = db.get_tracker(payload.tmdb_id, payload.media_type)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create tracker entry")
    return {"tracker": _to_out(row)}


@router.get("/list", response_model=TrackedListResponse)
def list_tracked(db: Database = Depends(get_database)) -> TrackedListResponse:
    """Every tracked title, muted or not -- backs a "Tracked Titles" panel
    distinct from /notifications (which only surfaces unmuted, pending ones)."""
    return TrackedListResponse(tracked=[_to_out(r) for r in db.list_tracked()])


@router.post("/{tracker_id}/mute")
def mute_tracker(tracker_id: int, payload: TrackerMuteRequest, db: Database = Depends(get_database)) -> dict:
    if db.get_tracker_by_id(tracker_id) is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    db.set_tracker_muted(tracker_id, payload.muted)
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.post("/{tracker_id}/check-now")
def check_tracker_now(
    tracker_id: int,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> dict:
    """Runs the tracker's TMDB check for a single title immediately, rather
    than waiting for the next scheduled daily run."""
    row = db.get_tracker_by_id(tracker_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    if row["media_type"] == "tv":
        check_tv_show(db, tmdb, row)
    else:
        check_movie_collection(db, tmdb, row)
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.get("/status", response_model=TrackerStatusResponse)
def tracker_status(db: Database = Depends(get_database)) -> TrackerStatusResponse:
    tracked = db.list_tracked()
    pending = db.list_pending_notifications()
    last_checked = max((r["last_checked"] for r in tracked if r["last_checked"]), default=None)
    return TrackerStatusResponse(
        total_tracked=len(tracked),
        pending_notifications=len(pending),
        last_checked=last_checked,
    )
