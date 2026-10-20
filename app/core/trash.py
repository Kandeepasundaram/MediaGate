"""Soft-delete for the Browse & Clean Up tab: a deleted video is moved into
`<library root>/.mediagate-trash/<timestamp>/<path relative to the root>`
instead of being unlinked, so it can be restored until the retention window
expires. Lives inside the same root (same filesystem) so the move is a cheap
rename, and the scanner skips this folder (see scanner.TRASH_DIR_NAME).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config_loader import AppConfig
from app.core.scanner import TRASH_DIR_NAME

_STAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


@dataclass
class TrashEntry:
    trash_path: Path
    original_path: Path
    size_bytes: int
    deleted_at: datetime


def library_roots(config: AppConfig) -> list[Path]:
    """Unique incoming/archive roots (incoming often equals archive)."""
    seen: dict[Path, Path] = {}
    for root in (
        config.paths.incoming_movies, config.paths.incoming_tv,
        config.paths.archive_movies, config.paths.archive_tv,
    ):
        seen.setdefault(root.resolve(), root)
    return list(seen.values())


def root_for(path: Path, config: AppConfig) -> Path | None:
    """The deepest configured root containing `path`, or None."""
    resolved = path.resolve()
    best = None
    for root in library_roots(config):
        r = root.resolve()
        if (resolved == r or r in resolved.parents) and (best is None or len(r.parts) > len(best.resolve().parts)):
            best = root
    return best


def move_to_trash(target: Path, config: AppConfig) -> Path:
    """Moves `target` into its root's trash folder and returns the new path."""
    root = root_for(target, config)
    if root is None:
        raise ValueError("Path is outside the configured media directories")
    stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)
    rel = target.resolve().relative_to(root.resolve())
    dest = root / TRASH_DIR_NAME / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(dest))
    return dest


def list_trash(config: AppConfig) -> list[TrashEntry]:
    entries: list[TrashEntry] = []
    for root in library_roots(config):
        trash_root = root / TRASH_DIR_NAME
        if not trash_root.is_dir():
            continue
        for stamp_dir in sorted(trash_root.iterdir()):
            if not stamp_dir.is_dir():
                continue
            try:
                deleted_at = datetime.strptime(stamp_dir.name, _STAMP_FORMAT).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            for f in stamp_dir.rglob("*"):
                if f.is_file():
                    entries.append(TrashEntry(
                        trash_path=f,
                        original_path=root / f.relative_to(stamp_dir),
                        size_bytes=f.stat().st_size,
                        deleted_at=deleted_at,
                    ))
    return entries


def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
    current = start
    while current != stop_at and current.is_dir():
        try:
            current.rmdir()  # only succeeds when empty
        except OSError:
            return
        current = current.parent


def restore(trash_path: Path, config: AppConfig) -> Path:
    """Moves a trashed file back to where it came from. Refuses to clobber
    a file that has since appeared at the original location."""
    for entry in list_trash(config):
        if entry.trash_path.resolve() == trash_path.resolve():
            if entry.original_path.exists():
                raise FileExistsError(f"A file already exists at {entry.original_path}")
            entry.original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry.trash_path), str(entry.original_path))
            root = root_for(entry.original_path, config)
            _prune_empty_dirs(entry.trash_path.parent, (root / TRASH_DIR_NAME) if root else entry.trash_path.parent)
            return entry.original_path
    raise FileNotFoundError("Not in the trash")


def purge(config: AppConfig, older_than_days: int | None = None) -> int:
    """Permanently removes trashed files -- all of them, or only those
    trashed more than `older_than_days` ago. Returns how many were removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days) if older_than_days is not None else None
    removed = 0
    for entry in list_trash(config):
        if cutoff is not None and entry.deleted_at > cutoff:
            continue
        try:
            entry.trash_path.unlink()
            removed += 1
        except OSError:
            continue
        root = root_for(entry.original_path, config)
        if root:
            _prune_empty_dirs(entry.trash_path.parent, root / TRASH_DIR_NAME)
    for root in library_roots(config):
        trash_root = root / TRASH_DIR_NAME
        if trash_root.is_dir():
            try:
                trash_root.rmdir()  # only if now empty
            except OSError:
                pass
    return removed
