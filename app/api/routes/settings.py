"""Runtime-editable settings: media paths, TMDB key, CORS origins.

Deliberately does NOT expose anything that mutates host filesystem
ownership/permissions — /permissions-check is read-only diagnostics. Fixing
a permission problem is a docker-compose/Arcane-level decision (PUID/PGID or
running the container as root), not something a web request should be able
to trigger.
"""
from __future__ import annotations

import os
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config_loader import (
    AppConfig,
    get_config_history_diff,
    list_config_history,
    rollback_config_version,
    update_settings,
)
from app.database import Database
from app.dependencies import get_config, get_database, reset_singletons
from app.models import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenOut,
    ApiTokensListResponse,
    ConfigHistoryDiffResponse,
    ConfigHistoryEntryOut,
    ConfigHistoryListResponse,
    PathCheck,
    PermissionsCheckResponse,
    SettingsOut,
    SettingsUpdateRequest,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _to_out(config: AppConfig) -> SettingsOut:
    return SettingsOut(
        incoming_movies=str(config.paths.incoming_movies),
        incoming_tv=str(config.paths.incoming_tv),
        archive_movies=str(config.paths.archive_movies),
        archive_tv=str(config.paths.archive_tv),
        cors_origins=config.server.cors_origins,
        tmdb_api_key_set=bool(config.tmdb.api_key),
        tmdb_api_key_locked_by_env=config.tmdb_api_key_from_env,
        webhook_url=config.notifications.webhook_url,
        discord_webhook_url=config.notifications.discord_webhook_url,
        telegram_bot_token_set=bool(config.notifications.telegram_bot_token),
        telegram_chat_id=config.notifications.telegram_chat_id,
        pushover_api_token_set=bool(config.notifications.pushover_api_token),
        pushover_user_key_set=bool(config.notifications.pushover_user_key),
        omdb_api_key_set=bool(config.omdb.api_key),
        auto_track_new=config.tracker.auto_track_new,
        digest_mode=config.tracker.digest_mode,
        digest_interval_days=config.tracker.digest_interval_days,
        watcher_enabled=config.watcher.enabled,
        api_token_set=bool(config.server.api_token),
        plex_url=config.media_server.plex_url,
        plex_token_set=bool(config.media_server.plex_token),
        jellyfin_url=config.media_server.jellyfin_url,
        jellyfin_api_key_set=bool(config.media_server.jellyfin_api_key),
        subtitle_keep_languages=config.subtitles.keep_languages,
        subtitle_keep_languages_movies=config.subtitles.keep_languages_movies,
        subtitle_keep_languages_tv=config.subtitles.keep_languages_tv,
        movie_folder_template=config.renaming.movie_folder,
        tv_season_folder_template=config.renaming.tv_season_folder,
        tv_file_template=config.renaming.tv_file,
        collision_policy=config.renaming.collision_policy,
        low_disk_alert_enabled=config.notifications.low_disk_alert_enabled,
        low_disk_threshold_gb=config.notifications.low_disk_threshold_gb,
        webdav_url=config.backup.webdav_url,
        webdav_username=config.backup.webdav_username,
        webdav_password_set=bool(config.backup.webdav_password),
        webdav_remote_path=config.backup.webdav_remote_path,
        opensubtitles_api_key_set=bool(config.subtitles.opensubtitles_api_key),
        auto_fetch_missing_subtitles=config.subtitles.auto_fetch_missing_subtitles,
        write_nfo_files=config.media_server.write_nfo_files,
    )


@router.get("", response_model=SettingsOut)
def get_settings(config: AppConfig = Depends(get_config)) -> SettingsOut:
    return _to_out(config)


@router.post("", response_model=SettingsOut)
def save_settings(payload: SettingsUpdateRequest, config: AppConfig = Depends(get_config)) -> SettingsOut:
    updates: dict[str, dict] = {
        "paths": {}, "server": {}, "tmdb": {}, "notifications": {}, "omdb": {}, "tracker": {}, "media_server": {},
        "subtitles": {}, "renaming": {}, "watcher": {}, "backup": {},
    }

    if payload.incoming_movies is not None:
        updates["paths"]["incoming_movies"] = payload.incoming_movies
    if payload.incoming_tv is not None:
        updates["paths"]["incoming_tv"] = payload.incoming_tv
    if payload.archive_movies is not None:
        updates["paths"]["archive_movies"] = payload.archive_movies
    if payload.archive_tv is not None:
        updates["paths"]["archive_tv"] = payload.archive_tv
    if payload.cors_origins is not None:
        updates["server"]["cors_origins"] = payload.cors_origins
    if payload.tmdb_api_key is not None and not config.tmdb_api_key_from_env:
        updates["tmdb"]["api_key"] = payload.tmdb_api_key
    if payload.webhook_url is not None:
        updates["notifications"]["webhook_url"] = payload.webhook_url
    if payload.discord_webhook_url is not None:
        updates["notifications"]["discord_webhook_url"] = payload.discord_webhook_url
    if payload.telegram_bot_token is not None:
        updates["notifications"]["telegram_bot_token"] = payload.telegram_bot_token
    if payload.telegram_chat_id is not None:
        updates["notifications"]["telegram_chat_id"] = payload.telegram_chat_id
    if payload.pushover_api_token is not None:
        updates["notifications"]["pushover_api_token"] = payload.pushover_api_token
    if payload.pushover_user_key is not None:
        updates["notifications"]["pushover_user_key"] = payload.pushover_user_key
    if payload.low_disk_alert_enabled is not None:
        updates["notifications"]["low_disk_alert_enabled"] = payload.low_disk_alert_enabled
    if payload.low_disk_threshold_gb is not None:
        updates["notifications"]["low_disk_threshold_gb"] = max(0.1, payload.low_disk_threshold_gb)
    if payload.webdav_url is not None:
        updates["backup"]["webdav_url"] = payload.webdav_url
    if payload.webdav_username is not None:
        updates["backup"]["webdav_username"] = payload.webdav_username
    if payload.webdav_password is not None:
        updates["backup"]["webdav_password"] = payload.webdav_password
    if payload.webdav_remote_path is not None:
        updates["backup"]["webdav_remote_path"] = payload.webdav_remote_path
    if payload.opensubtitles_api_key is not None:
        updates["subtitles"]["opensubtitles_api_key"] = payload.opensubtitles_api_key
    if payload.auto_fetch_missing_subtitles is not None:
        updates["subtitles"]["auto_fetch_missing_subtitles"] = payload.auto_fetch_missing_subtitles
    if payload.omdb_api_key is not None:
        updates["omdb"]["api_key"] = payload.omdb_api_key
    if payload.auto_track_new is not None:
        updates["tracker"]["auto_track_new"] = payload.auto_track_new
    if payload.digest_mode is not None:
        updates["tracker"]["digest_mode"] = payload.digest_mode
    if payload.digest_interval_days is not None:
        updates["tracker"]["digest_interval_days"] = max(1, payload.digest_interval_days)
    if payload.watcher_enabled is not None:
        updates["watcher"]["enabled"] = payload.watcher_enabled
    if payload.api_token is not None:
        updates["server"]["api_token"] = payload.api_token
    if payload.plex_url is not None:
        updates["media_server"]["plex_url"] = payload.plex_url
    if payload.plex_token is not None:
        updates["media_server"]["plex_token"] = payload.plex_token
    if payload.jellyfin_url is not None:
        updates["media_server"]["jellyfin_url"] = payload.jellyfin_url
    if payload.jellyfin_api_key is not None:
        updates["media_server"]["jellyfin_api_key"] = payload.jellyfin_api_key
    if payload.write_nfo_files is not None:
        updates["media_server"]["write_nfo_files"] = payload.write_nfo_files
    if payload.subtitle_keep_languages is not None:
        updates["subtitles"]["keep_languages"] = [
            lang.strip().lower() for lang in payload.subtitle_keep_languages if lang.strip()
        ]
    if payload.subtitle_keep_languages_movies is not None:
        updates["subtitles"]["keep_languages_movies"] = [
            lang.strip().lower() for lang in payload.subtitle_keep_languages_movies if lang.strip()
        ]
    if payload.subtitle_keep_languages_tv is not None:
        updates["subtitles"]["keep_languages_tv"] = [
            lang.strip().lower() for lang in payload.subtitle_keep_languages_tv if lang.strip()
        ]
    if payload.movie_folder_template is not None:
        updates["renaming"]["movie_folder"] = payload.movie_folder_template
    if payload.tv_season_folder_template is not None:
        updates["renaming"]["tv_season_folder"] = payload.tv_season_folder_template
    if payload.tv_file_template is not None:
        updates["renaming"]["tv_file"] = payload.tv_file_template
    if payload.collision_policy is not None:
        if payload.collision_policy not in ("suffix", "overwrite", "skip"):
            raise HTTPException(status_code=400, detail="collision_policy must be 'suffix', 'overwrite', or 'skip'")
        updates["renaming"]["collision_policy"] = payload.collision_policy

    updates = {k: v for k, v in updates.items() if v}
    new_config = update_settings(config.config_path, updates)
    reset_singletons()
    return _to_out(new_config)


@router.get("/history", response_model=ConfigHistoryListResponse)
def get_config_history(config: AppConfig = Depends(get_config)) -> ConfigHistoryListResponse:
    """Every config.yaml version saved so far (see
    config_loader._snapshot_config_history), newest first -- the Settings
    tab's version list, distinct from the single config.yaml.bak file
    which only ever holds the immediately-previous save."""
    return ConfigHistoryListResponse(
        versions=[ConfigHistoryEntryOut(**entry) for entry in list_config_history(config.config_path)]
    )


@router.get("/history/{version}/diff", response_model=ConfigHistoryDiffResponse)
def get_config_history_diff_route(version: str, config: AppConfig = Depends(get_config)) -> ConfigHistoryDiffResponse:
    try:
        diff = get_config_history_diff(config.config_path, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ConfigHistoryDiffResponse(diff=diff)


@router.post("/history/{version}/rollback", response_model=SettingsOut)
def rollback_config_history(version: str, config: AppConfig = Depends(get_config)) -> SettingsOut:
    """Restores config.yaml from a historical version. The config that was
    live just before this call is itself snapshotted first (see
    rollback_config_version), so rolling back is never a one-way trip."""
    try:
        new_config = rollback_config_version(config.config_path, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    reset_singletons()
    return _to_out(new_config)


@router.get("/tokens", response_model=ApiTokensListResponse)
def list_api_tokens(db: Database = Depends(get_database)) -> ApiTokensListResponse:
    """Named, individually-revocable API tokens -- distinct from the single
    legacy server.api_token in config.yaml, which both this endpoint and
    require_api_token() leave untouched. Never returns the token value
    itself; only visible once, in the create response below."""
    rows = db.list_api_tokens()
    return ApiTokensListResponse(
        tokens=[
            ApiTokenOut(
                id=r["id"], name=r["name"], created_at=r["created_at"], last_used_at=r["last_used_at"],
                scope=r["scope"],
            )
            for r in rows
        ]
    )


@router.post("/tokens", response_model=ApiTokenCreateResponse)
def create_api_token(payload: ApiTokenCreateRequest, db: Database = Depends(get_database)) -> ApiTokenCreateResponse:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Token name is required")
    if payload.scope not in ("read_write", "read_only"):
        raise HTTPException(status_code=400, detail="scope must be 'read_write' or 'read_only'")
    token = secrets.token_urlsafe(32)
    token_id = db.create_api_token(name, token, scope=payload.scope)
    row = db.get_api_token_by_value(token)
    return ApiTokenCreateResponse(id=token_id, name=name, token=token, created_at=row["created_at"], scope=payload.scope)


@router.delete("/tokens/{token_id}")
def delete_api_token(token_id: int, db: Database = Depends(get_database)) -> dict:
    db.delete_api_token(token_id)
    return {"deleted": True}


LOW_SPACE_THRESHOLD_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB


@router.get("/permissions-check", response_model=PermissionsCheckResponse)
def check_permissions(config: AppConfig = Depends(get_config)) -> PermissionsCheckResponse:
    unique_paths = dict.fromkeys(
        (
            config.paths.incoming_movies,
            config.paths.incoming_tv,
            config.paths.archive_movies,
            config.paths.archive_tv,
        )
    )
    uid = os.getuid() if hasattr(os, "getuid") else None
    gid = os.getgid() if hasattr(os, "getgid") else None
    checks = [_check_path(p, uid, gid) for p in unique_paths]
    return PermissionsCheckResponse(paths=checks, running_uid=uid, running_gid=gid)


def _check_path(path: Path, uid: int | None, gid: int | None) -> PathCheck:
    if not path.exists():
        return PathCheck(path=str(path), exists=False, writable=False, error="Directory does not exist")

    free_bytes = shutil.disk_usage(path).free
    low_space = free_bytes < LOW_SPACE_THRESHOLD_BYTES

    probe = path / f".media-manager-write-test-{uuid.uuid4().hex}"
    try:
        probe.write_text("probe")
        probe.unlink()
        return PathCheck(path=str(path), exists=True, writable=True, free_bytes=free_bytes, low_space=low_space)
    except OSError as exc:
        # A copy-pasteable remediation command -- fixing ownership is a
        # docker-compose/host-level decision (see module docstring), this
        # just saves someone from hand-typing the uid/gid/path.
        chown_hint = f"chown -R {uid}:{gid} {path}" if uid is not None else None
        return PathCheck(
            path=str(path),
            exists=True,
            writable=False,
            error=str(exc),
            free_bytes=free_bytes,
            low_space=low_space,
            chown_hint=chown_hint,
        )
