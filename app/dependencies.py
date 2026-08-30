"""Shared singletons (config, DB, TMDB client) wired up as FastAPI dependencies."""
from __future__ import annotations

import time
from functools import lru_cache

from fastapi import Depends, Header, HTTPException

from app.config_loader import AppConfig, load_config
from app.core.omdb_client import OMDbClient
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


def require_api_token(
    config: AppConfig = Depends(get_config), x_api_token: str | None = Header(default=None)
) -> None:
    """Optional shared-secret gate for the API, off by default (empty
    token) -- this is a LAN dashboard with no login system, so this exists
    for the one case that matters: someone exposing it past the LAN via a
    reverse proxy who wants more than "whoever can reach the port". Applied
    as a router-level dependency (not global middleware) so it goes through
    Depends(get_config) like everything else, honoring dependency_overrides
    in tests instead of hitting the real cached singleton directly.
    """
    if config.server.api_token and x_api_token != config.server.api_token:
        raise HTTPException(status_code=401, detail="Missing or invalid API token")


def reset_singletons() -> None:
    """Test helper: clear cached singletons so a fresh config/db can be injected."""
    get_config.cache_clear()
    get_database.cache_clear()
    get_tmdb_client.cache_clear()
    get_omdb_client.cache_clear()
