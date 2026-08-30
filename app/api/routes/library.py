"""Read/browse the archived library and toggle manual watch state.

Distinct from /api/scan (which finds new, unarchived files): this reflects
whatever this app has itself archived into media_items, for the gallery
views and manual watch tracking that are Media Manager's own job alongside
Radarr/Sonarr's automated import pipeline.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config_loader import AppConfig
from app.core.library_adopt import adopt_new_files
from app.core.organizer import OrganizeError, organize_file
from app.core.renamer import RenamePlan
from app.core.scanner import scan_directory
from app.database import Database
from app.dependencies import get_config, get_database
from app.models import (
    ArchiveConfirmRequest,
    ArchiveConfirmResponse,
    ArchiveConfirmResult,
    BrowseItemOut,
    BrowseResponse,
    DeleteFileRequest,
    LibraryItemOut,
    LibraryResponse,
    MediaType,
    MetadataStatusResponse,
    WatchedBatchRequest,
    WatchedBatchResponse,
    WatchedUpdateRequest,
)

logger = logging.getLogger(__name__)

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
def list_movies(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> LibraryResponse:
    """Auto-adopts (registers, no file operations, no network calls) any
    file physically in archive_movies not yet tracked, then returns
    everything tracked -- so files already organized by Radarr/Sonarr show
    up here without needing to be manually run through Ready to Archive.
    Newly-adopted rows show with a placeholder poster until the background
    metadata backfill (see /metadata-status) fills them in.
    """
    adopt_new_files(db, config, "movie")
    return LibraryResponse(items=[_to_out(r) for r in db.list_media_items(media_type="movie")])


@router.get("/tv", response_model=LibraryResponse)
def list_tv(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> LibraryResponse:
    adopt_new_files(db, config, "tv")
    return LibraryResponse(items=[_to_out(r) for r in db.list_media_items(media_type="tv")])


@router.get("/metadata-status", response_model=MetadataStatusResponse)
def metadata_status(media_type: MediaType | None = None, db: Database = Depends(get_database)) -> MetadataStatusResponse:
    """Pending count for the background TMDB metadata backfill, so the
    dashboard can show progress and know when to stop polling for updates --
    plus a separate failed count (searched at least once, no match found),
    so a permanently-unmatched title doesn't look like it's "still loading".
    """
    return MetadataStatusResponse(
        pending=db.count_unmatched_media_items(media_type),
        failed=db.count_failed_match_items(media_type),
    )


@router.post("/{item_id}/watched", response_model=LibraryItemOut)
def set_watched(item_id: int, payload: WatchedUpdateRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    db.update_media_item(item_id, watched=1 if payload.watched else 0)
    return _to_out(db.get_media_item(item_id))


@router.post("/watched-batch", response_model=WatchedBatchResponse)
def set_watched_batch(payload: WatchedBatchRequest, db: Database = Depends(get_database)) -> WatchedBatchResponse:
    updated = 0
    for item_id in payload.ids:
        if db.get_media_item(item_id) is None:
            continue
        db.update_media_item(item_id, watched=1 if payload.watched else 0)
        updated += 1
    return WatchedBatchResponse(updated=updated)


@router.post("/organize", response_model=ArchiveConfirmResponse)
def organize_selected(payload: ArchiveConfirmRequest, db: Database = Depends(get_database)) -> ArchiveConfirmResponse:
    """Stage 2 of the manual library browser: given TMDB-matched preview
    items (from the same /api/archive/preview used by "Ready to Archive"),
    moves each file to its computed destination in place and updates its
    existing media_items row -- unlike /api/archive/confirm, which always
    copies to a new location and always inserts a new row. Reuses the
    archive preview/confirm request-response shapes since the data is
    identical; only what happens to the filesystem and the DB differs.
    """
    results: list[ArchiveConfirmResult] = []
    for item in payload.items:
        source = Path(item.source_path)
        dest = Path(item.dest_path)
        plan = RenamePlan(
            source_path=source,
            dest_path=dest,
            media_type=item.media_type,
            tmdb_id=item.tmdb_id,
            title=item.title,
            year=item.year,
            season=item.season,
            episode=item.episode,
            poster_path=item.poster_path,
            overview=item.overview,
        )
        try:
            media_id = organize_file(db, plan)
            results.append(
                ArchiveConfirmResult(source_path=str(source), dest_path=str(dest), media_id=media_id, status="success")
            )
        except OrganizeError as exc:
            results.append(ArchiveConfirmResult(source_path=str(source), status="failed", error=str(exc)))
    return ArchiveConfirmResponse(results=results)


@router.get("/browse", response_model=BrowseResponse)
def browse_archive(
    media_type: MediaType,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BrowseResponse:
    """Lists every video file physically present in the archive root for
    `media_type`, tracked in media_items or not -- unlike /api/scan (which
    only surfaces new, untracked files) and /api/library/movies|tv (which
    only reflects what's in the database), this is a raw filesystem view
    meant for manual cleanup of a library that already existed before (or
    outside) this app, e.g. Radarr/Sonarr-managed files never archived
    through here.
    """
    root = config.paths.archive_movies if media_type == "movie" else config.paths.archive_tv
    scanned = scan_directory(root)

    items = []
    for f in scanned:
        tracked_row = db.get_media_item_by_final_path(str(f.path))
        items.append(
            BrowseItemOut(
                path=str(f.path),
                size_bytes=f.size_bytes,
                parsed_title=f.parsed.title,
                year=f.parsed.year,
                season=f.parsed.season,
                episode=f.parsed.episode,
                tracked=tracked_row is not None,
                media_id=tracked_row["id"] if tracked_row else None,
                watched=bool(tracked_row["watched"]) if tracked_row else False,
            )
        )
    return BrowseResponse(directory=str(root), items=items)


@router.post("/delete-file")
def delete_file(
    payload: DeleteFileRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> dict:
    """Permanently deletes a single file from disk. Only allowed inside a
    configured incoming/archive root -- a guard against a client bug or bad
    request touching anything outside the library, since this is the one
    genuinely destructive endpoint in the app.
    """
    target = Path(payload.path).resolve()
    allowed_roots = [
        config.paths.incoming_movies,
        config.paths.incoming_tv,
        config.paths.archive_movies,
        config.paths.archive_tv,
    ]
    if not any(target == root.resolve() or root.resolve() in target.parents for root in allowed_roots):
        raise HTTPException(status_code=400, detail="Path is outside the configured media directories")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    tracked_row = db.get_media_item_by_final_path(str(target))
    try:
        target.unlink()
    except OSError as exc:
        db.log_operation(
            operation_type="delete",
            status="failed",
            media_id=tracked_row["id"] if tracked_row else None,
            error_message=str(exc),
            details={"path": str(target)},
        )
        raise HTTPException(status_code=500, detail=f"Failed to delete: {exc}") from exc

    # Log while media_id still references a real row -- operation_log.media_id
    # has a foreign key, and deleting the media_items row first would make a
    # log entry pointing at it fail that constraint.
    db.log_operation(
        operation_type="delete",
        status="success",
        media_id=tracked_row["id"] if tracked_row else None,
        details={"path": str(target)},
    )
    if tracked_row:
        db.delete_media_item(tracked_row["id"])
    logger.info("Deleted %s (was tracked: %s)", target, bool(tracked_row))
    return {"deleted": True}
