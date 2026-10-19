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

import glob
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.archiver import sha256
from app.core.renamer import RenamePlan, write_nfo
from app.core.subtitle_purger import SUBTITLE_EXTENSIONS
from app.core.tmdb_client import MediaResult
from app.database import Database

logger = logging.getLogger(__name__)


class OrganizeError(Exception):
    pass


@dataclass
class OrganizeScope:
    """Which parts of a RenamePlan to actually apply. All three default on,
    which is the original "do everything" organize behaviour.

    - file: rename the video itself to the plan's file name
    - folder: rename/move the folder(s) it lives in to the plan's folder
      names (a movie's folder; a show's folder and its Season folder)
    - sidecars: rename the subtitles/NFO/artwork named after the video along
      with it, and refresh the NFO
    A folder rename carries everything inside it along regardless of
    `sidecars` -- that's what renaming a folder means.
    """

    file: bool = True
    folder: bool = True
    sidecars: bool = True


_SIDECAR_EXTENSIONS = SUBTITLE_EXTENSIONS | {".nfo", ".jpg", ".jpeg", ".png", ".webp", ".tbn"}


def effective_dest(source: Path, dest: Path, scope: OrganizeScope) -> Path:
    """The path `source` ends up at once only the parts in `scope` are
    applied -- a folder-only rename keeps the video's current file name,
    a file-only rename keeps its current folder."""
    folder = dest.parent if scope.folder else source.parent
    name = dest.name if scope.file else source.name
    return folder / name


def organize_file(
    db: Database,
    plan: RenamePlan,
    write_nfo_files: bool = True,
    scope: OrganizeScope | None = None,
    protected_roots: frozenset[Path] | set[Path] = frozenset(),
) -> int:
    """Applies `scope`'s share of plan.source_path -> plan.dest_path, and
    updates the media_items row tracking that file by its previous
    final_path in place, or creates one if it wasn't tracked yet. Returns
    the media_item id. A no-op move (already at the destination) is just a
    metadata refresh.

    Folders are renamed in place (an atomic directory rename that also
    updates every other tracked row inside it) when the old and new folder
    share a parent; otherwise the file is moved into the new folder. Files
    are renamed in place when possible and fall back to copy-verify-delete
    (same sha256 check as archiver.archive_file(), not shutil.move: a bare
    move can partially succeed on Windows if something locks the source just
    long enough for the fallback copy to finish but the unlink to fail,
    leaving the file in both places instead of moved).
    `protected_roots` (the configured media roots) are never renamed.
    """
    scope = scope or OrganizeScope()
    existing = db.get_media_item_by_final_path(str(plan.source_path))
    final = effective_dest(plan.source_path, plan.dest_path, scope)
    current = plan.source_path

    try:
        if scope.folder and current != final:
            current = _rename_folders_in_place(db, current, final, 2 if plan.media_type == "tv" else 1, protected_roots)
        if current != final:
            old_dir = current.parent
            _move_file(current, final)
            if scope.sidecars:
                _move_sidecars(current, final)
            _remove_dir_if_empty(old_dir, protected_roots)
    except OSError as exc:
        db.log_operation(
            operation_type="rename",
            status="failed",
            media_id=existing["id"] if existing else None,
            error_message=str(exc),
            details={"from": str(plan.source_path), "to": str(final)},
        )
        raise OrganizeError(f"Failed to move {plan.source_path} -> {final}: {exc}") from exc

    if write_nfo_files and scope.sidecars:
        _write_nfo_best_effort(plan, final)

    fields = dict(
        title=plan.title,
        year=plan.year,
        media_type=plan.media_type,
        tmdb_id=plan.tmdb_id,
        final_path=str(final),
        season_number=plan.season,
        episode_number=plan.episode,
        metadata={
            "poster_path": plan.poster_path,
            "overview": plan.overview,
            "episode_title": plan.episode_title,
            "air_date": plan.air_date,
            "vote_average": plan.vote_average,
            "genres": plan.genres,
        },
    )

    # Re-lookup: a folder rename above already rebased the row's final_path.
    existing = db.get_media_item_by_final_path(str(final)) or existing
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
        details={
            "from": str(plan.source_path),
            "to": str(final),
            "scope": {"file": scope.file, "folder": scope.folder, "sidecars": scope.sidecars},
        },
    )
    logger.info("Organized %s -> %s", plan.source_path, final)
    return media_id


def _rename_folders_in_place(db: Database, current: Path, final: Path, levels: int, protected_roots) -> Path:
    """Renames up to `levels` ancestor folders of `current` (outermost
    first) to the matching ancestor names of `final`, but only where the
    old and new folder are siblings -- a real rename, not a move. Returns
    where the video sits afterwards (unchanged if nothing qualified)."""
    protected = {p.resolve() for p in protected_roots}
    for level in range(levels, 0, -1):
        if len(current.parents) < level or len(final.parents) < level:
            continue
        old_dir, new_dir = current.parents[level - 1], final.parents[level - 1]
        if old_dir == new_dir or old_dir.parent != new_dir.parent or old_dir.resolve() in protected:
            continue
        if new_dir.exists():  # merging into an existing folder is a file move, not a rename
            continue
        old_dir.rename(new_dir)
        db.rebase_final_paths(str(old_dir), str(new_dir))
        current = new_dir / current.relative_to(old_dir)
    return current


def _move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not dest.exists():
            src.rename(dest)  # same volume: instant, no copy of a multi-GB file
            return
    except OSError:
        pass  # cross-volume or locked -- fall through to copy-verify-delete
    try:
        shutil.copy2(src, dest)
        source_hash, dest_hash = sha256(src), sha256(dest)
        if source_hash != dest_hash:
            raise OSError(f"Checksum mismatch after copy (source={source_hash}, dest={dest_hash})")
        src.unlink()
    except OSError:
        dest.unlink(missing_ok=True)  # never leave a duplicate behind on a failed move
        raise


def _move_sidecars(source: Path, dest: Path) -> None:
    """Moves files named after `source`'s base name (subtitles, NFO, artwork
    like "Movie-poster.jpg") alongside it, renamed to match `dest`'s base
    name while preserving the tail (e.g. ".en.srt") -- a sidecar left under
    the video's old name doesn't get picked up by Plex/Jellyfin. The tail
    must start with a separator so "Movie" doesn't also claim "Movie 2.srt".
    """
    for sub in source.parent.glob(f"{glob.escape(source.stem)}*"):
        tail = sub.name[len(source.stem):]
        if sub.is_file() and sub.suffix.lower() in _SIDECAR_EXTENSIONS and tail[:1] in (".", "-", "_"):
            shutil.move(str(sub), str(dest.parent / f"{dest.stem}{tail}"))


def _remove_dir_if_empty(folder: Path, protected_roots) -> None:
    if folder.resolve() in {p.resolve() for p in protected_roots}:
        return
    try:
        folder.rmdir()
    except OSError:
        pass  # not empty, or already gone


def _write_nfo_best_effort(plan: RenamePlan, final: Path) -> None:
    try:
        write_nfo(
            final.parent,
            MediaResult(
                tmdb_id=plan.tmdb_id,
                title=plan.title,
                media_type=plan.media_type,
                year=plan.year,
                overview=plan.overview,
            ),
        )
    except OSError as exc:
        logger.warning("NFO write failed for %s: %s", final, exc)
