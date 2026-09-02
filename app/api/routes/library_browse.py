"""Raw-filesystem library browsing, manual organize-in-place, and file
deletion. Split out of library.py (see that module's own docstring) --
unlike the gallery/detail routes (DB-only) or /api/scan (new files only),
these act directly on whatever's physically present under the configured
archive roots, tracked in media_items or not.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.archive import _dry_run_result
from app.api.routes.library_common import _metadata_dict
from app.config_loader import AppConfig
from app.core.orphan_artwork import ARTWORK_NAMES
from app.core.organizer import OrganizeError, organize_file
from app.core.renamer import RenamePlan
from app.core.scanner import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, scan_directory
from app.core.media_server import notify_media_servers
from app.core.tracker import maybe_auto_track
from app.database import Database
from app.dependencies import get_config, get_database
from app.models import (
    ArchiveConfirmRequest,
    ArchiveConfirmResponse,
    ArchiveConfirmResult,
    BrowseItemOut,
    BrowseResponse,
    DeleteBatchRequest,
    DeleteBatchResponse,
    DeleteFileRequest,
    DeletePreviewOut,
    MediaType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])


@router.post("/organize", response_model=ArchiveConfirmResponse)
def organize_selected(
    payload: ArchiveConfirmRequest, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)
) -> ArchiveConfirmResponse:
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

        if payload.dry_run:
            results.append(_dry_run_result(source, dest))
            continue

        plan = RenamePlan(
            source_path=source,
            dest_path=dest,
            media_type=item.media_type,
            tmdb_id=item.tmdb_id,
            title=item.title,
            year=item.year,
            season=item.season,
            episode=item.episode,
            episode_title=item.episode_title,
            air_date=item.air_date,
            poster_path=item.poster_path,
            overview=item.overview,
            vote_average=item.vote_average,
            genres=item.genres,
        )
        try:
            media_id = organize_file(db, plan, write_nfo_files=config.media_server.write_nfo_files)
            maybe_auto_track(
                db, config.tracker.auto_track_new, item.tmdb_id, item.media_type, item.title, item.season
            )
            results.append(
                ArchiveConfirmResult(source_path=str(source), dest_path=str(dest), media_id=media_id, status="success")
            )
        except OrganizeError as exc:
            results.append(ArchiveConfirmResult(source_path=str(source), status="failed", error=str(exc)))

    if not payload.dry_run and any(r.status == "success" for r in results):
        notify_media_servers(config)

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
                tmdb_id=tracked_row["tmdb_id"] if tracked_row else None,
                watched=bool(tracked_row["watched"]) if tracked_row else False,
            )
        )
    return BrowseResponse(directory=str(root), items=items)


def _resolve_and_validate_target(path: str, config: AppConfig) -> Path:
    """Only allowed inside a configured incoming/archive root -- a guard
    against a client bug or bad request touching anything outside the
    library, since delete is the one genuinely destructive action here."""
    target = Path(path).resolve()
    allowed_roots = [
        config.paths.incoming_movies,
        config.paths.incoming_tv,
        config.paths.archive_movies,
        config.paths.archive_tv,
    ]
    if not any(target == root.resolve() or root.resolve() in target.parents for root in allowed_roots):
        raise ValueError("Path is outside the configured media directories")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("File not found")
    return target


def _delete_target(target: Path, db: Database, config: AppConfig) -> None:
    # Log while media_id still references a real row -- operation_log.media_id
    # has a foreign key, and deleting the media_items row first would make a
    # log entry pointing at it fail that constraint.
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
        raise

    _cleanup_siblings_and_folder(target, config)

    db.log_operation(
        operation_type="delete",
        status="success",
        media_id=tracked_row["id"] if tracked_row else None,
        details={"path": str(target)},
    )
    if tracked_row:
        db.delete_media_item(tracked_row["id"])
        _maybe_move_to_watched_history(db, tracked_row)
    logger.info("Deleted %s (was tracked: %s)", target, bool(tracked_row))


def _maybe_move_to_watched_history(db: Database, deleted_row: dict) -> None:
    """After a tracked media_items row is deleted, files a "Watched /
    History" entry in the tracker for it -- a movie is always the whole
    title, so one delete is always the end of it; a TV episode only counts
    once no other episodes of that show remain on disk, so deleting one
    episode out of a still-owned season doesn't wrongly mark the whole show
    watched. Reuses upsert_tracker's partial-merge semantics (see
    Database.upsert_tracker), so a title already being tracked for
    sequels/seasons keeps that state -- only its category (and poster/
    overview) change.
    """
    tmdb_id = deleted_row["tmdb_id"]
    if tmdb_id is None:
        return
    media_type = deleted_row["media_type"]
    if media_type == "tv" and db.count_media_items_for_tmdb(tmdb_id, "tv") > 0:
        return
    meta = _metadata_dict(deleted_row)
    fields: dict = {"category": "watched"}
    if meta.get("poster_path"):
        fields["poster_path"] = meta["poster_path"]
    if meta.get("overview"):
        fields["overview"] = meta["overview"]
    db.upsert_tracker(tmdb_id=tmdb_id, media_type=media_type, title=deleted_row["title"], **fields)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tbn"}


def _protected_roots(config: AppConfig) -> set[Path]:
    return {
        config.paths.incoming_movies.resolve(),
        config.paths.incoming_tv.resolve(),
        config.paths.archive_movies.resolve(),
        config.paths.archive_tv.resolve(),
    }


def _sidecar_candidates(deleted_video: Path, config: AppConfig) -> tuple[list[Path], bool]:
    """What _cleanup_siblings_and_folder would remove for `deleted_video`:
    (sidecar files to remove, whether the folder itself would end up
    removable). Pure/side-effect-free so both the real cleanup and the
    delete dry-run preview compute the exact same answer from one place.

    Works whether `deleted_video` still physically exists in the folder
    (dry run -- nothing has been deleted yet) or not (real cleanup runs
    this after the video's already unlinked): entries are always compared
    by path against `deleted_video` rather than relying on its absence.

    While another video remains in the folder (a TV season with more
    episodes), the answer is scoped to this video's own stem-prefixed
    subtitle siblings only, so a still-needed sibling of another episode
    is never touched. Once nothing else in the folder needs a video file,
    any leftover subtitle/image/nfo file can only have belonged to what's
    being deleted -- including ones this app didn't write itself (an
    adopted Radarr/Sonarr import's own artwork/nfo naming, e.g.
    "Movie (2020).nfo" or "banner.jpg", not just this app's own
    poster.jpg/fanart.jpg/movie.nfo) -- so those are swept by extension
    instead of by the fixed ARTWORK_NAMES list. Genuinely unrelated files
    (anything not a subtitle/image/nfo extension) are left alone, and so
    is the folder if any remain -- and the folder is never removable if
    it's one of the configured incoming/archive roots themselves.
    """
    folder = deleted_video.parent
    if not folder.is_dir():
        return [], False

    is_protected_root = folder.resolve() in _protected_roots(config)
    other_entries = [p for p in folder.iterdir() if p.is_file() and p != deleted_video]

    stem_subtitle_siblings = [
        p for p in folder.glob(f"{deleted_video.stem}*")
        if p.is_file() and p != deleted_video and p.suffix.lower() in SUBTITLE_EXTENSIONS
    ]
    remaining_videos = any(p.suffix.lower() in VIDEO_EXTENSIONS for p in other_entries)
    if remaining_videos or is_protected_root:
        return stem_subtitle_siblings, False

    sidecar_exts = SUBTITLE_EXTENSIONS | IMAGE_EXTENSIONS | {".nfo"}
    all_sidecars = [p for p in other_entries if p.name.lower() in ARTWORK_NAMES or p.suffix.lower() in sidecar_exts]
    folder_removable = len(all_sidecars) == len(other_entries)  # nothing left over but sidecars
    return all_sidecars, folder_removable


def _cleanup_siblings_and_folder(deleted_video: Path, config: AppConfig) -> None:
    """After deleting a video, also removes the sidecar files
    `_sidecar_candidates()` identifies and, if that leaves the folder with
    nothing else in it, the folder itself. Best-effort throughout: a
    failure here doesn't undo the video deletion that already succeeded.
    """
    sidecars, folder_removable = _sidecar_candidates(deleted_video, config)
    for sidecar in sidecars:
        try:
            sidecar.unlink()
        except OSError as exc:
            logger.warning("Failed to remove leftover sidecar file %s: %s", sidecar, exc)
    if folder_removable:
        try:
            deleted_video.parent.rmdir()
        except OSError:
            pass  # not empty (something else still in there), or already gone


def _preview_delete(target: Path, config: AppConfig) -> DeletePreviewOut:
    """What deleting `target` would do, without touching the filesystem."""
    sidecars, folder_removable = _sidecar_candidates(target, config)
    return DeletePreviewOut(
        path=str(target),
        would_delete=True,
        sibling_files=[str(p) for p in sidecars],
        folder_removed=str(target.parent) if folder_removable else None,
    )


@router.post("/delete-file")
def delete_file(
    payload: DeleteFileRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> dict:
    """Permanently deletes a single file from disk, or -- with dry_run --
    just reports what that would do (the file itself, plus which sidecar
    files and the folder it would take with it) without touching anything.
    """
    try:
        target = _resolve_and_validate_target(payload.path, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.dry_run:
        return {"deleted": False, "dry_run": True, "preview": _preview_delete(target, config)}

    try:
        _delete_target(target, db, config)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {exc}") from exc
    return {"deleted": True}


@router.post("/delete-batch", response_model=DeleteBatchResponse)
def delete_batch(
    payload: DeleteBatchRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> DeleteBatchResponse:
    """Bulk version of delete-file for the gallery/Browse "Delete Selected"
    action -- one bad path in the batch (outside the media dirs, already
    gone, a permissions error) is reported per-path rather than aborting
    the rest of the selection. With dry_run, nothing is deleted -- each
    path gets a DeletePreviewOut instead, in `previews`.
    """
    if payload.dry_run:
        previews: list[DeletePreviewOut] = []
        errors: list[str] = []
        for path in payload.paths:
            try:
                target = _resolve_and_validate_target(path, config)
                previews.append(_preview_delete(target, config))
            except (ValueError, FileNotFoundError) as exc:
                errors.append(f"{path}: {exc}")
                previews.append(DeletePreviewOut(path=path, would_delete=False, error=str(exc)))
        return DeleteBatchResponse(deleted=0, errors=errors, previews=previews)

    deleted = 0
    errors = []
    for path in payload.paths:
        try:
            target = _resolve_and_validate_target(path, config)
            _delete_target(target, db, config)
            deleted += 1
        except (ValueError, FileNotFoundError, OSError) as exc:
            errors.append(f"{path}: {exc}")
    return DeleteBatchResponse(deleted=deleted, errors=errors)
