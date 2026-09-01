"""Library-wide maintenance: export/import, health checks (orphans,
duplicates, orphaned artwork), retrying failed TMDB matches, and bulk
metadata refresh. Split out of library.py (see that module's own
docstring) -- these routes act across the whole library rather than one
item, unlike the gallery/detail routes that stayed there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from app.api.routes.library_common import _metadata_dict, _to_out
from app.config_loader import AppConfig
from app.core.orphan_artwork import cleanup_orphaned_artwork, find_orphaned_artwork
from app.core.tmdb_client import MediaResult, TMDBClient, genres_for, vote_average_for
from app.database import Database
from app.dependencies import get_config, get_database, get_tmdb_client
from app.models import (
    LibraryExportResponse,
    LibraryHealthOut,
    LibraryImportRequest,
    LibraryImportResponse,
    MediaItemExportOut,
    MediaType,
    OrphanArtworkCleanupResponse,
    OrphanArtworkGroupOut,
    OrphanCleanupResponse,
    RefreshMetadataRequest,
    RefreshMetadataResponse,
    RetryFailedMatchesResponse,
)

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/export", response_model=LibraryExportResponse)
def export_library(db: Database = Depends(get_database)) -> LibraryExportResponse:
    """Dumps every media_items row as portable JSON -- a backup/restore path
    that doesn't depend on copying the SQLite file directly (e.g. moving to
    a fresh install, or archiving a snapshot alongside the media itself).
    `id` is deliberately omitted: importing into a different (or rebuilt)
    database shouldn't assume the old row ids still mean anything there.
    """
    items = [
        MediaItemExportOut(
            original_path=row["original_path"],
            title=row["title"],
            year=row["year"],
            tmdb_id=row["tmdb_id"],
            media_type=row["media_type"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            final_path=row["final_path"],
            archived_at=row["archived_at"],
            watched=bool(row["watched"]),
            metadata=_metadata_dict(row),
            imdb_id=row["imdb_id"],
            manual_override=bool(row["manual_override"]),
        )
        for row in db.list_media_items()
    ]
    return LibraryExportResponse(items=items, exported_at=datetime.now(timezone.utc).isoformat())


@router.post("/import", response_model=LibraryImportResponse)
def import_library(payload: LibraryImportRequest, db: Database = Depends(get_database)) -> LibraryImportResponse:
    """Restores media_items rows from a previous /export. An item is skipped
    (not overwritten) when its final_path is already tracked -- the same
    dedupe rule adopt_new_files uses -- so re-importing the same backup
    twice, or importing into a library that's since moved on, doesn't
    clobber anything or create duplicates.
    """
    imported = 0
    skipped = 0
    for item in payload.items:
        if item.final_path and db.get_media_item_by_final_path(item.final_path) is not None:
            skipped += 1
            continue
        db.create_media_item(
            original_path=item.original_path,
            title=item.title,
            year=item.year,
            tmdb_id=item.tmdb_id,
            media_type=item.media_type,
            season_number=item.season_number,
            episode_number=item.episode_number,
            final_path=item.final_path,
            archived_at=item.archived_at,
            watched=1 if item.watched else 0,
            metadata=item.metadata,
            imdb_id=item.imdb_id,
            manual_override=1 if item.manual_override else 0,
        )
        imported += 1
    return LibraryImportResponse(imported=imported, skipped=skipped)


@router.get("/health", response_model=LibraryHealthOut)
def library_health(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> LibraryHealthOut:
    """Three library-wide sanity checks the gallery views don't otherwise
    surface: orphans (a media_items row whose final_path no longer exists on
    disk -- moved or deleted outside this app), duplicates (more than one
    row pointing at the same tmdb_id/season/episode, e.g. from organizing the
    same episode from two different source files), and orphaned artwork
    (poster.jpg/*.nfo/subtitle files left in a folder whose video has since
    been renamed or moved away -- see app/core/orphan_artwork.py). Duplicates
    and orphaned artwork are reported only, never auto-resolved -- picking
    which copy to keep, or confirming a stray file really is stray, is a
    judgment call for the user.
    """
    items = db.list_media_items()

    orphans = [
        _to_out(row) for row in items if row["final_path"] and not Path(row["final_path"]).exists()
    ]

    groups: dict[tuple, list[dict]] = {}
    for row in items:
        if row["tmdb_id"] is None:
            continue
        key = (row["tmdb_id"], row["media_type"], row["season_number"], row["episode_number"])
        groups.setdefault(key, []).append(row)
    duplicates = [[_to_out(r) for r in rows] for rows in groups.values() if len(rows) > 1]

    artwork_groups = find_orphaned_artwork(config.paths.archive_movies) + find_orphaned_artwork(config.paths.archive_tv)
    orphaned_artwork = [
        OrphanArtworkGroupOut(folder=str(g.folder), files=[f.name for f in g.files]) for g in artwork_groups
    ]

    return LibraryHealthOut(orphans=orphans, duplicates=duplicates, orphaned_artwork=orphaned_artwork)


@router.post("/orphans/cleanup", response_model=OrphanCleanupResponse)
def cleanup_orphans(dry_run: bool = False, db: Database = Depends(get_database)) -> OrphanCleanupResponse:
    """Removes media_items rows whose final_path no longer exists on disk.
    There's no file to delete (it's already gone) -- just the stale DB row --
    so this logs a 'delete' operation for the audit trail without touching
    the filesystem, same operation_type the single-file delete-file uses.
    With dry_run, nothing is logged or removed -- just reports which rows
    would go.
    """
    removed = 0
    paths: list[str] = []
    for row in db.list_media_items():
        if row["final_path"] and not Path(row["final_path"]).exists():
            paths.append(row["final_path"])
            if not dry_run:
                db.log_operation(
                    operation_type="delete",
                    status="success",
                    media_id=row["id"],
                    details={"reason": "orphan_cleanup", "final_path": row["final_path"]},
                )
                db.delete_media_item(row["id"])
            removed += 1
    return OrphanCleanupResponse(removed=removed, dry_run=dry_run, paths=paths)


@router.post("/orphaned-artwork/cleanup", response_model=OrphanArtworkCleanupResponse)
def cleanup_orphaned_artwork_route(
    dry_run: bool = False, config: AppConfig = Depends(get_config)
) -> OrphanArtworkCleanupResponse:
    """Deletes poster/nfo/subtitle files found by find_orphaned_artwork() --
    a fresh re-scan at cleanup time (not whatever /health last returned),
    so a file that got a video back in the meantime isn't touched. With
    dry_run, nothing is deleted -- the groups that would be cleaned up are
    returned instead, same shape as /health's orphaned_artwork.
    """
    groups = find_orphaned_artwork(config.paths.archive_movies) + find_orphaned_artwork(config.paths.archive_tv)
    if dry_run:
        total_files = sum(len(g.files) for g in groups)
        group_out = [OrphanArtworkGroupOut(folder=str(g.folder), files=[f.name for f in g.files]) for g in groups]
        return OrphanArtworkCleanupResponse(removed=total_files, dry_run=True, groups=group_out)
    removed = cleanup_orphaned_artwork(groups)
    return OrphanArtworkCleanupResponse(removed=removed)


@router.post("/retry-failed-matches", response_model=RetryFailedMatchesResponse)
def retry_failed_matches(media_type: MediaType | None = None, db: Database = Depends(get_database)) -> RetryFailedMatchesResponse:
    """Clears the retry cooldown on every previously-failed match so the
    background backfill re-searches them on its very next cycle, instead of
    waiting out list_unmatched_media_items' cooldown -- for after fixing a
    file name or when a title just wasn't on TMDB yet at the time.
    """
    return RetryFailedMatchesResponse(reset=db.reset_failed_match_attempts(media_type))


@router.post("/refresh-metadata", response_model=RefreshMetadataResponse)
def refresh_metadata(
    payload: RefreshMetadataRequest,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RefreshMetadataResponse:
    """Bulk "Refresh Metadata" for the gallery multi-select: re-fetches
    title/poster/overview/rating/genres from TMDB for each selected,
    already-matched item -- the tmdb_id itself never changes, unlike
    rematch-imdb/rematch-tmdb (which point an item at a *different* TMDB
    entry) or retry-failed-matches (which is for items with no tmdb_id at
    all yet). Uses TMDBClient.refresh_*_details, which bypasses that
    client's own cache -- otherwise a "refresh" during the same process
    lifetime would just hand back the same stale result it already had.

    Multiple selected rows sharing one tmdb_id (every episode of a TV show)
    only trigger one TMDB lookup, not one per row. ffprobe-derived fields
    (resolution/codec/HDR/audio) and episode_title are carried forward from
    the existing row -- refreshing metadata shouldn't need to re-probe the
    file or lose per-episode data TMDB doesn't have anyway.
    """
    now = datetime.now(timezone.utc).isoformat()
    media_cache: dict[tuple[int, str], MediaResult | None] = {}
    updated = 0
    failed = 0

    for item_id in payload.ids:
        row = db.get_media_item(item_id)
        if row is None or row["tmdb_id"] is None:
            failed += 1
            continue

        cache_key = (row["tmdb_id"], row["media_type"])
        if cache_key not in media_cache:
            media_cache[cache_key] = (
                tmdb.refresh_tv_details(row["tmdb_id"])
                if row["media_type"] == "tv"
                else tmdb.refresh_movie_details(row["tmdb_id"])
            )
        media = media_cache[cache_key]
        if media is None:
            failed += 1
            continue

        existing_meta = _metadata_dict(row)
        db.update_media_item(
            item_id,
            title=media.title,
            year=media.year,
            metadata={
                "width": existing_meta.get("width"),
                "height": existing_meta.get("height"),
                "video_codec": existing_meta.get("video_codec"),
                "hdr": existing_meta.get("hdr"),
                "audio_channels": existing_meta.get("audio_channels"),
                "episode_title": existing_meta.get("episode_title"),
                "poster_path": media.poster_path,
                "overview": media.overview,
                "vote_average": vote_average_for(media),
                "genres": genres_for(media),
            },
            match_attempted_at=now,
        )
        updated += 1

    return RefreshMetadataResponse(updated=updated, failed=failed)
