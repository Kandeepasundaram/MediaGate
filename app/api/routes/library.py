"""Read/browse the archived library and toggle manual watch state.

Distinct from /api/scan (which finds new, unarchived files): this reflects
whatever this app has itself archived into media_items, for the gallery
views and manual watch tracking that are Media Manager's own job alongside
Radarr/Sonarr's automated import pipeline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.archive import _dry_run_result
from app.config_loader import AppConfig
from app.core import media_probe
from app.core.library_adopt import adopt_new_files
from app.core.omdb_client import OMDbClient
from app.core.orphan_artwork import cleanup_orphaned_artwork, find_orphaned_artwork
from app.core.organizer import OrganizeError, organize_file
from app.core.renamer import RenamePlan
from app.core.scanner import scan_directory
from app.core.media_server import notify_media_servers
from app.core.tmdb_client import MediaResult, TMDBClient, genres_for, vote_average_for
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
    RetryFailedMatchesResponse,
    RematchImdbRequest,
    RematchResponse,
    RematchTmdbRequest,
    CastMemberOut,
    MoreInfoOut,
    SimilarTitleOut,
    TrailerOut,
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


def _to_out(row: dict) -> LibraryItemOut:
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
    )


@router.get("/movies", response_model=LibraryResponse)
def list_movies(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> LibraryResponse:
    """Auto-adopts (registers, no file operations, no network calls) any
    file physically in archive_movies not yet tracked, then returns
    everything tracked -- so files already organized by Radarr/Sonarr show
    up here without needing to be manually run through Ready to Archive.
    Newly-adopted rows show with a placeholder poster until the background
    metadata backfill (see /metadata-status) fills them in.
    """
    adopt_new_files(db, config, "movie")
    return LibraryResponse(items=[_to_out(r) for r in db.list_media_items(media_type="movie")])


@router.get("/tv", response_model=LibraryResponse)
def list_tv(config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> LibraryResponse:
    adopt_new_files(db, config, "tv")
    return LibraryResponse(items=[_to_out(r) for r in db.list_media_items(media_type="tv")])


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
    return TvStatusOut(
        tmdb_id=tmdb_id,
        status=media.raw.get("status"),
        latest_known_season=media.raw.get("number_of_seasons"),
        latest_season_episode_count=media.raw.get("latest_season_episode_count"),
        total_episodes=media.raw.get("number_of_episodes"),
        data_available=media.source == "api",
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
            media_id = organize_file(db, plan)
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


def _delete_target(target: Path, db: Database) -> None:
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

    db.log_operation(
        operation_type="delete",
        status="success",
        media_id=tracked_row["id"] if tracked_row else None,
        details={"path": str(target)},
    )
    if tracked_row:
        db.delete_media_item(tracked_row["id"])
    logger.info("Deleted %s (was tracked: %s)", target, bool(tracked_row))


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
        _delete_target(target, db)
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
            _delete_target(target, db)
            deleted += 1
        except (ValueError, FileNotFoundError, OSError) as exc:
            errors.append(f"{path}: {exc}")
    return DeleteBatchResponse(deleted=deleted, errors=errors)
