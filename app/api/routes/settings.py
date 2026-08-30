"""Runtime-editable settings: media paths, TMDB key, CORS origins.

Deliberately does NOT expose anything that mutates host filesystem
ownership/permissions — /permissions-check is read-only diagnostics. Fixing
a permission problem is a docker-compose/Arcane-level decision (PUID/PGID or
running the container as root), not something a web request should be able
to trigger.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends

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
    )


@router.get("", response_model=SettingsOut)
def get_settings(config: AppConfig = Depends(get_config)) -> SettingsOut:
    return _to_out(config)


@router.post("", response_model=SettingsOut)
def save_settings(payload: SettingsUpdateRequest, config: AppConfig = Depends(get_config)) -> SettingsOut:
    updates: dict[str, dict] = {"paths": {}, "server": {}, "tmdb": {}}

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

    updates = {k: v for k, v in updates.items() if v}
    new_config = update_settings(config.config_path, updates)
    reset_singletons()
    return _to_out(new_config)


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
    checks = [_check_path(p) for p in unique_paths]
    return PermissionsCheckResponse(
        paths=checks,
        running_uid=os.getuid() if hasattr(os, "getuid") else None,
        running_gid=os.getgid() if hasattr(os, "getgid") else None,
    )


def _check_path(path: Path) -> PathCheck:
    if not path.exists():
        return PathCheck(path=str(path), exists=False, writable=False, error="Directory does not exist")

    probe = path / f".media-manager-write-test-{uuid.uuid4().hex}"
    try:
        probe.write_text("probe")
        probe.unlink()
        return PathCheck(path=str(path), exists=True, writable=True)
    except OSError as exc:
        return PathCheck(path=str(path), exists=True, writable=False, error=str(exc))
