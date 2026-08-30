"""Deletes non-English subtitle files, with dry-run support and empty-folder cleanup."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}
DEFAULT_KEEP_LANGUAGES = {"en", "eng", "english"}


@dataclass
class PurgeResult:
    kept: list[Path]
    deleted: list[Path]
    dry_run: bool


def _is_english(path: Path, keep_languages: set[str]) -> bool:
    """A subtitle like `Movie.en.srt` or `Movie.english.srt` is kept.
    A plain `Movie.srt` with no language tag is kept too (assumed default/English).
    """
    stem_parts = path.stem.lower().split(".")
    if len(stem_parts) == 1:
        return True
    tag = stem_parts[-1]
    return tag in keep_languages


def purge_subtitles(
    root: Path | str,
    keep_languages: list[str] | None = None,
    dry_run: bool = False,
    cleanup_empty_dirs: bool = True,
) -> PurgeResult:
    root = Path(root)
    keep = {lang.lower() for lang in (keep_languages or DEFAULT_KEEP_LANGUAGES)}

    kept: list[Path] = []
    deleted: list[Path] = []

    if not root.exists():
        return PurgeResult(kept=kept, deleted=deleted, dry_run=dry_run)

    subtitle_files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUBTITLE_EXTENSIONS]

    for sub in subtitle_files:
        if _is_english(sub, keep):
            kept.append(sub)
            continue
        deleted.append(sub)
        if not dry_run:
            try:
                sub.unlink()
                logger.info("Deleted non-English subtitle: %s", sub)
            except OSError as exc:
                logger.error("Failed to delete %s: %s", sub, exc)
                deleted.remove(sub)

    if cleanup_empty_dirs and not dry_run:
        _remove_empty_dirs(root)

    return PurgeResult(kept=kept, deleted=deleted, dry_run=dry_run)


def _remove_empty_dirs(root: Path) -> None:
    for dirpath in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if dirpath.is_dir():
            try:
                next(dirpath.iterdir())
            except StopIteration:
                dirpath.rmdir()
                logger.info("Removed empty directory: %s", dirpath)
            except OSError:
                pass
