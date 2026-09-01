"""Periodic library/watch-activity reports (quarterly, half-yearly, or any
custom date range) -- the frontend computes the actual start/end dates for
a named period (e.g. "Q1 2026", "H1 2026") and always calls this with a
plain date range, so this route itself knows nothing about calendar
quarters/halves.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.status import compute_insights
from app.database import Database
from app.dependencies import get_database
from app.models import (
    ReportGrowthOut,
    ReportSummaryOut,
    ReportTrackerActivityOut,
    ReportWatchActivityOut,
    ViewerWatchCountOut,
)

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


@router.get("/summary", response_model=ReportSummaryOut)
def get_report_summary(
    start: date, end: date, db: Database = Depends(get_database)
) -> ReportSummaryOut:
    """`start`/`end` are plain dates (inclusive on both ends); the frontend
    is responsible for turning "Q1 2026", "H2 2025", "This Year", etc. into
    a concrete range before calling this. Every figure here is derived
    from media_items/viewer_watched_items already being written by the
    rest of the app -- no separate reporting table.
    """
    if start > end:
        raise HTTPException(status_code=400, detail="start date must not be after end date")
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

    return ReportSummaryOut(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        growth=growth,
        watch_activity=watch_activity,
        tracker_activity=tracker_activity,
        insights=compute_insights(added),
    )
