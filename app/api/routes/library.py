"""Read/browse the archived library and toggle manual watch state.

Distinct from /api/scan (which finds new, unarchived files): this reflects
whatever this app has itself archived into media_items, for the gallery
views and manual watch tracking that are Media Manager's own job alongside
Radarr/Sonarr's automated import pipeline.

Split into four route modules, all mounted at the same /api/library prefix
(see main.py): this one (gallery listing, tags, viewers, watch state,
detail-pane lookups), library_notes.py (Obsidian note generation),
library_maintenance.py (export/import, health, orphan cleanup, bulk
refresh), and library_browse.py (raw-filesystem browse/organize/delete).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.library_common import _metadata_dict, _tags_list, _to_out
from app.config_loader import AppConfig
from app.core import media_probe
from app.core.library_adopt import adopt_new_files
from app.core.omdb_client import OMDbClient
from app.core.media_server import (
    get_plex_deep_link,
    jellyfin_item_id_for_imdb,
    list_jellyfin_sessions,
    play_on_jellyfin_session,
    sync_watched_from_media_servers,
)
from app.core.tmdb_client import MediaResult, TMDBClient, genres_for, season_episode_counts, vote_average_for
from app.core.tvmaze_client import TVmazeClient
from app.database import Database
from app.dependencies import get_config, get_database, get_omdb_client, get_tmdb_client, get_tvmaze_client
from app.models import (
    BackdropOut,
    CastMemberOut,
    FileInfoOut,
    JellyfinSessionsResponse,
    PlayLinkOut,
    PlayOnJellyfinRequest,
    PlayOnJellyfinResponse,
    LibraryItemOut,
    LibraryResponse,
    ManualOverrideRequest,
    MediaType,
    MetadataStatusResponse,
    MoreInfoOut,
    MovieRelatedTitleOut,
    MovieStatusOut,
    PersonalRatingUpdateRequest,
    PersonCreditItemOut,
    PersonCreditsResponse,
    RatingsOut,
    RecommendationOut,
    RecommendationsResponse,
    RematchImdbRequest,
    RematchResponse,
    RematchTmdbRequest,
    SimilarTitleOut,
    SyncWatchedResponse,
    TagDeleteRequest,
    TagRenameRequest,
    WatchHistoryImportRequest,
    WatchHistoryImportResponse,
    TagsBatchRequest,
    TagsBatchResponse,
    TagsListResponse,
    TagsUpdateRequest,
    TrailerOut,
    TvEpisodeOut,
    TvLibraryResponse,
    TvSeasonEpisodesOut,
    TvSeasonSummaryOut,
    TvShowStatusUpdateRequest,
    TvShowSummaryOut,
    TvStatusOut,
    ViewerCreateRequest,
    ViewerOut,
    ViewersListResponse,
    ViewerWatchedUpdateRequest,
    WatchedBatchRequest,
    WatchedBatchResponse,
    WatchedUpdateRequest,
)

router = APIRouter(prefix="/api/library", tags=["library"])


def _watched_at_for(watched: bool) -> str | None:
    """Current timestamp when marking watched, None (clears any prior
    value) when marking unwatched -- so watched_at always reflects the
    current watched state instead of accumulating a stale date from a
    previous watch/unwatch cycle. See the Reports page's watch-activity
    query, the only reader of this column."""
    return datetime.now(timezone.utc).isoformat() if watched else None


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

    all_shows = db.list_tv_shows()
    status_by_tmdb_id = {s["tmdb_id"]: s["status"] for s in all_shows}
    personal_by_tmdb_id = {s["tmdb_id"]: (s["personal_rating"], s["personal_note"]) for s in all_shows}
    items = [
        _to_out(r, watched_ids, status_by_tmdb_id.get(r["tmdb_id"]), personal_by_tmdb_id.get(r["tmdb_id"]))
        for r in rows
    ]

    orphaned_shows = [
        TvShowSummaryOut(
            tmdb_id=s["tmdb_id"], title=s["title"], imdb_id=s["imdb_id"], poster_path=s["poster_path"],
            overview=s["overview"] or "", genres=json.loads(s["genres"]) if s["genres"] else [], status=s["status"],
            personal_rating=s["personal_rating"], personal_note=s["personal_note"],
        )
        for s in all_shows if s["tmdb_id"] not in present_tmdb_ids
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
        personal_rating=show["personal_rating"], personal_note=show["personal_note"],
    )


def _validate_rating(rating: int | None) -> None:
    if rating is not None and not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be 1-5, or null to clear it")


@router.post("/{item_id}/personal", response_model=LibraryItemOut)
def set_personal(item_id: int, payload: PersonalRatingUpdateRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    """Movie personal rating/note -- lives on the media_items row itself,
    unlike a TV show's (see set_tv_show_personal below), since one movie
    is one row."""
    _validate_rating(payload.rating)
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    db.update_media_item(item_id, personal_rating=payload.rating, personal_note=payload.note)
    return _to_out(db.get_media_item(item_id))


@router.post("/tv-shows/{tmdb_id}/personal", response_model=TvShowSummaryOut)
def set_tv_show_personal(tmdb_id: int, payload: PersonalRatingUpdateRequest, db: Database = Depends(get_database)) -> TvShowSummaryOut:
    _validate_rating(payload.rating)
    show = db.get_tv_show(tmdb_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not tracked yet -- open its TV tab entry first")
    db.set_tv_show_personal(tmdb_id, payload.rating, payload.note)
    show = db.get_tv_show(tmdb_id)
    return TvShowSummaryOut(
        tmdb_id=show["tmdb_id"], title=show["title"], imdb_id=show["imdb_id"], poster_path=show["poster_path"],
        overview=show["overview"] or "", genres=json.loads(show["genres"]) if show["genres"] else [], status=show["status"],
        personal_rating=show["personal_rating"], personal_note=show["personal_note"],
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
    seed_titles: dict[int, str] = {}
    for row in sorted(rows, key=lambda r: r["archived_at"] or "", reverse=True):
        if row["tmdb_id"] not in seed_ids:
            seed_ids.append(row["tmdb_id"])
            seed_titles[row["tmdb_id"]] = row["title"]
        if len(seed_ids) >= 15:
            break

    scores: dict[int, dict] = {}
    for seed_id in seed_ids:
        for candidate in tmdb.get_similar_titles(seed_id, media_type):
            if candidate.tmdb_id is None or candidate.tmdb_id in owned_tmdb_ids:
                continue
            # setdefault only fires on the *first* seed to surface this
            # candidate; since seed_ids is walked most-recently-archived
            # first, because_of ends up naming the most recently archived
            # owned title responsible -- the most useful "because you have
            # X" attribution when several seeds point at the same candidate.
            entry = scores.setdefault(
                candidate.tmdb_id,
                {
                    "title": candidate.title, "year": candidate.year, "poster_path": candidate.poster_path,
                    "score": 0, "because_of": seed_titles[seed_id],
                },
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


@router.get("/tv-status", response_model=TvStatusOut)
def tv_status(
    tmdb_id: int,
    tmdb: TMDBClient = Depends(get_tmdb_client),
    tvmaze: TVmazeClient = Depends(get_tvmaze_client),
) -> TvStatusOut:
    """Live TMDB season/episode counts for the detail pane's "new season
    available" banner -- deliberately independent of the archive_tracker
    table (which is an opt-in, separately-added watchlist for the
    Notifications tab); this just answers "is there more of this show than
    what's in the library" for whatever show is currently open, no tracking
    side effect. Scraper mode returns an empty MediaResult.raw, so
    data_available naturally comes back false and the pane shows no banner
    rather than a wrong one.

    When TVmaze is enabled it's queried unconditionally (not just as a
    scraper-mode fallback -- see tvmaze_client.py's module docstring) and
    preferred for `status`/next-episode fields when it has an answer, since
    it's the newer, richer signal; TMDB's own fields (already used before
    TVmaze existed) fill in whatever TVmaze didn't return. `network` has no
    TMDB equivalent in this endpoint, so it's TVmaze-only.
    """
    media = tmdb.get_tv_details(tmdb_id)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No TMDB details found for tmdb_id {tmdb_id}")

    show_info = None
    tvmaze_id = None
    if tvmaze.enabled:
        imdb_id = tmdb.get_external_imdb_id(tmdb_id, "tv")
        if imdb_id:
            tvmaze_id = tvmaze.lookup_show_id_by_imdb(imdb_id)
            if tvmaze_id is not None:
                show_info = tvmaze.get_show_info(tvmaze_id)

    # Aired-episode count per season, as of today -- only available via
    # TVmaze (TMDB's per-season episode_count doesn't distinguish aired from
    # scheduled-but-not-yet-aired within a season still being released).
    aired_by_season: dict[int, int] = {}
    if tvmaze_id is not None:
        today = date.today().isoformat()
        for ep in tvmaze.get_episodes(tvmaze_id):
            if ep.air_date and ep.air_date <= today:
                aired_by_season[ep.season] = aired_by_season.get(ep.season, 0) + 1

    # season_number 0 is TMDB's "Specials" bucket -- never counted as a gap,
    # same reason latest_known_season/total_episodes never include it.
    seasons = [
        TvSeasonSummaryOut(season_number=num, episode_count=count, aired_count=aired_by_season.get(num))
        for num, count in season_episode_counts(media).items()
        if num != 0
    ]

    next_episode_to_air = media.raw.get("next_episode_to_air") or {}
    next_season = next_episode_to_air.get("season_number")
    next_number = next_episode_to_air.get("episode_number")
    tmdb_next_code = (
        f"S{next_season:02d}E{next_number:02d}" if next_season is not None and next_number is not None else None
    )

    return TvStatusOut(
        tmdb_id=tmdb_id,
        status=(show_info.status if show_info else None) or media.raw.get("status"),
        latest_known_season=media.raw.get("number_of_seasons"),
        latest_season_episode_count=media.raw.get("latest_season_episode_count"),
        total_episodes=media.raw.get("number_of_episodes"),
        data_available=media.source == "api" or show_info is not None,
        seasons=seasons,
        network=show_info.network if show_info else None,
        next_episode_air_date=(show_info.next_episode_air_date if show_info else None)
        or next_episode_to_air.get("air_date"),
        next_episode_code=(show_info.next_episode_code if show_info else None) or tmdb_next_code,
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


@router.get("/tv-season", response_model=TvSeasonEpisodesOut)
def tv_season_episodes(
    tmdb_id: int,
    season_number: int,
    tmdb: TMDBClient = Depends(get_tmdb_client),
    tvmaze: TVmazeClient = Depends(get_tvmaze_client),
) -> TvSeasonEpisodesOut:
    """Per-episode name/air-date detail for one season, for the detail
    pane's season-pills UI -- distinct from /tv-status, which only has
    per-season counts. TMDB's season-detail endpoint (API-key-only) is
    tried first; TVmaze's full episode list (already fetched by /tv-status
    for aired-counts, but with names/dates discarded there) fills in when
    TMDB has nothing, same fallback role it plays in /tv-status. Self-contained
    and tmdb_id-only, same as /tv-status/movie-status -- works for a
    tracker row with no media_items row behind it.
    """
    episodes = tmdb.get_season_episodes(tmdb_id, season_number)
    if episodes:
        return TvSeasonEpisodesOut(
            season_number=season_number,
            episodes=[TvEpisodeOut(**e) for e in episodes],
            data_available=True,
        )

    if tvmaze.enabled:
        imdb_id = tmdb.get_external_imdb_id(tmdb_id, "tv")
        tvmaze_id = tvmaze.lookup_show_id_by_imdb(imdb_id) if imdb_id else None
        if tvmaze_id is not None:
            season_episodes = sorted(
                (e for e in tvmaze.get_episodes(tvmaze_id) if e.season == season_number),
                key=lambda e: e.episode,
            )
            if season_episodes:
                return TvSeasonEpisodesOut(
                    season_number=season_number,
                    episodes=[
                        TvEpisodeOut(episode_number=e.episode, name=e.name, air_date=e.air_date)
                        for e in season_episodes
                    ],
                    data_available=True,
                )

    return TvSeasonEpisodesOut(season_number=season_number, episodes=[], data_available=False)


@router.get("/ratings", response_model=RatingsOut)
def get_ratings_by_tmdb(
    tmdb_id: int,
    media_type: MediaType,
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
) -> RatingsOut:
    """tmdb_id-keyed sibling of /{item_id}/ratings, for callers with no
    media_items row to key off of (e.g. the Tracker tab's detail pane,
    which only has a tmdb_id -- a tracked title need not be archived yet).
    Same OMDb lookup, just without the imdb_id caching write-back /{item_id}/
    has, since there's no row to cache it onto (TMDBClient memoizes the
    external-id lookup itself, so this stays cheap on repeat calls).
    """
    imdb_id = tmdb.get_external_imdb_id(tmdb_id, media_type)
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


@router.get("/trailer", response_model=TrailerOut)
def get_trailer_by_tmdb(
    tmdb_id: int, media_type: MediaType, tmdb: TMDBClient = Depends(get_tmdb_client)
) -> TrailerOut:
    """tmdb_id-keyed sibling of /{item_id}/trailer -- see get_ratings_by_tmdb."""
    key = tmdb.get_trailer_key(tmdb_id, media_type)
    return TrailerOut(youtube_key=key, tmdb_configured=tmdb.mode == "api")


def _backdrop_for(tmdb_id: int, media_type: str, tmdb: TMDBClient) -> str | None:
    media = tmdb.get_movie_details(tmdb_id) if media_type == "movie" else tmdb.get_tv_details(tmdb_id)
    return media.backdrop_path if media else None


@router.get("/backdrop", response_model=BackdropOut)
def get_backdrop_by_tmdb(
    tmdb_id: int, media_type: MediaType, tmdb: TMDBClient = Depends(get_tmdb_client)
) -> BackdropOut:
    """tmdb_id-keyed sibling of /{item_id}/backdrop -- see get_ratings_by_tmdb.
    API-key-only, same as get_trailer_key -- the scraper path never
    populates MediaResult.backdrop_path."""
    return BackdropOut(backdrop_path=_backdrop_for(tmdb_id, media_type, tmdb), tmdb_configured=tmdb.mode == "api")


@router.get("/more-info", response_model=MoreInfoOut)
def get_more_info_by_tmdb(
    tmdb_id: int, media_type: MediaType, tmdb: TMDBClient = Depends(get_tmdb_client)
) -> MoreInfoOut:
    """tmdb_id-keyed sibling of /{item_id}/more-info -- see get_ratings_by_tmdb."""
    cast = tmdb.get_cast(tmdb_id, media_type)
    similar = tmdb.get_similar_titles(tmdb_id, media_type)
    return MoreInfoOut(
        cast=[CastMemberOut(**c) for c in cast],
        similar=[
            SimilarTitleOut(tmdb_id=s.tmdb_id, title=s.title, year=s.year, poster_path=s.poster_path) for s in similar
        ],
        tmdb_configured=tmdb.mode == "api",
    )


@router.get("/person/{person_id}/credits", response_model=PersonCreditsResponse)
def get_person_credits(
    person_id: int, db: Database = Depends(get_database), tmdb: TMDBClient = Depends(get_tmdb_client),
) -> PersonCreditsResponse:
    """Cast-card click-through: "what else has this person been in", cross-
    referenced against the library so already-owned titles can be flagged
    rather than offered as a duplicate track/recommendation candidate.
    API-key-only, same as get_more_info_by_tmdb -- see TMDBClient.get_person_credits.
    """
    if tmdb.mode != "api":
        return PersonCreditsResponse(items=[], tmdb_configured=False)

    owned_movie_ids = {r["tmdb_id"] for r in db.list_media_items(media_type="movie") if r["tmdb_id"] is not None}
    owned_tv_ids = {r["tmdb_id"] for r in db.list_media_items(media_type="tv") if r["tmdb_id"] is not None}
    owned_tv_ids |= {s["tmdb_id"] for s in db.list_tv_shows()}

    items = [
        PersonCreditItemOut(
            tmdb_id=c["tmdb_id"], media_type=c["media_type"], title=c["title"], year=c["year"],
            poster_path=c["poster_path"],
            owned=c["tmdb_id"] in (owned_movie_ids if c["media_type"] == "movie" else owned_tv_ids),
        )
        for c in tmdb.get_person_credits(person_id)
        if c["tmdb_id"] is not None
    ]
    return PersonCreditsResponse(items=items, tmdb_configured=True)


@router.post("/tags-batch", response_model=TagsBatchResponse)
def set_tags_batch(payload: TagsBatchRequest, db: Database = Depends(get_database)) -> TagsBatchResponse:
    """Adds one tag to every listed item without disturbing each item's
    other tags -- unlike POST /{item_id}/tags (full-list replace, meant for
    the detail pane's own tag editor), this is for the gallery's multi-select
    toolbar where the whole point is applying one tag across many items at
    once."""
    tag = payload.tag.strip()
    if not tag:
        return TagsBatchResponse(updated=0)
    updated = 0
    for item_id in payload.ids:
        row = db.get_media_item(item_id)
        if row is None:
            continue
        tags = set(_tags_list(row))
        tags.add(tag)
        db.update_media_item(item_id, tags=sorted(tags))
        updated += 1
    return TagsBatchResponse(updated=updated)


@router.post("/tags/rename", response_model=TagsBatchResponse)
def rename_tag(payload: TagRenameRequest, db: Database = Depends(get_database)) -> TagsBatchResponse:
    """Renames a tag everywhere it's used across the whole library, e.g.
    "Kids" -> "Kids' Shows" -- unlike /tags-batch and /{item_id}/tags (both
    scoped to items the caller already picked), this scans every row."""
    old, new = payload.old.strip(), payload.new.strip()
    if not old or not new or old == new:
        return TagsBatchResponse(updated=0)
    return TagsBatchResponse(updated=db.rename_tag_everywhere(old, new))


@router.post("/tags/delete", response_model=TagsBatchResponse)
def delete_tag(payload: TagDeleteRequest, db: Database = Depends(get_database)) -> TagsBatchResponse:
    """Removes a tag everywhere it's used across the whole library."""
    tag = payload.tag.strip()
    if not tag:
        return TagsBatchResponse(updated=0)
    return TagsBatchResponse(updated=db.delete_tag_everywhere(tag))


@router.post("/import-watch-history", response_model=WatchHistoryImportResponse)
def import_watch_history(payload: WatchHistoryImportRequest, db: Database = Depends(get_database)) -> WatchHistoryImportResponse:
    """Bulk-marks movies watched (with date/rating) from an external
    service's export -- movies only, matched by title+year (see
    match_movie_by_title_year). Built against Letterboxd's diary.csv shape
    (Date, Name, Year, Letterboxd URI, Rating), parsed client-side; the
    frontend maps those columns into this request's rows."""
    updated = 0
    unmatched: list[str] = []
    for row in payload.rows:
        match = db.match_movie_by_title_year(row.title, row.year)
        if match is None:
            unmatched.append(row.title)
            continue
        fields: dict = {"watched": 1, "watched_at": row.watched_date}
        if row.rating:
            fields["personal_rating"] = max(1, min(5, round(row.rating)))
        db.update_media_item(match["id"], **fields)
        updated += 1
    return WatchHistoryImportResponse(updated=updated, unmatched=unmatched)


@router.post("/{item_id}/watched", response_model=LibraryItemOut)
def set_watched(item_id: int, payload: WatchedUpdateRequest, db: Database = Depends(get_database)) -> LibraryItemOut:
    if db.get_media_item(item_id) is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    db.update_media_item(item_id, watched=1 if payload.watched else 0, watched_at=_watched_at_for(payload.watched))
    return _to_out(db.get_media_item(item_id))


@router.post("/watched-batch", response_model=WatchedBatchResponse)
def set_watched_batch(payload: WatchedBatchRequest, db: Database = Depends(get_database)) -> WatchedBatchResponse:
    updated = 0
    watched_at = _watched_at_for(payload.watched)
    for item_id in payload.ids:
        if db.get_media_item(item_id) is None:
            continue
        db.update_media_item(item_id, watched=1 if payload.watched else 0, watched_at=watched_at)
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


@router.get("/{item_id}/backdrop", response_model=BackdropOut)
def get_backdrop(
    item_id: int, db: Database = Depends(get_database), tmdb: TMDBClient = Depends(get_tmdb_client)
) -> BackdropOut:
    """Backdrop image for the detail pane's banner -- see get_trailer."""
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item["tmdb_id"] is None:
        return BackdropOut(backdrop_path=None, tmdb_configured=tmdb.mode == "api")
    return BackdropOut(backdrop_path=_backdrop_for(item["tmdb_id"], item["media_type"], tmdb), tmdb_configured=tmdb.mode == "api")


@router.get("/{item_id}/play-link", response_model=PlayLinkOut)
def get_play_link(
    item_id: int, db: Database = Depends(get_database), config: AppConfig = Depends(get_config)
) -> PlayLinkOut:
    """Deep link to open this title directly in Plex -- movies only (see
    get_plex_deep_link), None if Plex isn't configured, the title has no
    imdb_id, or Plex doesn't have it. See /jellyfin-sessions +
    /play-on-jellyfin below for the Jellyfin/Kodi equivalent -- a command
    rather than a link, since Jellyfin has no plain-URL deep link."""
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item["media_type"] != "movie" or not item["imdb_id"] or not (config.media_server.plex_url and config.media_server.plex_token):
        return PlayLinkOut(plex_url=None)
    return PlayLinkOut(plex_url=get_plex_deep_link(config.media_server.plex_url, config.media_server.plex_token, item["imdb_id"]))


@router.get("/{item_id}/jellyfin-sessions", response_model=JellyfinSessionsResponse)
def get_jellyfin_sessions(
    item_id: int, db: Database = Depends(get_database), config: AppConfig = Depends(get_config)
) -> JellyfinSessionsResponse:
    """Currently-connected Jellyfin clients (e.g. Kodi via the
    Jellyfin-for-Kodi addon) that can be told to play this title right now
    -- movies only, same imdb_id-matched scope as the Plex link above.
    Empty list (never an error) if Jellyfin isn't configured, this isn't a
    movie, it has no imdb_id, or nothing's currently connected -- the
    frontend just hides the control in every one of those cases."""
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item["media_type"] != "movie" or not item["imdb_id"] or not (config.media_server.jellyfin_url and config.media_server.jellyfin_api_key):
        return JellyfinSessionsResponse(sessions=[])
    sessions = list_jellyfin_sessions(config.media_server.jellyfin_url, config.media_server.jellyfin_api_key)
    return JellyfinSessionsResponse(sessions=sessions)


@router.post("/{item_id}/play-on-jellyfin", response_model=PlayOnJellyfinResponse)
def play_on_jellyfin(
    item_id: int, payload: PlayOnJellyfinRequest, db: Database = Depends(get_database), config: AppConfig = Depends(get_config)
) -> PlayOnJellyfinResponse:
    """Commands the given already-connected Jellyfin session to start
    playing this title -- see play_on_jellyfin_session. False (not an
    error) if Jellyfin isn't configured, the title has no imdb_id, or
    Jellyfin doesn't have a matching item."""
    item = db.get_media_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    if not item["imdb_id"] or not (config.media_server.jellyfin_url and config.media_server.jellyfin_api_key):
        return PlayOnJellyfinResponse(success=False)
    jellyfin_item_id = jellyfin_item_id_for_imdb(config.media_server.jellyfin_url, config.media_server.jellyfin_api_key, item["imdb_id"])
    if jellyfin_item_id is None:
        return PlayOnJellyfinResponse(success=False)
    success = play_on_jellyfin_session(
        config.media_server.jellyfin_url, config.media_server.jellyfin_api_key, payload.session_id, jellyfin_item_id
    )
    return PlayOnJellyfinResponse(success=success)


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
            duration_seconds=probe.duration_seconds,  # feeds Reports' per-viewer watch-time column
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
