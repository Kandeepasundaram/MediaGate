from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.tmdb_client import TMDBClient
from app.database import Database
from app.dependencies import get_database, get_tmdb_client
from app.models import (
    UniverseCreateRequest,
    UniverseDetailOut,
    UniverseMemberAddRequest,
    UniverseMemberOut,
    UniverseOut,
    UniverseSuggestionOut,
    UniverseSuggestionsResponse,
    UniversesListResponse,
)

router = APIRouter(prefix="/api/universes", tags=["universes"])


def _universe_out(row: dict, member_count: int = 0, pending_count: int = 0) -> UniverseOut:
    return UniverseOut(
        id=row["id"],
        name=row["name"],
        media_type=row["media_type"],
        created_at=row["created_at"],
        member_count=member_count,
        pending_count=pending_count,
    )


def _member_out(member: dict, tracker_row: dict | None) -> UniverseMemberOut:
    return UniverseMemberOut(
        id=member["id"],
        tmdb_id=member["tmdb_id"],
        title=member["title"],
        poster_path=member["poster_path"],
        tracker_id=tracker_row["id"] if tracker_row else None,
        pending_notification=bool(tracker_row["pending_notification"]) if tracker_row else False,
        muted=bool(tracker_row["muted"]) if tracker_row else False,
        latest_known_season=tracker_row["latest_known_season"] if tracker_row else None,
        movie_release_status=tracker_row["movie_release_status"] if tracker_row else None,
        last_checked=tracker_row["last_checked"] if tracker_row else None,
    )


@router.post("", response_model=UniverseOut)
def create_universe(payload: UniverseCreateRequest, db: Database = Depends(get_database)) -> UniverseOut:
    universe_id = db.create_universe(name=payload.name, media_type=payload.media_type)
    return _universe_out(db.get_universe(universe_id))


@router.get("", response_model=UniversesListResponse)
def list_universes(media_type: str | None = None, db: Database = Depends(get_database)) -> UniversesListResponse:
    out = []
    for row in db.list_universes(media_type):
        members = db.list_universe_members(row["id"])
        pending = sum(
            1
            for m in members
            if (tr := db.get_tracker(m["tmdb_id"], row["media_type"]))
            and tr["pending_notification"]
            and not tr["muted"]
        )
        out.append(_universe_out(row, member_count=len(members), pending_count=pending))
    return UniversesListResponse(universes=out)


@router.get("/{universe_id}", response_model=UniverseDetailOut)
def get_universe_detail(universe_id: int, db: Database = Depends(get_database)) -> UniverseDetailOut:
    universe = db.get_universe(universe_id)
    if universe is None:
        raise HTTPException(status_code=404, detail="Universe not found")
    members = db.list_universe_members(universe_id)
    member_outs = [
        _member_out(m, db.get_tracker(m["tmdb_id"], universe["media_type"])) for m in members
    ]
    pending = sum(1 for m in member_outs if m.pending_notification and not m.muted)
    return UniverseDetailOut(
        universe=_universe_out(universe, member_count=len(member_outs), pending_count=pending),
        members=member_outs,
    )


@router.delete("/{universe_id}")
def delete_universe(universe_id: int, db: Database = Depends(get_database)) -> dict:
    if db.get_universe(universe_id) is None:
        raise HTTPException(status_code=404, detail="Universe not found")
    db.delete_universe(universe_id)
    return {"deleted": True}


@router.post("/{universe_id}/members", response_model=UniverseDetailOut)
def add_universe_member(
    universe_id: int, payload: UniverseMemberAddRequest, db: Database = Depends(get_database)
) -> UniverseDetailOut:
    universe = db.get_universe(universe_id)
    if universe is None:
        raise HTTPException(status_code=404, detail="Universe not found")
    # Registers the title with the existing notification engine, same call
    # /api/tracker/add makes -- a universe member is always a tracked title.
    db.upsert_tracker(tmdb_id=payload.tmdb_id, media_type=universe["media_type"], title=payload.title)
    db.add_universe_member(universe_id, payload.tmdb_id, payload.title, payload.poster_path)
    return get_universe_detail(universe_id, db)


@router.delete("/{universe_id}/members/{member_id}", response_model=UniverseDetailOut)
def remove_universe_member(universe_id: int, member_id: int, db: Database = Depends(get_database)) -> UniverseDetailOut:
    if db.get_universe(universe_id) is None:
        raise HTTPException(status_code=404, detail="Universe not found")
    db.remove_universe_member(universe_id, member_id)
    return get_universe_detail(universe_id, db)


@router.get("/{universe_id}/suggestions", response_model=UniverseSuggestionsResponse)
def universe_suggestions(
    universe_id: int,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> UniverseSuggestionsResponse:
    """Best-effort "possibly related" titles for this universe -- reliable
    for movies (TMDB Collections), heuristic-only for TV (TMDB's /similar,
    the only signal TMDB exposes; no shared-universe concept for TV exists).
    Excludes anything already a member of *any* universe of this type, not
    just this one, so the same suggestion doesn't show up twice across
    universes."""
    universe = db.get_universe(universe_id)
    if universe is None:
        raise HTTPException(status_code=404, detail="Universe not found")
    if tmdb.mode != "api":
        return UniverseSuggestionsResponse(items=[], tmdb_configured=False)

    members = db.list_universe_members(universe_id)
    exclude_ids = db.list_universe_member_tmdb_ids(universe["media_type"]) | {m["tmdb_id"] for m in members}

    candidates: dict[int, UniverseSuggestionOut] = {}
    if universe["media_type"] == "movie":
        for m in members:
            media = tmdb.get_movie_details(m["tmdb_id"])
            collection_id = (media.raw.get("belongs_to_collection") or {}).get("id") if media and media.raw else None
            if not collection_id:
                continue
            for c in tmdb.get_collection_movies(collection_id):
                if c.tmdb_id is None or c.tmdb_id in exclude_ids or c.tmdb_id in candidates:
                    continue
                candidates[c.tmdb_id] = UniverseSuggestionOut(
                    tmdb_id=c.tmdb_id, title=c.title, year=c.year, poster_path=c.poster_path
                )
    else:
        for m in members:
            for c in tmdb.get_similar_titles(m["tmdb_id"], "tv"):
                if c.tmdb_id is None or c.tmdb_id in exclude_ids or c.tmdb_id in candidates:
                    continue
                candidates[c.tmdb_id] = UniverseSuggestionOut(
                    tmdb_id=c.tmdb_id, title=c.title, year=c.year, poster_path=c.poster_path
                )

    return UniverseSuggestionsResponse(items=list(candidates.values()), tmdb_configured=True)
