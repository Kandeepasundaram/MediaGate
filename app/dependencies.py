"""Shared singletons (config, DB, TMDB client) wired up as FastAPI dependencies."""
from __future__ import annotations

from functools import lru_cache

from app.config_loader import AppConfig, load_config
from app.core.tmdb_client import TMDBClient
from app.database import Database


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


def reset_singletons() -> None:
    """Test helper: clear cached singletons so a fresh config/db can be injected."""
    get_config.cache_clear()
    get_database.cache_clear()
    get_tmdb_client.cache_clear()
