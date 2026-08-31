"""Read/browse the archived library and toggle manual watch state.

Distinct from /api/scan (which finds new, unarchived files): this reflects
whatever this app has itself archived into media_items, for the gallery
views and manual watch tracking that are Media Manager's own job alongside
Radarr/Sonarr's automated import pipeline.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.routes.archive import _dry_run_result
from app.config_loader import AppConfig
from app.core import media_probe
from app.core.library_adopt import adopt_new_files
from app.core.media_note import build_movie_note, build_tv_note
from app.core.omdb_client import OMDbClient
from app.core.orphan_artwork import ARTWORK_NAMES, cleanup_orphaned_artwork, find_orphaned_artwork
from app.core.organizer import OrganizeError, organize_file
from app.core.renamer import TMDB_IMAGE_BASE, RenamePlan, sanitize_filename
from app.core.scanner import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, scan_directory
from app.core.media_server import notify_media_servers, sync_watched_from_media_servers
from app.core.tmdb_client import MediaResult, TMDBClient, genres_for, season_episode_counts, vote_average_for
from app.core.tracker import maybe_auto_track
from app.database import Database
from app.dependencies import get_config, get_database, get_omdb_client, get_tmdb_client
from app.models import (
    ArchiveConfirmRequest,
    ArchiveConfirmResponse,
    ArchiveConfirmResult,
    BrowseItemOut,
    BrowseResponse,
    DeleteBatchRequest,
    DeleteBatchResponse,
    DeleteFileRequest,
    FileInfoOut,
    LibraryExportResponse,
    LibraryHealthOut,
    LibraryImportRequest,
    LibraryImportResponse,
    LibraryItemOut,
    LibraryResponse,
    ManualOverrideRequest,
    MediaItemExportOut,
    MediaType,
    MetadataStatusResponse,
    MovieRelatedTitleOut,
    MovieStatusOut,
    OrphanArtworkCleanupResponse,
    OrphanArtworkGroupOut,
    OrphanCleanupResponse,
    RatingsOut,
    RecommendationOut,
    RecommendationsResponse,
    RefreshMetadataRequest,
    RefreshMetadataResponse,
    RetryFailedMatchesResponse,
    RematchImdbRequest,
    RematchResponse,
    RematchTmdbRequest,
    CastMemberOut,
    MoreInfoOut,
    SimilarTitleOut,
    SyncWatchedResponse,
    TagsListResponse,
    TagsUpdateRequest,
    TrailerOut,
    NoteSaveResponse,
    TvLibraryResponse,
    TvSeasonSummaryOut,
    TvShowStatusUpdateRequest,
    TvShowSummaryOut,
    ViewerCreateRequest,
    ViewerOut,
    ViewersListResponse,
    ViewerWatchedUpdateRequest,
    TvStatusOut,
    WatchedBatchRequest,
    WatchedBatchResponse,
    WatchedUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])


def _metadata_dict(row: dict) -> dict:
    try:
        meta = json.loads(row["metadata"]) if row.get("metadata") else {}
    except json.JSONDecodeError:
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _tags_list(row: dict) -> list[str]:
    try:
        tags = json.loads(row["tags"]) if row.get("tags") else []
    except json.JSONDecodeError:
        tags = []
    return tags if isinstance(tags, list) else []


def _to_out(row: dict, viewer_watched_ids: set[int] | None = None, show_status: str | None = None) -> LibraryItemOut:
    meta = _metadata_dict(row)

    file_name = None
    size_bytes = None
    if row["final_path"]:
        final_path = Path(row["final_path"])
        file_name = final_path.name
        try:
            size_bytes = final_path.stat().st_size
        except OSError:
            pass  # file moved/deleted since archiving; name still worth showing

    return LibraryItemOut(
        id=row["id"],
        title=row["title"],
        media_type=row["media_type"],
        year=row["year"],
        season_number=row["season_number"],
        episode_number=row["episode_number"],
        tmdb_id=row["tmdb_id"],
        poster_path=meta.get("poster_path"),
        overview=meta.get("overview", ""),
        watched=bool(row["watched"]),
        final_path=row["final_path"],
        archived_at=row["archived_at"],
        file_name=file_name,
        size_bytes=size_bytes,
        episode_title=meta.get("episode_title"),
        manual_override=bool(row["manual_override"]),
        vote_average=meta.get("vote_average"),
        genres=meta.get("genres") or [],
        resolution=media_probe.resolution_bucket(meta.get("height")),
        hdr=bool(meta.get("hdr", False)),
        audio_channels=meta.get("audio_channels"),
        tags=_tags_list(row),
        viewer_watched=(row["id"] in viewer_watched_ids) if viewer_watched_ids is not None else None,
        show_status=show_status,
    )


@router.get("/movies", response_model=LibraryResponse)
def list_movies(
    viewer_id: int | None = None, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)
) -> LibraryResponse:
    """Auto-adopts (registers, no file operations, no network calls) any
    file physically in archive_movies not yet tracked, then returns
    everything tracked -- so files already organized by Radarr/Sonarr show
    up here without needing to be manually run through Ready to Archive.
    Newly-adopted rows show with a placeholder poster until the background
    metadata backfill (see /metadata-status) fills them in. `viewer_id`
    populates each item's `viewer_watched` with that viewer's own state
    (see viewers table) -- the plain `watched` field stays the single
    global flag every other feature already reads.
    """
    adopt_new_files(db, config, "movie")
    watched_ids = db.list_viewer_watched_ids(viewer_id) if viewer_id is not None else None
    return LibraryResponse(items=[_to_out(r, watched_ids) for r in db.list_media_items(media_type="movie")])


@router.get("/tv", response_model=TvLibraryResponse)
def list_tv(
    viewer_id: int | None = None, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)
) -> TvLibraryResponse:
    """Alongside the usual auto-adopt-then-list, this also syncs every
    tmdb-matched show still on disk into tv_shows (see Database.sync_tv_show)
    -- so a show survives in tv_shows before any of its episode files could
    ever be deleted -- and returns any tracked show with zero episode rows
    left (`orphaned_shows`) so it stays visible in the TV tab (with its
    user-set status) even after every file was deleted from disk.
    """
    adopt_new_files(db, config, "tv")
    rows = db.list_media_items(media_type="tv")
    watched_ids = db.list_viewer_watched_ids(viewer_id) if viewer_id is not None else None

    present_tmdb_ids: set[int] = set()
    latest_by_tmdb_id: dict[int, dict] = {}
    for row in rows:
        if row["tmdb_id"] is None:
            continue
        present_tmdb_ids.add(row["tmdb_id"])
        prior = latest_by_tmdb_id.get(row["tmdb_id"])
        if prior is None or (row["archived_at"] or "") > (prior["archived_at"] or ""):
            latest_by_tmdb_id[row["tmdb_id"]] = row

    for tmdb_id, row in latest_by_tmdb_id.items():
        meta = _metadata_dict(row)
        db.sync_tv_show(
            tmdb_id, row["title"], imdb_id=row["imdb_id"],
            poster_path=meta.get("poster_path"), overview=meta.get("overview"), genres=meta.get("genres"),
        )

    status_by_tmdb_id = {s["tmdb_id"]: s["status"] for s in db.list_tv_shows()}
    items = [_to_out(r, watched_ids, status_by_tmdb_id.get(r["tmdb_id"])) for r in rows]

    orphaned_shows = [
        TvShowSummaryOut(
            tmdb_id=s["tmdb_id"], title=s["title"], imdb_id=s["imdb_id"], poster_path=s["poster_path"],
            overview=s["overview"] or "", genres=json.loads(s["genres"]) if s["genres"] else [], status=s["status"],
        )
        for s in db.list_tv_shows() if s["tmdb_id"] not in present_tmdb_ids
    ]
    return TvLibraryResponse(items=items, orphaned_shows=orphaned_shows)


@router.post("/tv-shows/{tmdb_id}/status", response_model=TvShowSummaryOut)
def set_tv_show_status(tmdb_id: int, payload: TvShowStatusUpdateRequest, db: Database = Depends(get_database)) -> TvShowSummaryOut:
    show = db.get_tv_show(tmdb_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not tracked yet -- open its TV tab entry first")
    db.set_tv_show_status(tmdb_id, payload.status)
    show = db.get_tv_show(tmdb_id)
    return TvShowSummaryOut(
        tmdb_id=show["tmdb_id"], title=show["title"], imdb_id=show["imdb_id"], poster_path=show["poster_path"],
        overview=show["overview"] or "", genres=json.loads(show["genres"]) if show["genres"] else [], status=show["status"],
    )


@router.get("/search", response_model=LibraryResponse)
def search_library(q: str, db: Database = Depends(get_database)) -> LibraryResponse:
    """Cross-type title search backing the header's global search box --
    unlike the Movies/TV/Browse tabs' own search fields (which filter
    whatever that tab already loaded), this hits media_items directly so a
    title in a tab the user hasn't opened yet is still found. A blank/short
    query returns no results rather than the whole library.
    """
    q = q.strip()
    if len(q) < 2:
        return LibraryResponse(items=[])
    # Higher than the ~20 titles actually shown: a TV match returns one row
    # per episode, so a show with many episodes could otherwise crowd out
    # every other title before the frontend's per-title dedupe even runs.
    return LibraryResponse(items=[_to_out(r) for r in db.search_media_items(q, limit=60)])


@router.get("/recommendations", response_model=RecommendationsResponse)
def get_recommendations(
    media_type: MediaType,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RecommendationsResponse:
    """"Recommended for You" row: TMDB's per-title "similar" results,
    pooled across a sample of the library's most recently archived titles
    and ranked by how many of them pointed at the same candidate --
    library-wide, unlike the detail pane's own per-item Similar Titles
    section (get_more_info above), which only ever looks at one title.
    Already-owned candidates (by tmdb_id) are excluded. API-key-only --
    get_similar_titles returns [] in scraper mode, so tmdb_configured tells
    the frontend to show a hint instead of an empty row.
    """
    if tmdb.mode != "api":
        return RecommendationsResponse(items=[], tmdb_configured=False)

    rows = [r for r in db.list_media_items(media_type=media_type) if r["tmdb_id"] is not None]
    owned_tmdb_ids = {r["tmdb_id"] for r in rows}
    if media_type == "tv":
        # A show tracked in tv_shows but with every episode file deleted has
        # no media_items rows left, so it wouldn't otherwise count as "owned"
        # -- without this it'd resurface here right after being cleaned up.
        owned_tmdb_ids |= {s["tmdb_id"] for s in db.list_tv_shows()}

    # Most recently archived, deduped by tmdb_id (a TV show's many episode
    # rows would otherwise burn most of the sample on one title) and capped
    # -- each title costs one TMDB request, memoized per TMDBClient instance
    # (see get_similar_titles), but still not worth doing for the whole library.
    seed_ids: list[int] = []
    for row in sorted(rows, key=lambda r: r["archived_at"] or "", reverse=True):
        if row["tmdb_id"] not in seed_ids:
            seed_ids.append(row["tmdb_id"])
        if len(seed_ids) >= 15:
            break

    scores: dict[int, dict] = {}
    for seed_id in seed_ids:
        for candidate in tmdb.get_similar_titles(seed_id, media_type):
            if candidate.tmdb_id is None or candidate.tmdb_id in owned_tmdb_ids:
                continue
            entry = scores.setdefault(
                candidate.tmdb_id,
                {"title": candidate.title, "year": candidate.year, "poster_path": candidate.poster_path, "score": 0},
            )
            entry["score"] += 1

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]["score"], kv[1]["title"]))[:20]
    items = [
        RecommendationOut(tmdb_id=tid, media_type=media_type, **data)
        for tid, data in ranked
    ]
    return RecommendationsResponse(items=items, tmdb_configured=True)


@router.post("/sync-watched", response_model=SyncWatchedResponse)
def sync_watched(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> SyncWatchedResponse:
    """Manual trigger for pulling watched status back from Plex/Jellyfin --
    see sync_watched_from_media_servers for the matching rules and the
    movies-only, one-directional scope."""
    return SyncWatchedResponse(updated=sync_watched_from_media_servers(config, db))


@router.get("/tags", response_model=TagsListResponse)
def list_tags(db: Database = Depends(get_database)) -> TagsListResponse:
    """Distinct tag values across the library, for the gallery tag-filter
    dropdown -- same populate-on-load pattern the genre filter already
    uses, except genres come from TMDB metadata and tags are user-set."""
    return TagsListResponse(tags=db.list_all_tags())


@router.post("/{item_id}/tags", response_model=LibraryItemOut)
def set_tags(item_id: int, payload: TagsUpdateRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    """Replaces an item's full tag list (not an add/remove diff) -- the
    detail pane sends the complete edited list back, same as how genres
    aren't merged either."""
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    cleaned = sorted({t.strip() for t in payload.tags if t.strip()})
    db.update_media_item(item_id, tags=cleaned)
    return _to_out(db.get_media_item(item_id))


@router.get("/viewers", response_model=ViewersListResponse)
def list_viewers(db: Database = Depends(get_database)) -> ViewersListResponse:
    """Named viewer profiles for per-viewer watch state -- no password, not
    real accounts (see the viewers table's own migration docstring), just
    enough for a household sharing one LAN dashboard to tell "has Alex seen
    this" apart from "has Sam seen this"."""
    return ViewersListResponse(
        viewers=[ViewerOut(id=r["id"], name=r["name"], created_at=r["created_at"]) for r in db.list_viewers()]
    )


@router.post("/viewers", response_model=ViewerOut)
def create_viewer(payload: ViewerCreateRequest, db: Database = Depends(get_database)) -> ViewerOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Viewer name is required")
    try:
        viewer_id = db.create_viewer(name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"A viewer named {name!r} already exists") from exc
    row = db.get_viewer(viewer_id)
    return ViewerOut(id=row["id"], name=row["name"], created_at=row["created_at"])


@router.delete("/viewers/{viewer_id}")
def delete_viewer(viewer_id: int, db: Database = Depends(get_database)) -> dict:
    """Deleting a viewer also drops their per-item watched rows (the FK's
    ON DELETE CASCADE) -- their profile going away means "have they seen
    this" no longer has an answer to keep around."""
    if db.get_viewer(viewer_id) is None:
        raise HTTPException(status_code=404, detail="Viewer not found")
    db.delete_viewer(viewer_id)
    return {"deleted": True}


@router.post("/{item_id}/watched-by/{viewer_id}", response_model=LibraryItemOut)
def set_viewer_watched(
    item_id: int, viewer_id: int, payload: ViewerWatchedUpdateRequest, db: Database = Depends(get_database)
) -> LibraryItemOut:
    """Sets (or clears) this one viewer's own watched state for an item --
    independent of, and never writes to, the item's global `watched` flag
    every other feature reads."""
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if db.get_viewer(viewer_id) is None:
        raise HTTPException(status_code=404, detail="Viewer not found")
    db.set_viewer_watched(viewer_id, item_id, payload.watched)
    return _to_out(db.get_media_item(item_id), {item_id} if payload.watched else set())


@router.get("/metadata-status", response_model=MetadataStatusResponse)
def metadata_status(media_type: MediaType | None = None, db: Database = Depends(get_database)) -> MetadataStatusResponse:
    """Pending count for the background TMDB metadata backfill, so the
    dashboard can show progress and know when to stop polling for updates --
    plus a separate failed count (searched at least once, no match found),
    so a permanently-unmatched title doesn't look like it's "still loading".
    """
    return MetadataStatusResponse(
        pending=db.count_unmatched_media_items(media_type),
        failed=db.count_failed_match_items(media_type),
    )


@router.get("/export", response_model=LibraryExportResponse)
def export_library(db: Database = Depends(get_database)) -> LibraryExportResponse:
    """Dumps every media_items row as portable JSON -- a backup/restore path
    that doesn't depend on copying the SQLite file directly (e.g. moving to
    a fresh install, or archiving a snapshot alongside the media itself).
    `id` is deliberately omitted: importing into a different (or rebuilt)
    database shouldn't assume the old row ids still mean anything there.
    """
    items = [
        MediaItemExportOut(
            original_path=row["original_path"],
            title=row["title"],
            year=row["year"],
            tmdb_id=row["tmdb_id"],
            media_type=row["media_type"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            final_path=row["final_path"],
            archived_at=row["archived_at"],
            watched=bool(row["watched"]),
            metadata=_metadata_dict(row),
            imdb_id=row["imdb_id"],
            manual_override=bool(row["manual_override"]),
        )
        for row in db.list_media_items()
    ]
    return LibraryExportResponse(items=items, exported_at=datetime.now(timezone.utc).isoformat())


@router.post("/import", response_model=LibraryImportResponse)
def import_library(payload: LibraryImportRequest, db: Database = Depends(get_database)) -> LibraryImportResponse:
    """Restores media_items rows from a previous /export. An item is skipped
    (not overwritten) when its final_path is already tracked -- the same
    dedupe rule adopt_new_files uses -- so re-importing the same backup
    twice, or importing into a library that's since moved on, doesn't
    clobber anything or create duplicates.
    """
    imported = 0
    skipped = 0
    for item in payload.items:
        if item.final_path and db.get_media_item_by_final_path(item.final_path) is not None:
            skipped += 1
            continue
        db.create_media_item(
            original_path=item.original_path,
            title=item.title,
            year=item.year,
            tmdb_id=item.tmdb_id,
            media_type=item.media_type,
            season_number=item.season_number,
            episode_number=item.episode_number,
            final_path=item.final_path,
            archived_at=item.archived_at,
            watched=1 if item.watched else 0,
            metadata=item.metadata,
            imdb_id=item.imdb_id,
            manual_override=1 if item.manual_override else 0,
        )
        imported += 1
    return LibraryImportResponse(imported=imported, skipped=skipped)


@router.get("/health", response_model=LibraryHealthOut)
def library_health(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> LibraryHealthOut:
    """Three library-wide sanity checks the gallery views don't otherwise
    surface: orphans (a media_items row whose final_path no longer exists on
    disk -- moved or deleted outside this app), duplicates (more than one
    row pointing at the same tmdb_id/season/episode, e.g. from organizing the
    same episode from two different source files), and orphaned artwork
    (poster.jpg/*.nfo/subtitle files left in a folder whose video has since
    been renamed or moved away -- see app/core/orphan_artwork.py). Duplicates
    and orphaned artwork are reported only, never auto-resolved -- picking
    which copy to keep, or confirming a stray file really is stray, is a
    judgment call for the user.
    """
    items = db.list_media_items()

    orphans = [
        _to_out(row) for row in items if row["final_path"] and not Path(row["final_path"]).exists()
    ]

    groups: dict[tuple, list[dict]] = {}
    for row in items:
        if row["tmdb_id"] is None:
            continue
        key = (row["tmdb_id"], row["media_type"], row["season_number"], row["episode_number"])
        groups.setdefault(key, []).append(row)
    duplicates = [[_to_out(r) for r in rows] for rows in groups.values() if len(rows) > 1]

    artwork_groups = find_orphaned_artwork(config.paths.archive_movies) + find_orphaned_artwork(config.paths.archive_tv)
    orphaned_artwork = [
        OrphanArtworkGroupOut(folder=str(g.folder), files=[f.name for f in g.files]) for g in artwork_groups
    ]

    return LibraryHealthOut(orphans=orphans, duplicates=duplicates, orphaned_artwork=orphaned_artwork)


@router.post("/orphans/cleanup", response_model=OrphanCleanupResponse)
def cleanup_orphans(db: Database = Depends(get_database)) -> OrphanCleanupResponse:
    """Removes media_items rows whose final_path no longer exists on disk.
    There's no file to delete (it's already gone) -- just the stale DB row --
    so this logs a 'delete' operation for the audit trail without touching
    the filesystem, same operation_type the single-file delete-file uses.
    """
    removed = 0
    for row in db.list_media_items():
        if row["final_path"] and not Path(row["final_path"]).exists():
            db.log_operation(
                operation_type="delete",
                status="success",
                media_id=row["id"],
                details={"reason": "orphan_cleanup", "final_path": row["final_path"]},
            )
            db.delete_media_item(row["id"])
            removed += 1
    return OrphanCleanupResponse(removed=removed)


@router.post("/orphaned-artwork/cleanup", response_model=OrphanArtworkCleanupResponse)
def cleanup_orphaned_artwork_route(config: AppConfig = Depends(get_config)) -> OrphanArtworkCleanupResponse:
    """Deletes poster/nfo/subtitle files found by find_orphaned_artwork() --
    a fresh re-scan at cleanup time (not whatever /health last returned),
    so a file that got a video back in the meantime isn't touched."""
    groups = find_orphaned_artwork(config.paths.archive_movies) + find_orphaned_artwork(config.paths.archive_tv)
    removed = cleanup_orphaned_artwork(groups)
    return OrphanArtworkCleanupResponse(removed=removed)


@router.post("/retry-failed-matches", response_model=RetryFailedMatchesResponse)
def retry_failed_matches(media_type: MediaType | None = None, db: Database = Depends(get_database)) -> RetryFailedMatchesResponse:
    """Clears the retry cooldown on every previously-failed match so the
    background backfill re-searches them on its very next cycle, instead of
    waiting out list_unmatched_media_items' cooldown -- for after fixing a
    file name or when a title just wasn't on TMDB yet at the time.
    """
    return RetryFailedMatchesResponse(reset=db.reset_failed_match_attempts(media_type))


@router.post("/refresh-metadata", response_model=RefreshMetadataResponse)
def refresh_metadata(
    payload: RefreshMetadataRequest,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RefreshMetadataResponse:
    """Bulk "Refresh Metadata" for the gallery multi-select: re-fetches
    title/poster/overview/rating/genres from TMDB for each selected,
    already-matched item -- the tmdb_id itself never changes, unlike
    rematch-imdb/rematch-tmdb (which point an item at a *different* TMDB
    entry) or retry-failed-matches (which is for items with no tmdb_id at
    all yet). Uses TMDBClient.refresh_*_details, which bypasses that
    client's own cache -- otherwise a "refresh" during the same process
    lifetime would just hand back the same stale result it already had.

    Multiple selected rows sharing one tmdb_id (every episode of a TV show)
    only trigger one TMDB lookup, not one per row. ffprobe-derived fields
    (resolution/codec/HDR/audio) and episode_title are carried forward from
    the existing row -- refreshing metadata shouldn't need to re-probe the
    file or lose per-episode data TMDB doesn't have anyway.
    """
    now = datetime.now(timezone.utc).isoformat()
    media_cache: dict[tuple[int, str], MediaResult | None] = {}
    updated = 0
    failed = 0

    for item_id in payload.ids:
        row = db.get_media_item(item_id)
        if row is None or row["tmdb_id"] is None:
            failed += 1
            continue

        cache_key = (row["tmdb_id"], row["media_type"])
        if cache_key not in media_cache:
            media_cache[cache_key] = (
                tmdb.refresh_tv_details(row["tmdb_id"])
                if row["media_type"] == "tv"
                else tmdb.refresh_movie_details(row["tmdb_id"])
            )
        media = media_cache[cache_key]
        if media is None:
            failed += 1
            continue

        existing_meta = _metadata_dict(row)
        db.update_media_item(
            item_id,
            title=media.title,
            year=media.year,
            metadata={
                "width": existing_meta.get("width"),
                "height": existing_meta.get("height"),
                "video_codec": existing_meta.get("video_codec"),
                "hdr": existing_meta.get("hdr"),
                "audio_channels": existing_meta.get("audio_channels"),
                "episode_title": existing_meta.get("episode_title"),
                "poster_path": media.poster_path,
                "overview": media.overview,
                "vote_average": vote_average_for(media),
                "genres": genres_for(media),
            },
            match_attempted_at=now,
        )
        updated += 1

    return RefreshMetadataResponse(updated=updated, failed=failed)


@router.get("/tv-status", response_model=TvStatusOut)
def tv_status(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb_client)) -> TvStatusOut:
    """Live TMDB season/episode counts for the detail pane's "new season
    available" banner -- deliberately independent of the archive_tracker
    table (which is an opt-in, separately-added watchlist for the
    Notifications tab); this just answers "is there more of this show than
    what's in the library" for whatever show is currently open, no tracking
    side effect. Scraper mode returns an empty MediaResult.raw, so
    data_available naturally comes back false and the pane shows no banner
    rather than a wrong one.
    """
    media = tmdb.get_tv_details(tmdb_id)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No TMDB details found for tmdb_id {tmdb_id}")

    # season_number 0 is TMDB's "Specials" bucket -- never counted as a gap,
    # same reason latest_known_season/total_episodes never include it.
    seasons = [
        TvSeasonSummaryOut(season_number=num, episode_count=count)
        for num, count in season_episode_counts(media).items()
        if num != 0
    ]

    return TvStatusOut(
        tmdb_id=tmdb_id,
        status=media.raw.get("status"),
        latest_known_season=media.raw.get("number_of_seasons"),
        latest_season_episode_count=media.raw.get("latest_season_episode_count"),
        total_episodes=media.raw.get("number_of_episodes"),
        data_available=media.source == "api",
        seasons=seasons,
    )


@router.get("/movie-status", response_model=MovieStatusOut)
def movie_status(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb_client)) -> MovieStatusOut:
    """Movie counterpart of /tv-status: same "new content available" badge
    concept, but for movies that's a collection (sequel/prequel), not a
    season. Returns every other movie in the same TMDB collection; the
    frontend cross-references against the already-archived library to
    decide which of those are actually missing, since this endpoint has no
    DB access of its own -- same self-contained, tracker-independent design
    as tv_status above.
    """
    media = tmdb.get_movie_details(tmdb_id)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No TMDB details found for tmdb_id {tmdb_id}")
    collection_id = (media.raw.get("belongs_to_collection") or {}).get("id")
    related = []
    if collection_id:
        related = [
            MovieRelatedTitleOut(tmdb_id=m.tmdb_id, title=m.title, year=m.year)
            for m in tmdb.get_collection_movies(collection_id)
            if m.tmdb_id != tmdb_id
        ]
    return MovieStatusOut(
        tmdb_id=tmdb_id,
        collection_id=collection_id,
        related=related,
        data_available=media.source == "api",
    )


@router.post("/{item_id}/watched", response_model=LibraryItemOut)
def set_watched(item_id: int, payload: WatchedUpdateRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    db.update_media_item(item_id, watched=1 if payload.watched else 0)
    return _to_out(db.get_media_item(item_id))


@router.post("/watched-batch", response_model=WatchedBatchResponse)
def set_watched_batch(payload: WatchedBatchRequest, db: Database = Depends(get_database)) -> WatchedBatchResponse:
    updated = 0
    for item_id in payload.ids:
        if db.get_media_item(item_id) is None:
            continue
        db.update_media_item(item_id, watched=1 if payload.watched else 0)
        updated += 1
    return WatchedBatchResponse(updated=updated)


@router.post("/rematch-imdb", response_model=RematchResponse)
def rematch_by_imdb_id(
    payload: RematchImdbRequest,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RematchResponse:
    """Manual-match path for the detail pane: a title the automatic
    search/backfill couldn't identify (tmdb_id null) or matched wrong. One
    IMDb-id lookup, applied to every id in `ids` -- a TV show's IMDb id
    identifies the whole series, so the caller passes every episode row
    for that show, not just one. The imdb_id is already known here (the
    user typed it), so it's persisted directly -- no need for the ratings
    endpoint to later re-derive it via a TMDB external_ids call.
    """
    media = tmdb.find_by_imdb_id(payload.imdb_id.strip(), payload.media_type)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No TMDB match found for IMDb id {payload.imdb_id!r}")

    now = datetime.now(timezone.utc).isoformat()
    updated = _apply_rematch(db, payload.ids, media, now, imdb_id=payload.imdb_id.strip())

    return RematchResponse(
        updated=updated,
        tmdb_id=media.tmdb_id,
        title=media.title,
        year=media.year,
        poster_path=media.poster_path,
        overview=media.overview,
    )


@router.post("/rematch-tmdb", response_model=RematchResponse)
def rematch_by_tmdb_id(
    payload: RematchTmdbRequest,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RematchResponse:
    """Manual-match path for the detail pane's "Change Match" search picker
    (same candidate list as the archive preview table's picker) -- for
    fixing a title the automatic search matched to the wrong TMDB entry.
    Applied to every id in `ids`, same fan-out as rematch-imdb.
    """
    media = tmdb.get_tv_details(payload.tmdb_id) if payload.media_type == "tv" else tmdb.get_movie_details(payload.tmdb_id)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No TMDB details found for tmdb_id {payload.tmdb_id}")

    now = datetime.now(timezone.utc).isoformat()
    updated = _apply_rematch(db, payload.ids, media, now, imdb_id=None)

    return RematchResponse(
        updated=updated,
        tmdb_id=media.tmdb_id,
        title=media.title,
        year=media.year,
        poster_path=media.poster_path,
        overview=media.overview,
    )


def _apply_rematch(db: Database, ids: list[int], media: MediaResult, now: str, imdb_id: str | None) -> int:
    updated = 0
    for item_id in ids:
        row = db.get_media_item(item_id)
        if row is None:
            continue
        existing_meta = _metadata_dict(row)
        fields = dict(
            tmdb_id=media.tmdb_id,
            title=media.title,
            year=media.year,
            metadata={
                # width/height/video_codec are ffprobe results cached by
                # get_file_info, not TMDB data -- a rematch shouldn't wipe
                # them out, so anything already probed carries forward.
                "width": existing_meta.get("width"),
                "height": existing_meta.get("height"),
                "video_codec": existing_meta.get("video_codec"),
                "poster_path": media.poster_path,
                "overview": media.overview,
                "vote_average": vote_average_for(media),
                "genres": genres_for(media),
            },
            match_attempted_at=now,
            manual_override=0,
        )
        if imdb_id is not None:
            fields["imdb_id"] = imdb_id
        db.update_media_item(item_id, **fields)
        updated += 1
    return updated


@router.post("/{item_id}/override", response_model=LibraryItemOut)
def set_manual_override(item_id: int, payload: ManualOverrideRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    """Gives an item a title/year with no TMDB match at all -- for something
    genuinely not on TMDB (a home video, a fan edit, a rip TMDB doesn't list).
    Sets manual_override so the metadata backfill never overwrites it once
    its match_attempted_at cooldown lapses, and clears any previous tmdb_id
    so ratings/rematch don't act on a stale id that no longer describes it.
    """
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")

    now = datetime.now(timezone.utc).isoformat()
    db.update_media_item(
        item_id,
        title=payload.title,
        year=payload.year,
        tmdb_id=None,
        imdb_id=None,
        manual_override=1,
        match_attempted_at=now,
    )
    return _to_out(db.get_media_item(item_id))


@router.get("/{item_id}/ratings", response_model=RatingsOut)
def get_ratings(
    item_id: int,
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
) -> RatingsOut:
    """IMDb rating + Rotten Tomatoes score for the detail pane, via OMDb
    (optional -- omdb_configured tells the frontend whether to show a
    "no key configured" hint instead of "unavailable"). The imdb_id is
    resolved from tmdb_id on first request and cached on the row so a
    repeat pane-open doesn't need another TMDB round trip.
    """
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")

    imdb_id = item["imdb_id"]
    if imdb_id is None and item["tmdb_id"] is not None:
        imdb_id = tmdb.get_external_imdb_id(item["tmdb_id"], item["media_type"])
        if imdb_id:
            db.update_media_item(item_id, imdb_id=imdb_id)

    if not omdb.enabled or imdb_id is None:
        return RatingsOut(imdb_id=imdb_id, omdb_configured=omdb.enabled)

    ratings = omdb.get_ratings(imdb_id)
    if ratings is None:
        return RatingsOut(imdb_id=imdb_id, omdb_configured=True)
    return RatingsOut(
        imdb_id=imdb_id,
        imdb_rating=ratings.imdb_rating,
        imdb_votes=ratings.imdb_votes,
        rotten_tomatoes=ratings.rotten_tomatoes,
        metacritic=ratings.metacritic,
        omdb_configured=True,
    )


@router.get("/{item_id}/trailer", response_model=TrailerOut)
def get_trailer(
    item_id: int, db: Database = Depends(get_database), tmdb: TMDBClient = Depends(get_tmdb_client)
) -> TrailerOut:
    """YouTube trailer key for the detail pane's "Watch Trailer" link --
    API-key-only (see TMDBClient.get_trailer_key), so tmdb_configured tells
    the frontend whether to show "no TMDB API key configured" instead of
    "no trailer found" when youtube_key comes back empty."""
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item["tmdb_id"] is None:
        return TrailerOut(youtube_key=None, tmdb_configured=tmdb.mode == "api")

    key = tmdb.get_trailer_key(item["tmdb_id"], item["media_type"])
    return TrailerOut(youtube_key=key, tmdb_configured=tmdb.mode == "api")


@router.get("/{item_id}/more-info", response_model=MoreInfoOut)
def get_more_info(
    item_id: int, db: Database = Depends(get_database), tmdb: TMDBClient = Depends(get_tmdb_client)
) -> MoreInfoOut:
    """Top-billed cast and similar titles for the detail pane's discovery
    section -- both API-key-only (see TMDBClient.get_cast/get_similar_titles),
    combined into one call so opening the pane doesn't fire two separate
    TMDB requests for what's shown together."""
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item["tmdb_id"] is None:
        return MoreInfoOut(tmdb_configured=tmdb.mode == "api")

    cast = tmdb.get_cast(item["tmdb_id"], item["media_type"])
    similar = tmdb.get_similar_titles(item["tmdb_id"], item["media_type"])
    return MoreInfoOut(
        cast=[CastMemberOut(**c) for c in cast],
        similar=[
            SimilarTitleOut(tmdb_id=s.tmdb_id, title=s.title, year=s.year, poster_path=s.poster_path) for s in similar
        ],
        tmdb_configured=tmdb.mode == "api",
    )


def _generate_movie_note(item_id: int, db: Database, omdb: OMDbClient) -> tuple[str, str]:
    """Returns (markdown_text, filename). Shared by the download and
    save-to-folder routes below so they can never drift out of sync with
    each other. Movies only -- the Obsidian Media DB plugin's frontmatter
    shape for a TV show/episode is different enough (season/episode
    fields, a show-level vs episode-level note) that reusing this
    movie-shaped template for TV would just produce a note the plugin
    doesn't recognize correctly.
    """
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item["media_type"] != "movie":
        raise HTTPException(status_code=400, detail="Notes are only generated for movies")

    meta = _metadata_dict(item)
    poster_path = meta.get("poster_path")
    tmdb_poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else ""

    omdb_data = omdb.get_full_details(item["imdb_id"]) if item["imdb_id"] else None

    markdown = build_movie_note(
        title=item["title"],
        year=item["year"],
        imdb_id=item["imdb_id"],
        tmdb_id=item["tmdb_id"],
        watched=bool(item["watched"]),
        tmdb_overview=meta.get("overview", ""),
        tmdb_genres=meta.get("genres") or [],
        tmdb_poster_url=tmdb_poster_url,
        tmdb_vote_average=meta.get("vote_average"),
        omdb=omdb_data,
    )
    display_title = (omdb_data.title if omdb_data else item["title"]) or item["title"]
    base_name = f"{display_title} ({item['year']})" if item["year"] else display_title
    filename = f"{sanitize_filename(base_name)}.md"
    return markdown, filename


def _content_disposition(filename: str) -> str:
    """HTTP headers are Latin-1 only -- a title with an en dash, curly
    quote, or any non-Latin1 character (all routine in a real TV/movie
    title) would otherwise raise inside Starlette's own header encoding
    and 500 the whole download. filename= carries an ASCII-safe fallback
    for older clients; filename*= (RFC 5987/6266) carries the real UTF-8
    name, which every current browser prefers when both are present."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@router.get("/{item_id}/note")
def download_movie_note(
    item_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> Response:
    """Downloads the generated note without touching the archive folder --
    for saving it anywhere the user wants (e.g. an existing Obsidian vault
    outside this app's own media paths)."""
    markdown, filename = _generate_movie_note(item_id, db, omdb)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/{item_id}/note/save", response_model=NoteSaveResponse)
def save_movie_note(
    item_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> NoteSaveResponse:
    """Writes the generated note directly into the movie's own archive
    folder, alongside the video file -- for a vault that watches the
    library's own folders rather than a separate notes directory."""
    markdown, filename = _generate_movie_note(item_id, db, omdb)
    item = db.get_media_item(item_id)
    if not item["final_path"]:
        raise HTTPException(status_code=400, detail="This item has no archived file on disk")
    folder = Path(item["final_path"]).parent
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Movie folder no longer exists: {folder}")
    dest = folder / filename
    try:
        dest.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write note: {exc}") from exc
    return NoteSaveResponse(path=str(dest))


def _generate_tv_note(tmdb_id: int, db: Database, omdb: OMDbClient) -> tuple[str, str, list[dict]]:
    """Show-level counterpart of _generate_movie_note. Keyed by tmdb_id,
    not a single media_items row: a show is however many per-episode rows
    share that tmdb_id, so this aggregates across all of them (episode
    count, whether every owned episode is watched, the highest season with
    a watched episode) before building one note for the whole show.
    Returns (markdown_text, filename, episode_rows) -- the caller needs
    episode_rows too, to find the show's own folder for the save route.
    """
    episodes = [r for r in db.list_media_items(media_type="tv") if r["tmdb_id"] == tmdb_id]
    if not episodes:
        raise HTTPException(status_code=404, detail="No episodes found for this show")

    first = episodes[0]
    meta = _metadata_dict(first)
    poster_path = meta.get("poster_path")
    tmdb_poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else ""

    watched = all(bool(r["watched"]) for r in episodes)
    watched_seasons = [r["season_number"] for r in episodes if r["watched"] and r["season_number"] is not None]
    last_watched_season = max(watched_seasons) if watched_seasons else None

    omdb_data = omdb.get_full_details(first["imdb_id"]) if first["imdb_id"] else None

    markdown = build_tv_note(
        title=first["title"],
        imdb_id=first["imdb_id"],
        tmdb_id=tmdb_id,
        watched=watched,
        episode_count=len(episodes),
        last_watched_season=last_watched_season,
        tmdb_overview=meta.get("overview", ""),
        tmdb_genres=meta.get("genres") or [],
        tmdb_poster_url=tmdb_poster_url,
        tmdb_vote_average=meta.get("vote_average"),
        omdb=omdb_data,
    )
    display_title = (omdb_data.title if omdb_data else first["title"]) or first["title"]
    year_field = omdb_data.year if omdb_data else None
    base_name = f"{display_title} ({year_field})" if year_field else display_title
    filename = f"{sanitize_filename(base_name)}.md"
    return markdown, filename, episodes


@router.get("/tv-shows/{tmdb_id}/note")
def download_tv_note(
    tmdb_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> Response:
    """TV counterpart of download_movie_note -- one note for the whole
    show, aggregated across every archived episode sharing this tmdb_id."""
    markdown, filename, _ = _generate_tv_note(tmdb_id, db, omdb)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/tv-shows/{tmdb_id}/note/save", response_model=NoteSaveResponse)
def save_tv_note(
    tmdb_id: int, db: Database = Depends(get_database), omdb: OMDbClient = Depends(get_omdb_client)
) -> NoteSaveResponse:
    """Writes the show-level note into the show's own folder -- two levels
    up from any episode's file (Show/Season NN/episode.ext), not the
    season folder itself, so it sits alongside the show as a whole rather
    than inside whichever season happened to be picked."""
    markdown, filename, episodes = _generate_tv_note(tmdb_id, db, omdb)
    episode_with_file = next((e for e in episodes if e["final_path"]), None)
    if episode_with_file is None:
        raise HTTPException(status_code=400, detail="This show has no archived episodes on disk")
    folder = Path(episode_with_file["final_path"]).parent.parent
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Show folder no longer exists: {folder}")
    dest = folder / filename
    try:
        dest.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write note: {exc}") from exc
    return NoteSaveResponse(path=str(dest))


@router.get("/{item_id}/file-info", response_model=FileInfoOut)
def get_file_info(item_id: int, db: Database = Depends(get_database)) -> FileInfoOut:
    """File name/size (cheap, from the filesystem) plus duration/resolution/
    codec/bitrate via ffprobe (best-effort -- probe_available tells the
    frontend whether to show "ffprobe not installed" instead of blank
    fields). Deliberately lazy/on-demand rather than baked into the gallery
    list response: probing every episode of a show up front would mean one
    subprocess call per file on every load.
    """
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if not item["final_path"]:
        raise HTTPException(status_code=404, detail="This item has no archived file on disk")

    path = Path(item["final_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File no longer exists at {path}")

    size_bytes = path.stat().st_size
    probe = media_probe.probe_file(path)

    if probe is not None:
        # Cached on the row so the resolution/HDR/audio-channel filters and
        # badges don't need to re-probe the file (a subprocess call) on
        # every gallery load -- only the first detail-pane open (or
        # file-info fetch) pays that cost.
        meta = _metadata_dict(item)
        meta.update(
            width=probe.width, height=probe.height, video_codec=probe.video_codec,
            hdr=probe.hdr, audio_channels=probe.audio_channels,
        )
        db.update_media_item(item_id, metadata=meta)

    return FileInfoOut(
        file_name=path.name,
        path=str(path),
        size_bytes=size_bytes,
        duration_seconds=probe.duration_seconds if probe else None,
        width=probe.width if probe else None,
        height=probe.height if probe else None,
        video_codec=probe.video_codec if probe else None,
        audio_codec=probe.audio_codec if probe else None,
        hdr=probe.hdr if probe else False,
        audio_channels=probe.audio_channels if probe else None,
        bitrate=probe.bitrate if probe else None,
        container=probe.container if probe else None,
        probe_available=media_probe.ffprobe_available(),
    )


@router.post("/organize", response_model=ArchiveConfirmResponse)
def organize_selected(
    payload: ArchiveConfirmRequest, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)
) -> ArchiveConfirmResponse:
    """Stage 2 of the manual library browser: given TMDB-matched preview
    items (from the same /api/archive/preview used by "Ready to Archive"),
    moves each file to its computed destination in place and updates its
    existing media_items row -- unlike /api/archive/confirm, which always
    copies to a new location and always inserts a new row. Reuses the
    archive preview/confirm request-response shapes since the data is
    identical; only what happens to the filesystem and the DB differs.
    """
    results: list[ArchiveConfirmResult] = []
    for item in payload.items:
        source = Path(item.source_path)
        dest = Path(item.dest_path)

        if payload.dry_run:
            results.append(_dry_run_result(source, dest))
            continue

        plan = RenamePlan(
            source_path=source,
            dest_path=dest,
            media_type=item.media_type,
            tmdb_id=item.tmdb_id,
            title=item.title,
            year=item.year,
            season=item.season,
            episode=item.episode,
            poster_path=item.poster_path,
            overview=item.overview,
            vote_average=item.vote_average,
            genres=item.genres,
        )
        try:
            media_id = organize_file(db, plan, write_nfo_files=config.media_server.write_nfo_files)
            maybe_auto_track(
                db, config.tracker.auto_track_new, item.tmdb_id, item.media_type, item.title, item.season
            )
            results.append(
                ArchiveConfirmResult(source_path=str(source), dest_path=str(dest), media_id=media_id, status="success")
            )
        except OrganizeError as exc:
            results.append(ArchiveConfirmResult(source_path=str(source), status="failed", error=str(exc)))

    if not payload.dry_run and any(r.status == "success" for r in results):
        notify_media_servers(config)

    return ArchiveConfirmResponse(results=results)


@router.get("/browse", response_model=BrowseResponse)
def browse_archive(
    media_type: MediaType,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BrowseResponse:
    """Lists every video file physically present in the archive root for
    `media_type`, tracked in media_items or not -- unlike /api/scan (which
    only surfaces new, untracked files) and /api/library/movies|tv (which
    only reflects what's in the database), this is a raw filesystem view
    meant for manual cleanup of a library that already existed before (or
    outside) this app, e.g. Radarr/Sonarr-managed files never archived
    through here.
    """
    root = config.paths.archive_movies if media_type == "movie" else config.paths.archive_tv
    scanned = scan_directory(root)

    items = []
    for f in scanned:
        tracked_row = db.get_media_item_by_final_path(str(f.path))
        items.append(
            BrowseItemOut(
                path=str(f.path),
                size_bytes=f.size_bytes,
                parsed_title=f.parsed.title,
                year=f.parsed.year,
                season=f.parsed.season,
                episode=f.parsed.episode,
                tracked=tracked_row is not None,
                media_id=tracked_row["id"] if tracked_row else None,
                tmdb_id=tracked_row["tmdb_id"] if tracked_row else None,
                watched=bool(tracked_row["watched"]) if tracked_row else False,
            )
        )
    return BrowseResponse(directory=str(root), items=items)


def _resolve_and_validate_target(path: str, config: AppConfig) -> Path:
    """Only allowed inside a configured incoming/archive root -- a guard
    against a client bug or bad request touching anything outside the
    library, since delete is the one genuinely destructive action here."""
    target = Path(path).resolve()
    allowed_roots = [
        config.paths.incoming_movies,
        config.paths.incoming_tv,
        config.paths.archive_movies,
        config.paths.archive_tv,
    ]
    if not any(target == root.resolve() or root.resolve() in target.parents for root in allowed_roots):
        raise ValueError("Path is outside the configured media directories")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("File not found")
    return target


def _delete_target(target: Path, db: Database, config: AppConfig) -> None:
    # Log while media_id still references a real row -- operation_log.media_id
    # has a foreign key, and deleting the media_items row first would make a
    # log entry pointing at it fail that constraint.
    tracked_row = db.get_media_item_by_final_path(str(target))
    try:
        target.unlink()
    except OSError as exc:
        db.log_operation(
            operation_type="delete",
            status="failed",
            media_id=tracked_row["id"] if tracked_row else None,
            error_message=str(exc),
            details={"path": str(target)},
        )
        raise

    _cleanup_siblings_and_folder(target, config)

    db.log_operation(
        operation_type="delete",
        status="success",
        media_id=tracked_row["id"] if tracked_row else None,
        details={"path": str(target)},
    )
    if tracked_row:
        db.delete_media_item(tracked_row["id"])
    logger.info("Deleted %s (was tracked: %s)", target, bool(tracked_row))


def _cleanup_siblings_and_folder(deleted_video: Path, config: AppConfig) -> None:
    """After deleting a video, also removes its own subtitle files and --
    only once no video remains in the folder, since a TV season folder
    holds multiple episodes -- the poster/nfo written alongside it, then
    the folder itself if that leaves it empty. Best-effort throughout: a
    failure here doesn't undo the video deletion that already succeeded.
    Never touches the configured incoming/archive roots themselves --
    only subfolders within them (a movie's own folder, a TV season folder).
    """
    folder = deleted_video.parent
    if not folder.is_dir():
        return

    protected_roots = {
        config.paths.incoming_movies.resolve(),
        config.paths.incoming_tv.resolve(),
        config.paths.archive_movies.resolve(),
        config.paths.archive_tv.resolve(),
    }
    is_protected_root = folder.resolve() in protected_roots

    for sub in folder.glob(f"{deleted_video.stem}*"):
        if sub.is_file() and sub.suffix.lower() in SUBTITLE_EXTENSIONS:
            try:
                sub.unlink()
            except OSError as exc:
                logger.warning("Failed to remove sibling subtitle %s: %s", sub, exc)

    remaining_videos = any(
        p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS for p in folder.iterdir()
    )
    if not remaining_videos and not is_protected_root:
        for name in ARTWORK_NAMES:
            artwork = folder / name
            if artwork.exists():
                try:
                    artwork.unlink()
                except OSError as exc:
                    logger.warning("Failed to remove leftover artwork %s: %s", artwork, exc)
        try:
            folder.rmdir()
        except OSError:
            pass  # not empty (something else still in there), or already gone


@router.post("/delete-file")
def delete_file(
    payload: DeleteFileRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> dict:
    """Permanently deletes a single file from disk."""
    try:
        target = _resolve_and_validate_target(payload.path, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        _delete_target(target, db, config)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {exc}") from exc
    return {"deleted": True}


@router.post("/delete-batch", response_model=DeleteBatchResponse)
def delete_batch(
    payload: DeleteBatchRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> DeleteBatchResponse:
    """Bulk version of delete-file for the gallery/Browse "Delete Selected"
    action -- one bad path in the batch (outside the media dirs, already
    gone, a permissions error) is reported per-path rather than aborting
    the rest of the selection.
    """
    deleted = 0
    errors: list[str] = []
    for path in payload.paths:
        try:
            target = _resolve_and_validate_target(path, config)
            _delete_target(target, db, config)
            deleted += 1
        except (ValueError, FileNotFoundError, OSError) as exc:
            errors.append(f"{path}: {exc}")
    return DeleteBatchResponse(deleted=deleted, errors=errors)
