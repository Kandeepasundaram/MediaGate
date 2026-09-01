from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.core.tmdb_client import TMDBClient
from app.core.tracker import check_movie_collection, check_tv_show
from app.database import Database
from app.dependencies import get_database, get_tmdb_client
from app.models import (
    NotificationHistoryEntryOut,
    NotificationHistoryResponse,
    TrackedListResponse,
    TrackerAcknowledgeRequest,
    TrackerAddRequest,
    TrackerBulkAddRequest,
    TrackerBulkAddResponse,
    TrackerBulkPreviewItemOut,
    TrackerBulkPreviewRequest,
    TrackerBulkPreviewResponse,
    TrackerIntervalRequest,
    TrackerMuteRequest,
    TrackerNotificationOut,
    TrackerNotificationsResponse,
    TrackerSnoozeRequest,
    TrackerStatusResponse,
    TrackerWatchProgressRequest,
    UpcomingReleaseOut,
    UpcomingReleasesResponse,
)

router = APIRouter(prefix="/api/tracker", tags=["tracker"])


def _to_out(row: dict) -> TrackerNotificationOut:
    return TrackerNotificationOut(
        id=row["id"],
        tmdb_id=row["tmdb_id"],
        media_type=row["media_type"],
        title=row["title"],
        current_season_archived=row["current_season_archived"],
        latest_known_season=row["latest_known_season"],
        movie_release_status=row["movie_release_status"],
        pending_notification=bool(row["pending_notification"]),
        muted=bool(row["muted"]),
        last_checked=row["last_checked"],
        snoozed_until=row["snoozed_until"],
        check_interval_hours=row["check_interval_hours"],
        next_episode_air_date=row["next_episode_air_date"],
        poster_path=row["poster_path"],
        overview=row["overview"],
        watched_through_season=row["watched_through_season"],
        watched_through_episode=row["watched_through_episode"],
    )


@router.get("/notifications", response_model=TrackerNotificationsResponse)
def get_notifications(db: Database = Depends(get_database)) -> TrackerNotificationsResponse:
    rows = db.list_pending_notifications()
    return TrackerNotificationsResponse(notifications=[_to_out(r) for r in rows])


@router.post("/acknowledge")
def acknowledge_notification(payload: TrackerAcknowledgeRequest, db: Database = Depends(get_database)) -> dict:
    db.acknowledge_notification(payload.tracker_id)
    return {"acknowledged": True}


@router.post("/add")
def add_tracker(payload: TrackerAddRequest, db: Database = Depends(get_database)) -> dict:
    db.upsert_tracker(
        tmdb_id=payload.tmdb_id,
        media_type=payload.media_type,
        title=payload.title,
        current_season_archived=payload.current_season_archived,
        poster_path=payload.poster_path,
        overview=payload.overview,
        last_checked=datetime.now(timezone.utc).isoformat(),
    )
    row = db.get_tracker(payload.tmdb_id, payload.media_type)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create tracker entry")
    return {"tracker": _to_out(row)}


@router.post("/bulk-preview", response_model=TrackerBulkPreviewResponse)
def bulk_preview_tracker(
    payload: TrackerBulkPreviewRequest, tmdb: TMDBClient = Depends(get_tmdb_client)
) -> TrackerBulkPreviewResponse:
    """One TMDB search per pasted title, top result only (same "take the
    first match" convention the archive preview flow uses for an
    unattended scan) -- for the operator to eyeball and fix via the
    existing match picker before /bulk-add commits anything. A title with
    no result comes back with matched=false rather than being dropped, so
    the review table can show it as a miss instead of silently vanishing.
    """
    items: list[TrackerBulkPreviewItemOut] = []
    for raw_title in payload.titles:
        title = raw_title.strip()
        if not title:
            continue
        matches = tmdb.search_tv(title) if payload.media_type == "tv" else tmdb.search_movie(title)
        best = matches[0] if matches else None
        items.append(
            TrackerBulkPreviewItemOut(
                input_title=title,
                matched=best is not None,
                tmdb_id=best.tmdb_id if best else None,
                title=best.title if best else None,
                year=best.year if best else None,
                poster_path=best.poster_path if best else None,
                overview=best.overview if best else None,
            )
        )
    return TrackerBulkPreviewResponse(items=items)


@router.post("/bulk-add", response_model=TrackerBulkAddResponse)
def bulk_add_tracker(payload: TrackerBulkAddRequest, db: Database = Depends(get_database)) -> TrackerBulkAddResponse:
    """Commits every reviewed row from /bulk-preview (or from the manual
    match picker, for a row the auto-match got wrong) to the tracker in
    one request -- same upsert_tracker call /add makes, just looped."""
    now = datetime.now(timezone.utc).isoformat()
    for item in payload.items:
        db.upsert_tracker(
            tmdb_id=item.tmdb_id,
            media_type=item.media_type,
            title=item.title,
            poster_path=item.poster_path,
            overview=item.overview,
            last_checked=now,
        )
    return TrackerBulkAddResponse(added=len(payload.items))


@router.get("/list", response_model=TrackedListResponse)
def list_tracked(
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> TrackedListResponse:
    """Every tracked title, muted or not -- backs a "Tracked Titles" panel
    distinct from /notifications (which only surfaces unmuted, pending ones).

    Rows tracked before poster_path/overview existed (or added via the
    numeric-ID quick-add path) have them NULL -- lazily backfilled here,
    one TMDB lookup per missing row, same call check_tv_show/
    check_movie_collection already make. Tracked lists are small and
    TMDBClient memoizes per-instance, so this stays cheap; failures (no
    key, unresolvable title) are skipped silently, same tolerance the rest
    of the tracker has for TMDB being unavailable."""
    rows = db.list_tracked()
    for row in rows:
        if row["poster_path"] is not None:
            continue
        media = (
            tmdb.get_movie_details(row["tmdb_id"])
            if row["media_type"] == "movie"
            else tmdb.get_tv_details(row["tmdb_id"])
        )
        if media is None or not media.poster_path:
            continue
        db.update_tracker(row["id"], poster_path=media.poster_path, overview=media.overview)
        row["poster_path"] = media.poster_path
        row["overview"] = media.overview
    return TrackedListResponse(tracked=[_to_out(r) for r in rows])


@router.get("/upcoming", response_model=UpcomingReleasesResponse)
def upcoming_releases(
    days: int = 90,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> UpcomingReleasesResponse:
    """Calendar view for tracked titles: TMDB release date (movies) or next
    air date (TV), for whatever's due within `days` -- distinct from
    /notifications (which only fires once a season/sequel is actually
    *out*, via check_for_updates' polling loop). API-key-only, same
    data_available convention as /library/tv-status and /movie-status --
    scraper mode has no release-date field to read, so muted or
    unresolvable titles are silently skipped rather than erroring.
    """
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=days)
    items: list[UpcomingReleaseOut] = []

    for row in db.list_tracked():
        if row["muted"]:
            continue
        media = (
            tmdb.get_movie_details(row["tmdb_id"])
            if row["media_type"] == "movie"
            else tmdb.get_tv_details(row["tmdb_id"])
        )
        if media is None or media.source != "api":
            continue

        if row["media_type"] == "movie":
            date_str = media.raw.get("release_date")
            label = "Release"
        else:
            next_ep = media.raw.get("next_episode_to_air") or {}
            date_str = next_ep.get("air_date")
            season = next_ep.get("season_number")
            episode = next_ep.get("episode_number")
            label = f"S{season:02d}E{episode:02d}" if season is not None and episode is not None else "New episode"

        if not date_str:
            continue
        try:
            release_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if not (today <= release_date <= cutoff):
            continue

        items.append(
            UpcomingReleaseOut(
                tmdb_id=row["tmdb_id"], media_type=row["media_type"], title=row["title"],
                release_date=date_str, label=label,
            )
        )

    items.sort(key=lambda i: i.release_date)
    return UpcomingReleasesResponse(items=items)


@router.post("/{tracker_id}/mute")
def mute_tracker(tracker_id: int, payload: TrackerMuteRequest, db: Database = Depends(get_database)) -> dict:
    if db.get_tracker_by_id(tracker_id) is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    db.set_tracker_muted(tracker_id, payload.muted)
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.post("/{tracker_id}/snooze")
def snooze_tracker(tracker_id: int, payload: TrackerSnoozeRequest, db: Database = Depends(get_database)) -> dict:
    """"Remind me later": clears the current pending notification (like
    Mark Downloaded) but, unlike muting, only suppresses future checks for
    `days` -- once snoozed_until passes, the next scheduled check resumes
    normally and will re-flag pending_notification if the title is still
    behind."""
    if db.get_tracker_by_id(tracker_id) is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    until = (datetime.now(timezone.utc) + timedelta(days=payload.days)).isoformat()
    db.update_tracker(
        tracker_id,
        pending_notification=0,
        notification_sent_at=datetime.now(timezone.utc).isoformat(),
        snoozed_until=until,
    )
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.post("/{tracker_id}/interval")
def set_tracker_interval(tracker_id: int, payload: TrackerIntervalRequest, db: Database = Depends(get_database)) -> dict:
    """Overrides the global daily tracker.cron_time cadence for a single
    title; hours=None clears the override and falls back to the global
    schedule."""
    if db.get_tracker_by_id(tracker_id) is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    db.update_tracker(tracker_id, check_interval_hours=payload.hours)
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.post("/{tracker_id}/watch-progress")
def set_tracker_watch_progress(
    tracker_id: int, payload: TrackerWatchProgressRequest, db: Database = Depends(get_database)
) -> dict:
    """Records "watched up through SxxEyy" for a tracked show that has no
    archived files yet (see media_items.watched for the per-episode,
    file-backed equivalent). season=episode=None clears it."""
    if db.get_tracker_by_id(tracker_id) is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    db.update_tracker(
        tracker_id,
        watched_through_season=payload.season,
        watched_through_episode=payload.episode,
    )
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.post("/{tracker_id}/check-now")
def check_tracker_now(
    tracker_id: int,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> dict:
    """Runs the tracker's TMDB check for a single title immediately, rather
    than waiting for the next scheduled daily run."""
    row = db.get_tracker_by_id(tracker_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tracked title not found")
    if row["media_type"] == "tv":
        check_tv_show(db, tmdb, row)
    else:
        check_movie_collection(db, tmdb, row)
    return {"tracker": _to_out(db.get_tracker_by_id(tracker_id))}


@router.get("/history", response_model=NotificationHistoryResponse)
def get_notification_history(limit: int = 50, db: Database = Depends(get_database)) -> NotificationHistoryResponse:
    """A permanent record of notifications actually surfaced (unlike
    pending_notification, which is cleared on acknowledge/snooze) -- for
    "did I already get told about this" after clearing a notification."""
    return NotificationHistoryResponse(
        history=[NotificationHistoryEntryOut(**row) for row in db.list_notification_history(limit)]
    )


def _rfc822(iso_timestamp: str) -> str:
    return format_datetime(datetime.fromisoformat(iso_timestamp))


@router.get("/feed.rss")
def notification_feed_rss(limit: int = 50, db: Database = Depends(get_database)) -> Response:
    """RSS 2.0 feed of the notification history, for pointing a feed reader
    at instead of (or alongside) the webhook -- some setups want "check when
    I feel like it" rather than a push. Note: if server.api_token is set,
    this endpoint needs it too (it's under /api/*, same as everything else),
    so the feed reader has to be one that can send a custom header.
    """
    history = db.list_notification_history(limit)
    items_xml = "".join(
        f"<item>"
        f"<title>{escape(h['title'])}</title>"
        f"<description>{escape(h['message'])}</description>"
        f"<pubDate>{_rfc822(h['created_at'])}</pubDate>"
        f'<guid isPermaLink="false">media-manager-notification-{h["id"]}</guid>'
        f"</item>"
        for h in history
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>Media Manager Notifications</title>"
        "<description>New seasons and sequels detected for tracked titles</description>"
        f"{items_xml}"
        "</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/status", response_model=TrackerStatusResponse)
def tracker_status(db: Database = Depends(get_database)) -> TrackerStatusResponse:
    tracked = db.list_tracked()
    pending = db.list_pending_notifications()
    last_checked = max((r["last_checked"] for r in tracked if r["last_checked"]), default=None)
    return TrackerStatusResponse(
        total_tracked=len(tracked),
        pending_notifications=len(pending),
        last_checked=last_checked,
    )
