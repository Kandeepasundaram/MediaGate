"""Copies files into the archive tree and records the operation in the database."""
from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone

from app.core.renamer import RenamePlan, download_artwork, write_nfo
from app.core.tmdb_client import MediaResult
from app.database import Database

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024


class ArchiveError(Exception):
    pass


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_file(db: Database, plan: RenamePlan) -> int:
    """Copy (not move) plan.source_path to plan.dest_path, log to DB, return media_item id.

    Verifies the copy by comparing a sha256 checksum of source and dest --
    shutil.copy2 doesn't itself guarantee byte-for-byte fidelity on a flaky
    disk or network mount, and a silently-corrupt archive copy is worse than
    a loud failure here. On mismatch, the bad copy is removed so a retry
    doesn't collide with a partial file.
    """
    plan.dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        source_size = plan.source_path.stat().st_size
    except OSError as exc:
        db.log_operation(
            operation_type="archive",
            status="failed",
            error_message=str(exc),
            details={"source": str(plan.source_path), "dest": str(plan.dest_path)},
        )
        raise ArchiveError(f"Failed to copy {plan.source_path} -> {plan.dest_path}: {exc}") from exc

    if source_size == 0:
        # A 0-byte file is almost always a still-downloading or corrupt
        # placeholder, not a real media file -- archiving it would just move
        # the problem into the library instead of catching it here.
        message = f"Source file is 0 bytes (likely still downloading or corrupt): {plan.source_path}"
        db.log_operation(
            operation_type="archive",
            status="failed",
            error_message=message,
            details={"source": str(plan.source_path), "dest": str(plan.dest_path)},
        )
        raise ArchiveError(message)

    try:
        shutil.copy2(plan.source_path, plan.dest_path)
        source_hash = sha256(plan.source_path)
        dest_hash = sha256(plan.dest_path)
        if source_hash != dest_hash:
            plan.dest_path.unlink(missing_ok=True)
            raise OSError(f"Checksum mismatch after copy (source={source_hash}, dest={dest_hash})")
    except OSError as exc:
        db.log_operation(
            operation_type="archive",
            status="failed",
            error_message=str(exc),
            details={"source": str(plan.source_path), "dest": str(plan.dest_path)},
        )
        raise ArchiveError(f"Failed to copy {plan.source_path} -> {plan.dest_path}: {exc}") from exc

    _write_nfo_best_effort(plan)
    _download_artwork_best_effort(plan)

    now = datetime.now(timezone.utc).isoformat()
    media_id = db.create_media_item(
        original_path=str(plan.source_path),
        title=plan.title,
        year=plan.year,
        tmdb_id=plan.tmdb_id,
        media_type=plan.media_type,
        season_number=plan.season,
        episode_number=plan.episode,
        final_path=str(plan.dest_path),
        archived_at=now,
        metadata={
            "poster_path": plan.poster_path,
            "overview": plan.overview,
            "episode_title": plan.episode_title,
            "vote_average": plan.vote_average,
            "genres": plan.genres,
        },
    )

    db.log_operation(
        operation_type="archive",
        status="success",
        media_id=media_id,
        details={"source": str(plan.source_path), "dest": str(plan.dest_path)},
    )
    logger.info("Archived %s -> %s", plan.source_path, plan.dest_path)
    return media_id


def _write_nfo_best_effort(plan: RenamePlan) -> None:
    """Writes a Plex/Jellyfin-readable .nfo alongside the archived file.
    Best-effort: an NFO write failure shouldn't fail an otherwise-successful
    archive, so errors are logged, not raised."""
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


def _download_artwork_best_effort(plan: RenamePlan) -> None:
    """Saves poster.jpg alongside the archived file, same Plex/Jellyfin
    convention as the .nfo -- Radarr/Sonarr already do this for files they
    import, so a file archived through this app should look the same on
    disk. download_artwork() is itself best-effort (network failures are
    logged, not raised) and a no-op when there's no poster_path."""
    download_artwork(plan.dest_path.parent, plan.poster_path)
