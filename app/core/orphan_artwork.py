"""Finds/removes poster/nfo/subtitle files left behind in a folder that no
longer has any video file in it -- happens when a video gets renamed or
organized out from under them: download_artwork()/write_nfo() write
poster.jpg/fanart.jpg/{type}.nfo into the video's folder at archive time,
but nothing tracks or moves them afterward the way organizer.py's
_move_sibling_subtitles() does for subtitles specifically. Distinct from
library.py's existing orphan check, which is about stale `media_items`
database rows, not stray files on disk.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.scanner import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)

ARTWORK_NAMES = {"poster.jpg", "fanart.jpg", "movie.nfo", "tv.nfo"}


@dataclass
class OrphanArtworkGroup:
    folder: Path
    files: list[Path] = field(default_factory=list)


def find_orphaned_artwork(root: Path) -> list[OrphanArtworkGroup]:
    """Every folder under `root` that has at least one poster/nfo/subtitle
    file but no video file alongside it."""
    if not root.exists():
        return []

    dirs = sorted({p.parent for p in root.rglob("*") if p.is_file()})
    groups: list[OrphanArtworkGroup] = []
    for d in dirs:
        entries = [p for p in d.iterdir() if p.is_file()]
        if any(p.suffix.lower() in VIDEO_EXTENSIONS for p in entries):
            continue
        stray = sorted(
            p for p in entries if p.name.lower() in ARTWORK_NAMES or p.suffix.lower() in SUBTITLE_EXTENSIONS
        )
        if stray:
            groups.append(OrphanArtworkGroup(folder=d, files=stray))
    return groups


def cleanup_orphaned_artwork(groups: list[OrphanArtworkGroup]) -> int:
    """Deletes every file in every group, best-effort. Returns the count
    actually removed -- a failure on one file doesn't stop the rest."""
    removed = 0
    for group in groups:
        for f in group.files:
            try:
                f.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove orphaned artwork %s: %s", f, exc)
    return removed
