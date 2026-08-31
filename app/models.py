"""Pydantic request/response schemas for the API layer."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MediaType = Literal["movie", "tv"]

# A show's own lifecycle tag, distinct from any per-episode `watched` flag --
# set by the user (see TvShowStatusUpdateRequest), never inferred from TMDB.
TvShowStatus = Literal["watching", "running", "season_done", "cancelled", "ended"]


class ScannedFileOut(BaseModel):
    path: str
    size_bytes: int
    modified_at: float
    parsed_title: str
    media_type: MediaType
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    subtitle_count: int = 0


class ScanResponse(BaseModel):
    directories: list[str]
    files: list[ScannedFileOut]


class NewFilesStatusOut(BaseModel):
    count: int = 0


class ScanDirectoryRequest(BaseModel):
    directory: str


class ArchivePreviewRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    # Manual TMDB match override/disambiguation: path -> chosen tmdb_id,
    # used instead of the automatic top search result for that file.
    tmdb_overrides: dict[str, int] = Field(default_factory=dict)


class ArchivePreviewItem(BaseModel):
    source_path: str
    dest_path: str
    media_type: MediaType
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    tmdb_id: int | None = None
    poster_path: str | None = None
    overview: str = ""
    vote_average: float | None = None
    genres: list[str] = Field(default_factory=list)
    # True when a media_items row already matches this title (movie: same
    # title+year; TV: same show+season+episode) -- surfaced as a warning in
    # the preview table, not a hard block.
    duplicate: bool = False


class TMDBSearchResultOut(BaseModel):
    tmdb_id: int | None
    title: str
    year: int | None = None
    overview: str = ""
    poster_path: str | None = None


class TMDBSearchResponse(BaseModel):
    results: list[TMDBSearchResultOut]


class ArchivePreviewResponse(BaseModel):
    items: list[ArchivePreviewItem]
    errors: list[str] = Field(default_factory=list)


class ArchiveConfirmRequest(BaseModel):
    items: list[ArchivePreviewItem]
    purge_subtitles: bool = True
    # When true, neither archive/organize route touches the filesystem, the
    # database, subtitles, the tracker, or media-server notification -- just
    # reports what would happen (a source-exists/non-empty check) per item.
    dry_run: bool = False


class ArchiveConfirmResult(BaseModel):
    source_path: str
    dest_path: str | None = None
    media_id: int | None = None
    status: Literal["success", "failed"]
    error: str | None = None


class ArchiveConfirmResponse(BaseModel):
    results: list[ArchiveConfirmResult]


class OperationLogOut(BaseModel):
    id: int
    operation_type: str
    media_id: int | None
    details: Any = None
    status: str
    error_message: str | None
    created_at: str


class ArchiveHistoryResponse(BaseModel):
    operations: list[OperationLogOut]


class UndoResponse(BaseModel):
    undone: bool
    detail: str = ""


class TrackerAddRequest(BaseModel):
    tmdb_id: int
    media_type: MediaType
    title: str
    current_season_archived: int | None = None


class TrackerNotificationOut(BaseModel):
    id: int
    tmdb_id: int
    media_type: MediaType
    title: str
    current_season_archived: int | None
    latest_known_season: int | None
    movie_release_status: str | None
    pending_notification: bool
    muted: bool = False
    last_checked: str | None = None
    snoozed_until: str | None = None
    check_interval_hours: float | None = None


class TrackerSnoozeRequest(BaseModel):
    days: int = Field(gt=0)


class TrackerIntervalRequest(BaseModel):
    hours: float | None = Field(default=None, gt=0)


class TrackerNotificationsResponse(BaseModel):
    notifications: list[TrackerNotificationOut]


class TrackedListResponse(BaseModel):
    tracked: list[TrackerNotificationOut]


class TrackerMuteRequest(BaseModel):
    muted: bool


class TrackerAcknowledgeRequest(BaseModel):
    tracker_id: int


class TrackerStatusResponse(BaseModel):
    total_tracked: int
    pending_notifications: int
    last_checked: str | None = None


class NotificationHistoryEntryOut(BaseModel):
    id: int
    tracker_id: int | None
    tmdb_id: int | None
    media_type: MediaType
    title: str
    message: str
    created_at: str


class NotificationHistoryResponse(BaseModel):
    history: list[NotificationHistoryEntryOut]


class UpcomingReleaseOut(BaseModel):
    tmdb_id: int
    media_type: MediaType
    title: str
    release_date: str
    label: str  # "Release" for a movie, "Episode N" for a TV air date


class UpcomingReleasesResponse(BaseModel):
    items: list[UpcomingReleaseOut]


class RecommendationOut(BaseModel):
    tmdb_id: int
    media_type: MediaType
    title: str
    year: int | None = None
    poster_path: str | None = None
    score: int  # how many owned titles TMDB's "similar" endpoint linked this to


class RecommendationsResponse(BaseModel):
    items: list[RecommendationOut]
    tmdb_configured: bool = False


class SyncWatchedResponse(BaseModel):
    updated: int


class ViewerOut(BaseModel):
    id: int
    name: str
    created_at: str


class ViewersListResponse(BaseModel):
    viewers: list[ViewerOut] = Field(default_factory=list)


class ViewerCreateRequest(BaseModel):
    name: str


class ViewerWatchedUpdateRequest(BaseModel):
    watched: bool


class NoteSaveResponse(BaseModel):
    path: str


class RefreshMetadataRequest(BaseModel):
    ids: list[int]


class RefreshMetadataResponse(BaseModel):
    updated: int
    failed: int


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"
    tmdb_mode: str
    database_path: str
    ffprobe_available: bool = False
    database_size_bytes: int = 0
    uptime_seconds: float = 0
    next_tracker_check_in_seconds: float | None = None


class TrackerTaskStatusOut(BaseModel):
    last_check_at: str | None = None
    last_check_status: str | None = None
    next_check_in_seconds: float | None = None


class BackfillTaskStatusOut(BaseModel):
    pending: int = 0
    failed: int = 0


class SimpleTaskStatusOut(BaseModel):
    last_run_at: str | None = None
    last_error: str | None = None
    enabled: bool = True


class BackgroundTasksStatusOut(BaseModel):
    tracker: TrackerTaskStatusOut
    backfill: BackfillTaskStatusOut
    backup: SimpleTaskStatusOut
    maintenance: SimpleTaskStatusOut


class StoragePathOut(BaseModel):
    label: str
    path: str
    exists: bool
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    days_to_full: float | None = None  # None: not enough history yet, or usage isn't trending upward
    history_days: int = 0  # how many days of snapshot history days_to_full is based on


class StorageStatusOut(BaseModel):
    paths: list[StoragePathOut] = Field(default_factory=list)


class StatsResponse(BaseModel):
    total_media_items: int
    total_movies: int
    total_tv_episodes: int
    total_size_bytes: int
    movies_size_bytes: int = 0
    tv_size_bytes: int = 0


class GenreCountOut(BaseModel):
    genre: str
    count: int


class ResolutionStatOut(BaseModel):
    resolution: str
    count: int
    avg_size_bytes: int


class GrowthPointOut(BaseModel):
    month: str  # "YYYY-MM"
    count: int  # items archived that month


class StatsInsightsResponse(BaseModel):
    top_genres: list[GenreCountOut] = Field(default_factory=list)
    resolution_breakdown: list[ResolutionStatOut] = Field(default_factory=list)
    growth_by_month: list[GrowthPointOut] = Field(default_factory=list)


class LibraryItemOut(BaseModel):
    id: int
    title: str
    media_type: MediaType
    year: int | None = None
    season_number: int | None = None
    episode_number: int | None = None
    tmdb_id: int | None = None
    poster_path: str | None = None
    overview: str = ""
    watched: bool
    final_path: str | None = None
    archived_at: str | None = None
    file_name: str | None = None
    size_bytes: int | None = None
    episode_title: str | None = None
    manual_override: bool = False
    vote_average: float | None = None
    genres: list[str] = Field(default_factory=list)
    resolution: str | None = None
    hdr: bool = False
    audio_channels: int | None = None
    tags: list[str] = Field(default_factory=list)
    viewer_watched: bool | None = None  # only set when a ?viewer_id= is passed; reflects that viewer's own state
    show_status: TvShowStatus | None = None  # tv rows only -- see TvShowStatus


class TagsUpdateRequest(BaseModel):
    tags: list[str]


class TagsListResponse(BaseModel):
    tags: list[str]


class TvSeasonSummaryOut(BaseModel):
    season_number: int
    episode_count: int


class TvStatusOut(BaseModel):
    tmdb_id: int
    status: str | None = None
    latest_known_season: int | None = None
    latest_season_episode_count: int | None = None
    total_episodes: int | None = None
    data_available: bool = False
    seasons: list[TvSeasonSummaryOut] = Field(default_factory=list)


class MovieRelatedTitleOut(BaseModel):
    tmdb_id: int | None = None
    title: str
    year: int | None = None


class MovieStatusOut(BaseModel):
    tmdb_id: int
    collection_id: int | None = None
    related: list[MovieRelatedTitleOut] = []
    data_available: bool = False


class FileInfoOut(BaseModel):
    file_name: str
    path: str
    size_bytes: int
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = None
    container: str | None = None
    hdr: bool = False
    audio_channels: int | None = None
    probe_available: bool = False


class LibraryResponse(BaseModel):
    items: list[LibraryItemOut]


class TvShowSummaryOut(BaseModel):
    """A tracked show with no episode files currently on disk -- persists in
    tv_shows independently of media_items, so deleting every episode (see
    delete-file/delete-batch) doesn't make the show itself disappear from
    the TV tab. See TvShowStatus."""
    tmdb_id: int
    title: str
    imdb_id: str | None = None
    poster_path: str | None = None
    overview: str = ""
    genres: list[str] = Field(default_factory=list)
    status: TvShowStatus


class TvLibraryResponse(BaseModel):
    items: list[LibraryItemOut]
    orphaned_shows: list[TvShowSummaryOut] = Field(default_factory=list)


class TvShowStatusUpdateRequest(BaseModel):
    status: TvShowStatus


class WatchedUpdateRequest(BaseModel):
    watched: bool


class WatchedBatchRequest(BaseModel):
    ids: list[int]
    watched: bool


class WatchedBatchResponse(BaseModel):
    updated: int


class RematchImdbRequest(BaseModel):
    ids: list[int]
    imdb_id: str
    media_type: MediaType


class RematchTmdbRequest(BaseModel):
    ids: list[int]
    tmdb_id: int
    media_type: MediaType


class RematchResponse(BaseModel):
    updated: int
    tmdb_id: int | None = None
    title: str | None = None
    year: int | None = None
    poster_path: str | None = None
    overview: str | None = None


class RatingsOut(BaseModel):
    imdb_id: str | None = None
    imdb_rating: float | None = None
    imdb_votes: str | None = None
    rotten_tomatoes: str | None = None
    metacritic: str | None = None
    omdb_configured: bool = False


class TrailerOut(BaseModel):
    youtube_key: str | None = None
    tmdb_configured: bool = False


class CastMemberOut(BaseModel):
    name: str | None = None
    character: str | None = None
    profile_path: str | None = None


class SimilarTitleOut(BaseModel):
    tmdb_id: int | None = None
    title: str
    year: int | None = None
    poster_path: str | None = None


class MoreInfoOut(BaseModel):
    cast: list[CastMemberOut] = Field(default_factory=list)
    similar: list[SimilarTitleOut] = Field(default_factory=list)
    tmdb_configured: bool = False


class BrowseItemOut(BaseModel):
    path: str
    size_bytes: int
    parsed_title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    tracked: bool
    media_id: int | None = None
    tmdb_id: int | None = None
    watched: bool = False


class BrowseResponse(BaseModel):
    directory: str
    items: list[BrowseItemOut]


class DeleteFileRequest(BaseModel):
    path: str
    # When true, nothing is deleted -- just reports what would happen,
    # including which sibling files and the folder itself would go too.
    dry_run: bool = False


class DeleteBatchRequest(BaseModel):
    paths: list[str]
    dry_run: bool = False


class DeletePreviewOut(BaseModel):
    path: str
    would_delete: bool
    sibling_files: list[str] = Field(default_factory=list)
    folder_removed: str | None = None
    error: str | None = None


class DeleteBatchResponse(BaseModel):
    deleted: int
    errors: list[str] = Field(default_factory=list)
    # Populated only when the request was a dry run -- one entry per path,
    # describing what would have been deleted instead of what was.
    previews: list[DeletePreviewOut] = Field(default_factory=list)


class MetadataStatusResponse(BaseModel):
    pending: int
    failed: int = 0


class OrphanArtworkGroupOut(BaseModel):
    folder: str
    files: list[str] = Field(default_factory=list)


class LibraryHealthOut(BaseModel):
    orphans: list[LibraryItemOut] = Field(default_factory=list)
    duplicates: list[list[LibraryItemOut]] = Field(default_factory=list)
    orphaned_artwork: list[OrphanArtworkGroupOut] = Field(default_factory=list)


class OrphanCleanupResponse(BaseModel):
    removed: int
    dry_run: bool = False
    # final_path of each affected media_items row -- removed if dry_run is
    # false, would-be-removed if true.
    paths: list[str] = Field(default_factory=list)


class OrphanArtworkCleanupResponse(BaseModel):
    removed: int
    dry_run: bool = False
    # Populated only when dry_run is true -- same shape as /health's
    # orphaned_artwork, so the UI can show exactly what would go.
    groups: list[OrphanArtworkGroupOut] = Field(default_factory=list)


class RetryFailedMatchesResponse(BaseModel):
    reset: int


class ManualOverrideRequest(BaseModel):
    title: str
    year: int | None = None


class MediaItemExportOut(BaseModel):
    original_path: str
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    media_type: MediaType
    season_number: int | None = None
    episode_number: int | None = None
    final_path: str | None = None
    archived_at: str | None = None
    watched: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    imdb_id: str | None = None
    manual_override: bool = False


class LibraryExportResponse(BaseModel):
    items: list[MediaItemExportOut]
    exported_at: str


class LibraryImportRequest(BaseModel):
    items: list[MediaItemExportOut]


class LibraryImportResponse(BaseModel):
    imported: int
    skipped: int


class SettingsOut(BaseModel):
    incoming_movies: str
    incoming_tv: str
    archive_movies: str
    archive_tv: str
    cors_origins: list[str]
    tmdb_api_key_set: bool
    tmdb_api_key_locked_by_env: bool
    webhook_url: str = ""
    discord_webhook_url: str = ""
    telegram_bot_token_set: bool = False
    telegram_chat_id: str = ""
    pushover_api_token_set: bool = False
    pushover_user_key_set: bool = False
    omdb_api_key_set: bool = False
    auto_track_new: bool = False
    digest_mode: bool = False
    digest_interval_days: int = 1
    watcher_enabled: bool = False
    api_token_set: bool = False
    plex_url: str = ""
    plex_token_set: bool = False
    jellyfin_url: str = ""
    jellyfin_api_key_set: bool = False
    subtitle_keep_languages: list[str] = Field(default_factory=list)
    subtitle_keep_languages_movies: list[str] = Field(default_factory=list)
    subtitle_keep_languages_tv: list[str] = Field(default_factory=list)
    movie_folder_template: str = ""
    tv_season_folder_template: str = ""
    tv_file_template: str = ""
    collision_policy: str = "suffix"
    low_disk_alert_enabled: bool = False
    low_disk_threshold_gb: float = 10.0
    webdav_url: str = ""
    webdav_username: str = ""
    webdav_password_set: bool = False
    webdav_remote_path: str = "media-manager-backups"
    opensubtitles_api_key_set: bool = False
    auto_fetch_missing_subtitles: bool = False
    write_nfo_files: bool = True


class SettingsUpdateRequest(BaseModel):
    incoming_movies: str | None = None
    incoming_tv: str | None = None
    archive_movies: str | None = None
    archive_tv: str | None = None
    cors_origins: list[str] | None = None
    tmdb_api_key: str | None = None
    webhook_url: str | None = None
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    pushover_api_token: str | None = None
    pushover_user_key: str | None = None
    omdb_api_key: str | None = None
    auto_track_new: bool | None = None
    digest_mode: bool | None = None
    digest_interval_days: int | None = None
    watcher_enabled: bool | None = None
    api_token: str | None = None
    plex_url: str | None = None
    plex_token: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    subtitle_keep_languages: list[str] | None = None
    subtitle_keep_languages_movies: list[str] | None = None
    subtitle_keep_languages_tv: list[str] | None = None
    movie_folder_template: str | None = None
    tv_season_folder_template: str | None = None
    tv_file_template: str | None = None
    collision_policy: str | None = None
    low_disk_alert_enabled: bool | None = None
    low_disk_threshold_gb: float | None = None
    webdav_url: str | None = None
    webdav_username: str | None = None
    webdav_password: str | None = None
    webdav_remote_path: str | None = None
    opensubtitles_api_key: str | None = None
    auto_fetch_missing_subtitles: bool | None = None
    write_nfo_files: bool | None = None


class ConfigHistoryEntryOut(BaseModel):
    version: str
    timestamp: str
    size_bytes: int


class ConfigHistoryListResponse(BaseModel):
    versions: list[ConfigHistoryEntryOut]


class ConfigHistoryDiffResponse(BaseModel):
    diff: list[str]


class PathCheck(BaseModel):
    path: str
    exists: bool
    writable: bool
    error: str | None = None
    free_bytes: int | None = None
    low_space: bool = False
    chown_hint: str | None = None


class PermissionsCheckResponse(BaseModel):
    paths: list[PathCheck]
    running_uid: int | None = None
    running_gid: int | None = None


class ApiTokenOut(BaseModel):
    id: int
    name: str
    created_at: str
    last_used_at: str | None = None
    scope: str = "read_write"


class ApiTokenCreateRequest(BaseModel):
    name: str
    scope: str = "read_write"


class ApiTokenCreateResponse(BaseModel):
    id: int
    name: str
    token: str  # shown once -- not retrievable again after this response
    created_at: str
    scope: str = "read_write"


class ApiTokensListResponse(BaseModel):
    tokens: list[ApiTokenOut] = Field(default_factory=list)


class LogEntryOut(BaseModel):
    id: int
    operation_type: str
    status: str
    created_at: str
    error_message: str | None = None
