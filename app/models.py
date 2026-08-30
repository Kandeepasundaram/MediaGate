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


class TrackerNotificationsResponse(BaseModel):
    notifications: list[TrackerNotificationOut]


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


class SettingsOut(BaseModel):
    incoming_movies: str
    incoming_tv: str
    archive_movies: str
    archive_tv: str
    cors_origins: list[str]
    tmdb_api_key_set: bool
    tmdb_api_key_locked_by_env: bool


class SettingsUpdateRequest(BaseModel):
    incoming_movies: str | None = None
    incoming_tv: str | None = None
    archive_movies: str | None = None
    archive_tv: str | None = None
    cors_origins: list[str] | None = None
    tmdb_api_key: str | None = None


class PathCheck(BaseModel):
    path: str
    exists: bool
    writable: bool
    error: str | None = None


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
