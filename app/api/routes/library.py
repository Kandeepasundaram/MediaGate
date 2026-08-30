"""Read/browse the archived library and toggle manual watch state.

Distinct from /api/scan (which finds new, unarchived files): this reflects
whatever this app has itself archived into media_items, for the gallery
views and manual watch tracking that are Media Manager's own job alongside
Radarr/Sonarr's automated import pipeline.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.database import Database
from app.dependencies import get_database
from app.models import LibraryItemOut, LibraryResponse, WatchedUpdateRequest

router = APIRouter(prefix="/api/library", tags=["library"])


def _to_out(row: dict) -> LibraryItemOut:
    try:
        meta = json.loads(row["metadata"]) if row.get("metadata") else {}
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return LibraryItemOut(
        id=row["id"],
        title=row["title"],
        media_type=row["media_type"],
        year=row["year"],
        season_number=row["season_number"],
        episode_number=row["episode_number"],
        poster_path=meta.get("poster_path"),
        overview=meta.get("overview", ""),
        watched=bool(row["watched"]),
        final_path=row["final_path"],
        archived_at=row["archived_at"],
    )


@router.get("/movies", response_model=LibraryResponse)
def list_movies(db: Database = Depends(get_database)) -> LibraryResponse:
    return LibraryResponse(items=[_to_out(r) for r in db.list_media_items(media_type="movie")])


@router.get("/tv", response_model=LibraryResponse)
def list_tv(db: Database = Depends(get_database)) -> LibraryResponse:
    return LibraryResponse(items=[_to_out(r) for r in db.list_media_items(media_type="tv")])


@router.post("/{item_id}/watched", response_model=LibraryItemOut)
def set_watched(item_id: int, payload: WatchedUpdateRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    db.update_media_item(item_id, watched=1 if payload.watched else 0)
    return _to_out(db.get_media_item(item_id))
