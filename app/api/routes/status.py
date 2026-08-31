from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from app.config_loader import AppConfig
from app.core import media_probe
from app.core.low_disk_alert import check_low_disk
from app.core.scheduler import _seconds_until, get_task_status
from app.database import Database
from app.dependencies import START_TIME, get_config, get_database, get_tmdb_client
from app.core.tmdb_client import TMDBClient
from app.models import (
    BackfillTaskStatusOut,
    BackgroundTasksStatusOut,
    GenreCountOut,
    GrowthPointOut,
    LogEntryOut,
    ResolutionStatOut,
    SimpleTaskStatusOut,
    StatsInsightsResponse,
    StatsResponse,
    StatusResponse,
    StoragePathOut,
    StorageStatusOut,
    TrackerTaskStatusOut,
)

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


@router.get("/status/tasks", response_model=BackgroundTasksStatusOut)
def get_background_tasks_status(
    config: AppConfig = Depends(get_config), db: Database = Depends(get_database)
) -> BackgroundTasksStatusOut:
    """Health widget for the background tasks main.py's lifespan starts --
    tracker check, metadata backfill, weekly maintenance, daily backup --
    none of which otherwise surfaces "is this actually running" anywhere.
    Tracker/backfill both have real signal already (operation_log rows,
    the unmatched-items queue depth); backup/maintenance don't have their
    own operation_log entry type, so those two come from scheduler.py's
    in-memory _task_status instead (reset on restart -- an honest "unknown
    yet" is better than a stale claim survived from before the restart).
    """
    last_checks = db.list_operations(operation_type="tracker_check", limit=1)
    last_check = last_checks[0] if last_checks else None
    task_status = get_task_status()

    return BackgroundTasksStatusOut(
        tracker=TrackerTaskStatusOut(
            last_check_at=last_check["created_at"] if last_check else None,
            last_check_status=last_check["status"] if last_check else None,
            next_check_in_seconds=_seconds_until(config.tracker.cron_time),
        ),
        backfill=BackfillTaskStatusOut(
            pending=db.count_unmatched_media_items(None),
            failed=db.count_failed_match_items(None),
        ),
        backup=SimpleTaskStatusOut(
            last_run_at=task_status["backup"]["last_run_at"],
            last_error=task_status["backup"]["last_error"],
            enabled=config.backup.enabled,
        ),
        maintenance=SimpleTaskStatusOut(
            last_run_at=task_status["maintenance"]["last_run_at"],
            last_error=task_status["maintenance"]["last_error"],
        ),
    )


def _forecast_days_to_full(snapshots: list[dict], free_bytes: int) -> tuple[float | None, int]:
    """Linear days-to-full projection from the oldest vs. newest snapshot in
    the window -- a two-point slope rather than a full regression, which is
    plenty for a "roughly how worried should I be" number and doesn't need
    a stats dependency. None means either fewer than 2 days of history yet,
    or usage isn't trending upward (shrinking/flat is never "full")."""
    if len(snapshots) < 2:
        return None, 0
    oldest, newest = snapshots[0], snapshots[-1]
    elapsed_days = (
        datetime.fromisoformat(newest["created_at"]) - datetime.fromisoformat(oldest["created_at"])
    ).total_seconds() / 86400
    if elapsed_days <= 0:
        return None, 0
    bytes_per_day = (newest["used_bytes"] - oldest["used_bytes"]) / elapsed_days
    if bytes_per_day <= 0:
        return None, len(snapshots)
    return free_bytes / bytes_per_day, len(snapshots)


@router.get("/status/storage", response_model=StorageStatusOut)
def get_storage_status(
    config: AppConfig = Depends(get_config), db: Database = Depends(get_database)
) -> StorageStatusOut:
    """Disk total/used/free for each configured media path -- the Movies/TV
    incoming and archive folders commonly point at the same physical path
    (per config_loader's own default), so those are reported as one row
    with a combined label rather than as duplicate entries with the same
    numbers. Read-only aside from recording today's usage as a snapshot
    (see Database.record_storage_snapshot) for the days-to-full forecast --
    otherwise this is just shutil.disk_usage(), same as before.
    """
    labeled_paths = [
        ("Movies incoming", config.paths.incoming_movies),
        ("Movies archive", config.paths.archive_movies),
        ("TV incoming", config.paths.incoming_tv),
        ("TV archive", config.paths.archive_tv),
    ]
    grouped: dict[Path, list[str]] = {}
    for label, path in labeled_paths:
        grouped.setdefault(path, []).append(label)

    paths_out = []
    for path, labels in grouped.items():
        label = " / ".join(labels)
        if not path.exists():
            paths_out.append(StoragePathOut(label=label, path=str(path), exists=False))
            continue
        usage = shutil.disk_usage(path)
        db.record_storage_snapshot(label, usage.used, usage.total)
        check_low_disk(config, label, usage.free)
        days_to_full, history_days = _forecast_days_to_full(db.list_storage_snapshots(label), usage.free)
        paths_out.append(
            StoragePathOut(
                label=label, path=str(path), exists=True,
                total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free,
                days_to_full=days_to_full, history_days=history_days,
            )
        )
    return StorageStatusOut(paths=paths_out)


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


def _metadata_dict(row: dict) -> dict:
    try:
        meta = json.loads(row["metadata"]) if row.get("metadata") else {}
    except json.JSONDecodeError:
        meta = {}
    return meta if isinstance(meta, dict) else {}


@router.get("/stats/insights", response_model=StatsInsightsResponse)
def get_stats_insights(db: Database = Depends(get_database)) -> StatsInsightsResponse:
    """Settings tab "Insights" card: top genres by item count, average file
    size per resolution bucket, and archive activity by month -- all
    derived from data media_items/metadata already has (TMDB genres,
    ffprobe-derived height, archived_at), no new tracking needed.
    """
    items = db.list_media_items()

    genre_counts: dict[str, int] = {}
    resolution_sizes: dict[str, list[int]] = {}
    month_counts: dict[str, int] = {}

    for row in items:
        meta = _metadata_dict(row)

        for genre in meta.get("genres") or []:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1

        if row.get("final_path"):
            size = _file_size(row["final_path"])
            if size:
                bucket = media_probe.resolution_bucket(meta.get("height")) or "Unknown"
                resolution_sizes.setdefault(bucket, []).append(size)

        if row.get("archived_at"):
            month = row["archived_at"][:7]  # "YYYY-MM"
            month_counts[month] = month_counts.get(month, 0) + 1

    top_genres = [
        GenreCountOut(genre=genre, count=count)
        for genre, count in sorted(genre_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]
    resolution_breakdown = [
        ResolutionStatOut(resolution=res, count=len(sizes), avg_size_bytes=int(sum(sizes) / len(sizes)))
        for res, sizes in sorted(resolution_sizes.items(), key=lambda kv: -len(kv[1]))
    ]
    growth_by_month = [GrowthPointOut(month=month, count=count) for month, count in sorted(month_counts.items())]

    return StatsInsightsResponse(
        top_genres=top_genres, resolution_breakdown=resolution_breakdown, growth_by_month=growth_by_month
    )


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
