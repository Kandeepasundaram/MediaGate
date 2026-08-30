"""Recursively scans a directory for media files and groups them for review."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.tmdb_client import ParsedFilename, parse_filename

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}


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

    video_paths = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
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
