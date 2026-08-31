"""Runtime-editable settings: media paths, TMDB key, CORS origins.

Deliberately does NOT expose anything that mutates host filesystem
ownership/permissions — /permissions-check is read-only diagnostics. Fixing
a permission problem is a docker-compose/Arcane-level decision (PUID/PGID or
running the container as root), not something a web request should be able
to trigger.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config_loader import AppConfig, update_settings
from app.dependencies import get_config, reset_singletons
from app.models import (
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
        api_token_set=bool(config.server.api_token),
        plex_url=config.media_server.plex_url,
        plex_token_set=bool(config.media_server.plex_token),
        jellyfin_url=config.media_server.jellyfin_url,
        jellyfin_api_key_set=bool(config.media_server.jellyfin_api_key),
        subtitle_keep_languages=config.subtitles.keep_languages,
        movie_folder_template=config.renaming.movie_folder,
        tv_season_folder_template=config.renaming.tv_season_folder,
        tv_file_template=config.renaming.tv_file,
        collision_policy=config.renaming.collision_policy,
    )


@router.get("", response_model=SettingsOut)
def get_settings(config: AppConfig = Depends(get_config)) -> SettingsOut:
    return _to_out(config)


@router.post("", response_model=SettingsOut)
def save_settings(payload: SettingsUpdateRequest, config: AppConfig = Depends(get_config)) -> SettingsOut:
    updates: dict[str, dict] = {
        "paths": {}, "server": {}, "tmdb": {}, "notifications": {}, "omdb": {}, "tracker": {}, "media_server": {},
        "subtitles": {}, "renaming": {},
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
    if payload.omdb_api_key is not None:
        updates["omdb"]["api_key"] = payload.omdb_api_key
    if payload.auto_track_new is not None:
        updates["tracker"]["auto_track_new"] = payload.auto_track_new
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
    if payload.subtitle_keep_languages is not None:
        updates["subtitles"]["keep_languages"] = [
            lang.strip().lower() for lang in payload.subtitle_keep_languages if lang.strip()
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
