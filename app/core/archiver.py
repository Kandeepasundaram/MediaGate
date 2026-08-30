"""Copies files into the archive tree and records the operation in the database."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone

from app.core.renamer import RenamePlan
from app.database import Database

logger = logging.getLogger(__name__)


class ArchiveError(Exception):
    pass


def archive_file(db: Database, plan: RenamePlan) -> int:
    """Copy (not move) plan.source_path to plan.dest_path, log to DB, return media_item id.

    Checksum verification is intentionally deferred (see phases plan.txt Phase 3.4).
    """
    plan.dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(plan.source_path, plan.dest_path)
    except OSError as exc:
        db.log_operation(
            operation_type="archive",
            status="failed",
            error_message=str(exc),
            details={"source": str(plan.source_path), "dest": str(plan.dest_path)},
        )
        raise ArchiveError(f"Failed to copy {plan.source_path} -> {plan.dest_path}: {exc}") from exc

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
