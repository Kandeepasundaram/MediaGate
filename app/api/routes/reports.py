"""Periodic library/watch-activity reports (quarterly, half-yearly, or any
custom date range) -- the frontend computes the actual start/end dates for
a named period (e.g. "Q1 2026", "H1 2026") and always calls this with a
plain date range, so this route itself knows nothing about calendar
quarters/halves.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.status import compute_insights
from app.database import Database
from app.dependencies import get_database
from app.models import (
    ReportCleanupActivityOut,
    ReportComparisonOut,
    ReportGrowthOut,
    ReportMetadataBacklogOut,
    ReportSummaryOut,
    ReportTrackerActivityOut,
    ReportWatchActivityOut,
    ViewerWatchCountOut,
)

# Bulk deletes (e.g. orphan cleanup across a large library) can exceed
# list_operations' default limit=100 -- this app runs at homelab scale, so
# one generous cap rather than paginating is fine (same tradeoff
# reports.py already makes by loading db.list_media_items() in full).
_OPERATION_LOG_SCAN_LIMIT = 100_000

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _file_size(path: str) -> int:
    p = Path(path)
    return p.stat().st_size if p.exists() else 0


def _day_bounds(start: date, end: date) -> tuple[str, str]:
    """[start 00:00:00, end 23:59:59.999999] as UTC ISO timestamps --
    archived_at/watched_at are full timestamps, so a plain date-string
    comparison would silently exclude everything on `end` itself after
    midnight."""
    start_ts = datetime.combine(start, time.min, tzinfo=timezone.utc).isoformat()
    end_ts = datetime.combine(end, time.max, tzinfo=timezone.utc).isoformat()
    return start_ts, end_ts


def _previous_period(start: date, end: date) -> tuple[date, date]:
    """Same-length range immediately preceding [start, end], for the
    period-over-period delta -- e.g. Q2's previous period is Q1, not just
    "30 days back" if the requested range is a custom 47-day span."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end


def _comparison_totals(db: Database, start_ts: str, end_ts: str) -> dict:
    """Raw totals for the previous-period comparison -- same filters as the
    main summary but no per-viewer/insights breakdown, which a delta badge
    doesn't need."""
    items = db.list_media_items()
    added = [r for r in items if r["archived_at"] and start_ts <= r["archived_at"] <= end_ts]
    watched = [r for r in items if r["watched"] and r["watched_at"] and start_ts <= r["watched_at"] <= end_ts]
    notifications = db.list_notification_history_in_range(start_ts, end_ts)
    return {
        "movies_added": sum(1 for r in added if r["media_type"] == "movie"),
        "tv_episodes_added": sum(1 for r in added if r["media_type"] == "tv"),
        "total_size_bytes_added": sum(_file_size(r["final_path"]) for r in added if r.get("final_path")),
        "movies_watched": sum(1 for r in watched if r["media_type"] == "movie"),
        "tv_episodes_watched": sum(1 for r in watched if r["media_type"] == "tv"),
        "notifications_sent": len(notifications),
    }


def _metadata_backlog(db: Database) -> ReportMetadataBacklogOut:
    return ReportMetadataBacklogOut(
        pending_movies=db.count_unmatched_media_items("movie"),
        pending_tv=db.count_unmatched_media_items("tv"),
        failed_movies=db.count_failed_match_items("movie"),
        failed_tv=db.count_failed_match_items("tv"),
    )


def _cleanup_activity(db: Database, start_ts: str, end_ts: str) -> ReportCleanupActivityOut:
    ops = db.list_operations(
        operation_type="delete", since=start_ts, until=end_ts, limit=_OPERATION_LOG_SCAN_LIMIT
    )
    deleted_paths = []
    failed_count = 0
    for op in ops:
        if op["status"] != "success":
            failed_count += 1
            continue
        try:
            details = json.loads(op["details"]) if op.get("details") else {}
        except json.JSONDecodeError:
            details = {}
        path = details.get("path") or details.get("final_path")
        deleted_paths.append(Path(path).name if path else f"operation #{op['id']}")
    return ReportCleanupActivityOut(
        deleted_count=len(deleted_paths), failed_count=failed_count, deleted_paths=deleted_paths
    )


def build_report_summary(db: Database, start: date, end: date) -> ReportSummaryOut:
    """Core report computation -- shared by GET /api/reports/summary (below)
    and the periodic report-delivery scheduler (app/core/report_delivery.py),
    which needs the same summary without going through FastAPI. `start`/`end`
    are plain dates (inclusive on both ends); callers turn a named period
    ("Q1 2026", "H2 2025", "This Year", ...) into a concrete range before
    calling this -- it knows nothing about calendar quarters/halves itself.
    Every figure here is derived from media_items/viewer_watched_items/
    operation_log already being written by the rest of the app -- no
    separate reporting table.
    """
    if start > end:
        raise ValueError("start date must not be after end date")
    start_ts, end_ts = _day_bounds(start, end)

    items = db.list_media_items()
    added = [r for r in items if r["archived_at"] and start_ts <= r["archived_at"] <= end_ts]
    growth = ReportGrowthOut(
        movies_added=sum(1 for r in added if r["media_type"] == "movie"),
        tv_episodes_added=sum(1 for r in added if r["media_type"] == "tv"),
        total_size_bytes_added=sum(_file_size(r["final_path"]) for r in added if r.get("final_path")),
    )

    watched = [r for r in items if r["watched"] and r["watched_at"] and start_ts <= r["watched_at"] <= end_ts]
    viewer_counts = db.count_viewer_watched_in_range(start_ts, end_ts)
    viewer_seconds = db.sum_viewer_watch_seconds_in_range(start_ts, end_ts)
    by_viewer = [
        ViewerWatchCountOut(
            viewer_id=v["id"], viewer_name=v["name"], count=viewer_counts[v["id"]],
            watch_seconds=viewer_seconds.get(v["id"], 0.0),
        )
        for v in db.list_viewers()
        if v["id"] in viewer_counts
    ]
    watch_activity = ReportWatchActivityOut(
        movies_watched=sum(1 for r in watched if r["media_type"] == "movie"),
        tv_episodes_watched=sum(1 for r in watched if r["media_type"] == "tv"),
        by_viewer=by_viewer,
    )

    notifications = db.list_notification_history_in_range(start_ts, end_ts)
    tracker_activity = ReportTrackerActivityOut(
        notifications_sent=len(notifications),
        movies_notified=sum(1 for n in notifications if n["media_type"] == "movie"),
        tv_shows_notified=sum(1 for n in notifications if n["media_type"] == "tv"),
        titles=sorted({n["title"] for n in notifications}),
    )

    prev_start, prev_end = _previous_period(start, end)
    prev_start_ts, prev_end_ts = _day_bounds(prev_start, prev_end)
    prev_totals = _comparison_totals(db, prev_start_ts, prev_end_ts)
    previous_period = ReportComparisonOut(
        start_date=prev_start.isoformat(), end_date=prev_end.isoformat(), **prev_totals
    )

    return ReportSummaryOut(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        growth=growth,
        watch_activity=watch_activity,
        tracker_activity=tracker_activity,
        insights=compute_insights(added),
        previous_period=previous_period,
        metadata_backlog=_metadata_backlog(db),
        cleanup_activity=_cleanup_activity(db, start_ts, end_ts),
    )


@router.get("/summary", response_model=ReportSummaryOut)
def get_report_summary(
    start: date, end: date, db: Database = Depends(get_database)
) -> ReportSummaryOut:
    try:
        return build_report_summary(db, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
