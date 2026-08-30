from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.database import Database
from app.dependencies import get_database
from app.models import (
    TrackerAcknowledgeRequest,
    TrackerAddRequest,
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
