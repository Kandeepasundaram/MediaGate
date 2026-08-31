"""Recursively scans a directory for media files and groups them for review."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.tmdb_client import ParsedFilename, parse_filename

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}

# Standard Plex/Jellyfin/Kodi "extras" subfolder names -- a video sitting in
# one of these is a featurette/trailer/etc, not the main title, and would
# otherwise get scanned and TMDB-matched as if it were its own movie/episode.
EXTRAS_DIR_NAMES = {
    "extras", "featurettes", "behind the scenes", "deleted scenes",
    "interviews", "scenes", "shorts", "trailers", "other",
}

# A promotional sample clip bundled with some releases (e.g.
# "Movie.Name.2020.sample.mkv") -- junk to archive, not the movie itself.
# Anchored to the *end* of the filename specifically: "sample" bounded
# anywhere in the string (e.g. also matching at the start) would misfire
# on a real title that happens to start with the word, like "Sample Movie
# (2020)" -- real releases tag a sample clip as a trailing marker, not a
# leading one.
_SAMPLE_PATTERN = re.compile(r"[.\s_-]sample$", re.IGNORECASE)


def is_extra_or_sample(path: Path) -> bool:
    if path.parent.name.lower() in EXTRAS_DIR_NAMES:
        return True
    return bool(_SAMPLE_PATTERN.search(path.stem))


@dataclass
class SubtitleFile:
    path: Path
    size_bytes: int


@dataclass
class ScannedFile:
    path: Path
    size_bytes: int
    modified_at: float
    parsed: ParsedFilename
    subtitles: list[SubtitleFile] = field(default_factory=list)


def scan_directory(root: Path | str) -> list[ScannedFile]:
    """Walk `root` recursively, returning one ScannedFile per video found.

    Subtitles are matched to a video by directory + matching filename stem.
    """
    root = Path(root)
    if not root.exists():
        return []

    video_paths = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not is_extra_or_sample(p)
    ]
    subtitle_paths = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUBTITLE_EXTENSIONS]

    results: list[ScannedFile] = []
    for video in sorted(video_paths):
        stat = video.stat()
        matched_subs = [
            SubtitleFile(path=s, size_bytes=s.stat().st_size)
            for s in subtitle_paths
            if s.parent == video.parent and s.stem.startswith(video.stem)
        ]
        results.append(
            ScannedFile(
                path=video,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
                parsed=parse_filename(video.name),
                subtitles=matched_subs,
            )
        )
    return results


def scan_targets(roots: list[Path | str], known_paths: set[str] | None = None) -> list[ScannedFile]:
    """Scan multiple directories (e.g. incoming + both archive roots when a
    library is organized in-place rather than staged separately), deduping
    files reachable from more than one root and dropping anything already
    known (a previously-archived source, or an already-organized copy sitting
    inside an archive root) per `known_paths` (absolute path strings).
    """
    known_paths = known_paths or set()
    seen: dict[Path, ScannedFile] = {}

    for root in roots:
        for scanned in scan_directory(root):
            resolved = scanned.path.resolve()
            if str(resolved) in known_paths:
                continue
            seen.setdefault(resolved, scanned)

    return sorted(seen.values(), key=lambda s: s.path)
