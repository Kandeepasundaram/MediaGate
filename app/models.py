"""Pydantic request/response schemas for the API layer."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MediaType = Literal["movie", "tv"]


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


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"
    tmdb_mode: str
    database_path: str


class StatsResponse(BaseModel):
    total_media_items: int
    total_movies: int
    total_tv_episodes: int
    total_size_bytes: int


class LibraryItemOut(BaseModel):
    id: int
    title: str
    media_type: MediaType
    year: int | None = None
    season_number: int | None = None
    episode_number: int | None = None
    poster_path: str | None = None
    overview: str = ""
    watched: bool
    final_path: str | None = None
    archived_at: str | None = None


class LibraryResponse(BaseModel):
    items: list[LibraryItemOut]


class WatchedUpdateRequest(BaseModel):
    watched: bool


class WatchedBatchRequest(BaseModel):
    ids: list[int]
    watched: bool


class WatchedBatchResponse(BaseModel):
    updated: int


class BrowseItemOut(BaseModel):
    path: str
    size_bytes: int
    parsed_title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    tracked: bool
    media_id: int | None = None
    watched: bool = False


class BrowseResponse(BaseModel):
    directory: str
    items: list[BrowseItemOut]


class DeleteFileRequest(BaseModel):
    path: str


class MetadataStatusResponse(BaseModel):
    pending: int
    failed: int = 0


class SettingsOut(BaseModel):
    incoming_movies: str
    incoming_tv: str
    archive_movies: str
    archive_tv: str
    cors_origins: list[str]
    tmdb_api_key_set: bool
    tmdb_api_key_locked_by_env: bool
    webhook_url: str = ""


class SettingsUpdateRequest(BaseModel):
    incoming_movies: str | None = None
    incoming_tv: str | None = None
    archive_movies: str | None = None
    archive_tv: str | None = None
    cors_origins: list[str] | None = None
    tmdb_api_key: str | None = None
    webhook_url: str | None = None


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


class LogEntryOut(BaseModel):
    id: int
    operation_type: str
    status: str
    created_at: str
    error_message: str | None = None
