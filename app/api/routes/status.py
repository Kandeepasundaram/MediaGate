from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends

from app.config_loader import AppConfig
from app.core import media_probe
from app.core.scheduler import _seconds_until
from app.database import Database
from app.dependencies import START_TIME, get_config, get_database, get_tmdb_client
from app.core.tmdb_client import TMDBClient
from app.models import LogEntryOut, StatsResponse, StatusResponse

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=StatusResponse)
def get_status(
    config: AppConfig = Depends(get_config),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> StatusResponse:
    db_path = Path(config.database_path)
    return StatusResponse(
        tmdb_mode=tmdb.mode,
        database_path=str(config.database_path),
        ffprobe_available=media_probe.ffprobe_available(),
        database_size_bytes=db_path.stat().st_size if db_path.exists() else 0,
        uptime_seconds=time.monotonic() - START_TIME,
        next_tracker_check_in_seconds=_seconds_until(config.tracker.cron_time),
    )


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Database = Depends(get_database)) -> StatsResponse:
    items = db.list_media_items()
    movies_size = sum(_file_size(i["final_path"]) for i in items if i["media_type"] == "movie" and i.get("final_path"))
    tv_size = sum(_file_size(i["final_path"]) for i in items if i["media_type"] == "tv" and i.get("final_path"))
    return StatsResponse(
        total_media_items=len(items),
        total_movies=sum(1 for i in items if i["media_type"] == "movie"),
        total_tv_episodes=sum(1 for i in items if i["media_type"] == "tv"),
        total_size_bytes=movies_size + tv_size,
        movies_size_bytes=movies_size,
        tv_size_bytes=tv_size,
    )


def _file_size(path: str) -> int:
    from pathlib import Path

    p = Path(path)
    return p.stat().st_size if p.exists() else 0


@router.get("/logs", response_model=list[LogEntryOut])
def get_logs(limit: int = 50, db: Database = Depends(get_database)) -> list[LogEntryOut]:
    ops = db.list_operations(limit=limit)
    return [
        LogEntryOut(
            id=op["id"],
            operation_type=op["operation_type"],
            status=op["status"],
            created_at=op["created_at"],
            error_message=op["error_message"],
        )
        for op in ops
    ]
