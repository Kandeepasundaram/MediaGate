"""Shared singletons (config, DB, TMDB client) wired up as FastAPI dependencies."""
from __future__ import annotations

import time
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request

from app.config_loader import AppConfig, load_config
from app.core.fs_watcher import NewFileTracker
from app.core.omdb_client import OMDbClient
from app.core.opensubtitles_client import OpenSubtitlesClient
from app.core.tmdb_client import TMDBClient
from app.database import Database

START_TIME = time.monotonic()


@lru_cache
def get_config() -> AppConfig:
    return load_config()


@lru_cache
def get_database() -> Database:
    cfg = get_config()
    db = Database(cfg.database_path)
    db.init_db()
    return db


@lru_cache
def get_tmdb_client() -> TMDBClient:
    cfg = get_config()
    return TMDBClient(api_key=cfg.tmdb.api_key, language=cfg.tmdb.language)


@lru_cache
def get_omdb_client() -> OMDbClient:
    cfg = get_config()
    return OMDbClient(api_key=cfg.omdb.api_key)


@lru_cache
def get_opensubtitles_client() -> OpenSubtitlesClient:
    cfg = get_config()
    return OpenSubtitlesClient(api_key=cfg.subtitles.opensubtitles_api_key)


@lru_cache
def get_new_file_tracker() -> NewFileTracker:
    """One process-lifetime tracker of video files the filesystem watcher
    has seen but no scan has picked up yet -- see app/core/fs_watcher.py.
    Exists even when watcher.enabled is off; it just never gets fed."""
    return NewFileTracker()


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_api_token(
    request: Request,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
    x_api_token: str | None = Header(default=None),
) -> None:
    """Optional shared-secret gate for the API, off by default (no legacy
    token and no named tokens) -- this is a LAN dashboard with no login
    system, so this exists for the one case that matters: someone exposing
    it past the LAN via a reverse proxy who wants more than "whoever can
    reach the port". Applied as a router-level dependency (not global
    middleware) so it goes through Depends(get_config)/Depends(get_database)
    like everything else, honoring dependency_overrides in tests instead of
    hitting the real cached singletons directly.

    Two token forms, checked in order: the single legacy server.api_token
    from config.yaml (kept for backward compatibility, one shared secret
    for everyone, always full read-write), and any named per-device token
    from Settings > API Tokens (api_tokens table) -- each independently
    revocable without rotating the token everyone else uses, and each with
    its own scope (read_write or read_only). A matched named token's
    last_used_at is updated so Settings can show which ones are actually
    still in use. A read_only token is accepted for GET/HEAD/OPTIONS only;
    anything else gets 403 rather than silently doing nothing, so a wrong
    method reads as "this token can't do that" and not a confusing 404/500
    further down the route.
    """
    if x_api_token and config.server.api_token and x_api_token == config.server.api_token:
        return
    if x_api_token:
        token_row = db.get_api_token_by_value(x_api_token)
        if token_row:
            db.touch_api_token(token_row["id"])
            if token_row["scope"] == "read_only" and request.method not in _SAFE_METHODS:
                raise HTTPException(status_code=403, detail="This API token is read-only")
            return
    if not config.server.api_token and not db.list_api_tokens():
        return
    raise HTTPException(status_code=401, detail="Missing or invalid API token")


def reset_singletons() -> None:
    """Test helper: clear cached singletons so a fresh config/db can be injected."""
    get_config.cache_clear()
    get_database.cache_clear()
    get_tmdb_client.cache_clear()
    get_omdb_client.cache_clear()
    get_opensubtitles_client.cache_clear()
    get_new_file_tracker.cache_clear()
