"""Periodic library/watch-activity reports (quarterly, half-yearly, or any
custom date range) -- the frontend computes the actual start/end dates for
a named period (e.g. "Q1 2026", "H1 2026") and always calls this with a
plain date range, so this route itself knows nothing about calendar
quarters/halves.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.library_common import _tags_list
from app.api.routes.status import compute_insights
from app.database import Database
from app.dependencies import get_database
from app.models import (
    GenreCountOut,
    ReportBacklogOut,
    ReportCleanupActivityOut,
    ReportComparisonOut,
    ReportContentProfileOut,
    ReportEngagementOut,
    ReportGrowthOut,
    ReportMatchQualityOut,
    ReportMetadataBacklogOut,
    ReportOperationsHealthOut,
    ReportStorageTrendOut,
    ReportSummaryOut,
    ReportTrackerActivityOut,
    ReportUniverseActivityOut,
    ReportWatchActivityOut,
    StorageTrendPointOut,
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


def _content_profile(added: list[dict]) -> ReportContentProfileOut:
    sizes = [(r, _file_size(r["final_path"])) for r in added if r.get("final_path")]
    sizes = [(r, s) for r, s in sizes if s > 0]
    years = [r["year"] for r in added if r.get("year")]
    if not sizes:
        return ReportContentProfileOut(avg_release_year=round(statistics.mean(years), 1) if years else None)
    just_sizes = [s for _, s in sizes]
    largest_row, largest_size = max(sizes, key=lambda pair: pair[1])
    return ReportContentProfileOut(
        avg_file_size_bytes=int(statistics.mean(just_sizes)),
        median_file_size_bytes=int(statistics.median(just_sizes)),
        largest_title=largest_row["title"],
        largest_size_bytes=largest_size,
        avg_release_year=round(statistics.mean(years), 1) if years else None,
    )


def _match_quality(added: list[dict]) -> ReportMatchQualityOut:
    if not added:
        return ReportMatchQualityOut()
    matched = sum(1 for r in added if r.get("tmdb_id"))
    unmatched = len(added) - matched
    return ReportMatchQualityOut(
        matched_count=matched,
        unmatched_count=unmatched,
        match_rate_pct=round(matched / len(added) * 100, 1),
        manual_override_count=sum(1 for r in added if r.get("manual_override")),
        imdb_linked_count=sum(1 for r in added if r.get("imdb_id")),
    )


def _universe_activity(db: Database, start_ts: str, end_ts: str) -> ReportUniverseActivityOut:
    members = [m for m in db.list_all_universe_members() if start_ts <= m["added_at"] <= end_ts]
    return ReportUniverseActivityOut(
        titles_added_count=len(members), titles=sorted({m["title"] for m in members})
    )


def _storage_trend(db: Database, start_ts: str, end_ts: str) -> ReportStorageTrendOut:
    by_label: dict[str, list[dict]] = {}
    for snap in db.list_storage_snapshots_in_range(start_ts, end_ts):
        by_label.setdefault(snap["label"], []).append(snap)
    paths = [
        StorageTrendPointOut(
            label=label,
            start_used_bytes=snaps[0]["used_bytes"],
            end_used_bytes=snaps[-1]["used_bytes"],
            delta_bytes=snaps[-1]["used_bytes"] - snaps[0]["used_bytes"],
        )
        for label, snaps in by_label.items()
    ]
    return ReportStorageTrendOut(paths=sorted(paths, key=lambda p: p.label))


def _backlog(items: list[dict]) -> ReportBacklogOut:
    unwatched = [r for r in items if not r["watched"]]
    return ReportBacklogOut(
        unwatched_count=len(unwatched),
        unwatched_size_bytes=sum(_file_size(r["final_path"]) for r in unwatched if r.get("final_path")),
    )


def _engagement(added: list[dict], viewer_counts: dict[int, int]) -> ReportEngagementOut:
    tag_counts: dict[str, int] = {}
    for row in added:
        for tag in _tags_list(row):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = [
        GenreCountOut(genre=tag, count=count)
        for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]
    return ReportEngagementOut(
        distinct_active_viewers=sum(1 for v in viewer_counts.values() if v > 0), top_tags=top_tags
    )


def _operations_health(db: Database, start_ts: str, end_ts: str) -> ReportOperationsHealthOut:
    ops = [
        op
        for op_type in ("archive", "rename")
        for op in db.list_operations(operation_type=op_type, since=start_ts, until=end_ts, limit=_OPERATION_LOG_SCAN_LIMIT)
    ]
    succeeded = sum(1 for op in ops if op["status"] == "success")
    failed = sum(1 for op in ops if op["status"] == "failed")
    total = succeeded + failed
    return ReportOperationsHealthOut(
        succeeded=succeeded, failed=failed,
        success_rate_pct=round(succeeded / total * 100, 1) if total else None,
    )


def _tracker_activity_extra(db: Database, start_ts: str, end_ts: str) -> dict:
    """Extra archive_tracker fields merged into ReportTrackerActivityOut --
    kept separate from build_report_summary's notification-based totals
    since this reads archive_tracker directly instead of notification_history."""
    trackers = db.list_tracked()
    new_trackers = [t for t in trackers if start_ts <= t["created_at"] <= end_ts]
    checks = db.list_operations(
        operation_type="tracker_check", since=start_ts, until=end_ts, limit=_OPERATION_LOG_SCAN_LIMIT
    )
    return {
        "new_trackers_added": len(new_trackers),
        "new_trackers_watching": sum(1 for t in new_trackers if t["category"] == "watching"),
        "new_trackers_interested": sum(1 for t in new_trackers if t["category"] == "interested"),
        "new_trackers_watched": sum(1 for t in new_trackers if t["category"] == "watched"),
        "muted_trackers_total": sum(1 for t in trackers if t["muted"]),
        "tracker_checks_run": len(checks),
    }


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
        **_tracker_activity_extra(db, start_ts, end_ts),
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
        content_profile=_content_profile(added),
        match_quality=_match_quality(added),
        universe_activity=_universe_activity(db, start_ts, end_ts),
        storage_trend=_storage_trend(db, start_ts, end_ts),
        backlog=_backlog(items),
        engagement=_engagement(added, viewer_counts),
        operations_health=_operations_health(db, start_ts, end_ts),
    )


@router.get("/summary", response_model=ReportSummaryOut)
def get_report_summary(
    start: date, end: date, db: Database = Depends(get_database)
) -> ReportSummaryOut:
    try:
        return build_report_summary(db, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
