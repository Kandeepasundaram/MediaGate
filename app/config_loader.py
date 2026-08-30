"""Loads config.yaml, applies env var overrides, exposes typed config objects."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(os.environ.get("MEDIA_MANAGER_CONFIG", "config.yaml"))

_DEFAULT_CONFIG: dict = {
    "paths": {
        "active_dir": "./sample_media/incoming",
        "archive_movies": "./sample_media/archive/movies",
        "archive_tv": "./sample_media/archive/tv",
    },
    "database": {"path": "./data/media_manager.db"},
    "tmdb": {"api_key": "", "language": "en-US"},
    "subtitles": {
        "keep_languages": ["en", "eng", "english"],
        "delete_extensions": [".srt", ".ass", ".ssa"],
    },
    "tracker": {
        "cron_time": "06:00",
        "notification_ttl_days": 30,
        "windows_agent_url": "",
    },
    "logging": {"level": "INFO", "file": "./logs/media_manager.log"},
    "server": {"host": "0.0.0.0", "port": 8000, "cors_origins": ["*"]},
}


@dataclass
class PathsConfig:
    active_dir: Path
    archive_movies: Path
    archive_tv: Path


@dataclass
class TMDBConfig:
    api_key: str
    language: str = "en-US"


@dataclass
class SubtitlesConfig:
    keep_languages: list[str] = field(default_factory=lambda: ["en", "eng", "english"])
    delete_extensions: list[str] = field(default_factory=lambda: [".srt", ".ass", ".ssa"])


@dataclass
class TrackerConfig:
    cron_time: str = "06:00"
    notification_ttl_days: int = 30
    windows_agent_url: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Path = Path("./logs/media_manager.log")


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = field(default_factory=lambda: ["*"])


@dataclass
class AppConfig:
    paths: PathsConfig
    database_path: Path
    tmdb: TMDBConfig
    subtitles: SubtitlesConfig
    tracker: TrackerConfig
    logging: LoggingConfig
    server: ServerConfig


def _merge_defaults(raw: dict) -> dict:
    merged = {}
    for key, default_section in _DEFAULT_CONFIG.items():
        section = raw.get(key, {}) or {}
        merged[key] = {**default_section, **section}
    return merged


def load_config(path: Path | str = DEFAULT_CONFIG_PATH, *, create_dirs: bool = True) -> AppConfig:
    """Parse config.yaml (creating a default file if missing), apply env overrides."""
    path = Path(path)
    if not path.exists():
        path.write_text(yaml.safe_dump(_DEFAULT_CONFIG, sort_keys=False), encoding="utf-8")
        raw = _DEFAULT_CONFIG
    else:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = _merge_defaults(raw)

    api_key = os.environ.get("TMDB_API_KEY", raw["tmdb"].get("api_key", ""))

    cfg = AppConfig(
        paths=PathsConfig(
            active_dir=Path(raw["paths"]["active_dir"]),
            archive_movies=Path(raw["paths"]["archive_movies"]),
            archive_tv=Path(raw["paths"]["archive_tv"]),
        ),
        database_path=Path(raw["database"]["path"]),
        tmdb=TMDBConfig(api_key=api_key, language=raw["tmdb"].get("language", "en-US")),
        subtitles=SubtitlesConfig(
            keep_languages=[s.lower() for s in raw["subtitles"]["keep_languages"]],
            delete_extensions=raw["subtitles"]["delete_extensions"],
        ),
        tracker=TrackerConfig(**raw["tracker"]),
        logging=LoggingConfig(
            level=raw["logging"]["level"],
            file=Path(raw["logging"]["file"]),
        ),
        server=ServerConfig(**raw["server"]),
    )

    if create_dirs:
        for d in (cfg.paths.active_dir, cfg.paths.archive_movies, cfg.paths.archive_tv):
            d.mkdir(parents=True, exist_ok=True)
        cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.logging.file.parent.mkdir(parents=True, exist_ok=True)

    return cfg
