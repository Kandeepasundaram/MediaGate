"""Moves an already-tracked-or-untracked file to its TMDB-computed
destination in place, updating (not duplicating) its media_items row.

Distinct from archiver.archive_file(), which always copies to a new
location and always inserts a new row -- that's right for a fresh incoming
download, but wrong for a file already sitting in the archive folder
(auto-adopted or not): copying it would leave the original behind as a
duplicate. This is stage 2 of the manual library browser: organize/rename/
move files selected from Browse & Clean Up.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone

from app.core.archiver import sha256
from app.core.renamer import RenamePlan, write_nfo
from app.core.subtitle_purger import SUBTITLE_EXTENSIONS
from app.core.tmdb_client import MediaResult
from app.database import Database

logger = logging.getLogger(__name__)


class OrganizeError(Exception):
    pass


def organize_file(db: Database, plan: RenamePlan) -> int:
    """Moves plan.source_path to plan.dest_path (a no-op if they're already
    the same path -- just a metadata refresh then), and updates the
    media_items row tracking that file by its previous final_path in place,
    or creates one if it wasn't tracked yet. Returns the media_item id.

    The move itself is copy-verify-delete (same sha256 check as
    archiver.archive_file()), not shutil.move: a bare move can partially
    succeed on Windows if something (Plex, an open handle) locks the source
    just long enough for the rename-fallback copy to finish but the
    subsequent unlink of the source to fail, leaving the file duplicated in
    both the old and new locations instead of moved.
    """
    existing = db.get_media_item_by_final_path(str(plan.source_path))

    if plan.source_path != plan.dest_path:
        plan.dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(plan.source_path, plan.dest_path)
            source_hash = sha256(plan.source_path)
            dest_hash = sha256(plan.dest_path)
            if source_hash != dest_hash:
                plan.dest_path.unlink(missing_ok=True)
                raise OSError(f"Checksum mismatch after copy (source={source_hash}, dest={dest_hash})")
            plan.source_path.unlink()
            _move_sibling_subtitles(plan.source_path, plan.dest_path.parent)
        except OSError as exc:
            db.log_operation(
                operation_type="rename",
                status="failed",
                media_id=existing["id"] if existing else None,
                error_message=str(exc),
                details={"from": str(plan.source_path), "to": str(plan.dest_path)},
            )
            raise OrganizeError(f"Failed to move {plan.source_path} -> {plan.dest_path}: {exc}") from exc

    _write_nfo_best_effort(plan)

    fields = dict(
        title=plan.title,
        year=plan.year,
        media_type=plan.media_type,
        tmdb_id=plan.tmdb_id,
        final_path=str(plan.dest_path),
        season_number=plan.season,
        episode_number=plan.episode,
        metadata={
            "poster_path": plan.poster_path,
            "overview": plan.overview,
            "episode_title": plan.episode_title,
            "vote_average": plan.vote_average,
            "genres": plan.genres,
        },
    )

    if existing:
        db.update_media_item(existing["id"], **fields)
        media_id = existing["id"]
    else:
        media_id = db.create_media_item(
            original_path=str(plan.source_path),
            archived_at=datetime.now(timezone.utc).isoformat(),
            **fields,
        )

    db.log_operation(
        operation_type="rename",
        status="success",
        media_id=media_id,
        details={"from": str(plan.source_path), "to": str(plan.dest_path)},
    )
    logger.info("Organized %s -> %s", plan.source_path, plan.dest_path)
    return media_id


def _move_sibling_subtitles(source, dest_folder) -> None:
    for sub in source.parent.glob(f"{source.stem}*"):
        if sub.suffix.lower() in SUBTITLE_EXTENSIONS:
            shutil.move(str(sub), str(dest_folder / sub.name))


def _write_nfo_best_effort(plan: RenamePlan) -> None:
    try:
        write_nfo(
            plan.dest_path.parent,
            MediaResult(
                tmdb_id=plan.tmdb_id,
                title=plan.title,
                media_type=plan.media_type,
                year=plan.year,
                overview=plan.overview,
            ),
        )
    except OSError as exc:
        logger.warning("NFO write failed for %s: %s", plan.dest_path, exc)
