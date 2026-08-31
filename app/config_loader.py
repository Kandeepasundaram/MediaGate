"""Loads config.yaml, applies env var overrides, exposes typed config objects."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(os.environ.get("MEDIA_MANAGER_CONFIG", "config.yaml"))

_DEFAULT_CONFIG: dict = {
    "paths": {
        "incoming_movies": "./sample_media/incoming/movies",
        "incoming_tv": "./sample_media/incoming/tv",
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
        "cron_time": "06:00", "notification_ttl_days": 30, "auto_track_new": False,
        "digest_mode": False, "digest_interval_days": 1,
    },
    "notifications": {
        "webhook_url": "",
        "discord_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "pushover_api_token": "",
        "pushover_user_key": "",
    },
    "omdb": {"api_key": ""},
    "backup": {"enabled": True, "retention_days": 14},
    "media_server": {"plex_url": "", "plex_token": "", "jellyfin_url": "", "jellyfin_api_key": ""},
    "logging": {"level": "INFO", "file": "./logs/media_manager.log"},
    "server": {"host": "0.0.0.0", "port": 8000, "cors_origins": ["*"], "api_token": ""},
    "renaming": {
        "movie_folder": "{title}{year_suffix}",
        "tv_season_folder": "Season {season:02d}",
        "tv_file": "{show_name} - {code}{episode_title_suffix}",
        "collision_policy": "suffix",
    },
}


@dataclass
class PathsConfig:
    incoming_movies: Path
    incoming_tv: Path
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
    auto_track_new: bool = False
    # False (default): a notification fires the moment a title newly
    # becomes pending -- real-time, one push per event. True: no real-time
    # push at all; instead a single batch digest covering everything still
    # pending fires every digest_interval_days, at cron_time, from the same
    # scheduler loop that already runs the tracker check.
    digest_mode: bool = False
    digest_interval_days: int = 1


@dataclass
class NotificationsConfig:
    webhook_url: str = ""
    # Native provider integrations, each fired independently when
    # configured (in addition to webhook_url above, not instead of it) --
    # see tracker.py's _send_discord/_send_telegram/_send_pushover.
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    pushover_api_token: str = ""
    pushover_user_key: str = ""


@dataclass
class OMDbConfig:
    api_key: str = ""


@dataclass
class BackupConfig:
    enabled: bool = True
    retention_days: int = 14


@dataclass
class MediaServerConfig:
    plex_url: str = ""
    plex_token: str = ""
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Path = Path("./logs/media_manager.log")


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    api_token: str = ""


@dataclass
class RenamingConfig:
    """User-configurable str.format() templates for archive.py's rename
    plans. Movies have no separate file template -- the file always shares
    the folder's base name by convention (see renamer.py::plan_movie_rename
    and the "also rename file" UI, which both depend on that). Available
    tokens: movie_folder gets {title}, {year}, {year_suffix} (" (YYYY)" or
    "" when year is unknown); tv_season_folder gets {season}; tv_file gets
    {show_name}, {season}, {episode}, {code} ("S01E02"), {episode_title},
    {episode_title_suffix} (" - Title" or "" when there's no episode title).
    """
    movie_folder: str = "{title}{year_suffix}"
    tv_season_folder: str = "Season {season:02d}"
    tv_file: str = "{show_name} - {code}{episode_title_suffix}"
    # What to do when the computed destination path already exists:
    # "suffix" appends " (2)", " (3)", ... (the historical default);
    # "overwrite" reuses the exact path, replacing whatever's there;
    # "skip" excludes the file from the preview with an error message
    # instead of silently creating or clobbering anything.
    collision_policy: str = "suffix"


@dataclass
class AppConfig:
    paths: PathsConfig
    database_path: Path
    tmdb: TMDBConfig
    subtitles: SubtitlesConfig
    tracker: TrackerConfig
    notifications: NotificationsConfig
    omdb: OMDbConfig
    backup: BackupConfig
    media_server: MediaServerConfig
    logging: LoggingConfig
    server: ServerConfig
    renaming: RenamingConfig = field(default_factory=RenamingConfig)
    config_path: Path = Path("config.yaml")
    tmdb_api_key_from_env: bool = False


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

    env_api_key = os.environ.get("TMDB_API_KEY")
    api_key = env_api_key if env_api_key else raw["tmdb"].get("api_key", "")

    cfg = AppConfig(
        paths=PathsConfig(
            incoming_movies=Path(raw["paths"]["incoming_movies"]),
            incoming_tv=Path(raw["paths"]["incoming_tv"]),
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
        notifications=NotificationsConfig(**raw["notifications"]),
        omdb=OMDbConfig(**raw["omdb"]),
        backup=BackupConfig(**raw["backup"]),
        media_server=MediaServerConfig(**raw["media_server"]),
        logging=LoggingConfig(
            level=raw["logging"]["level"],
            file=Path(raw["logging"]["file"]),
        ),
        server=ServerConfig(**raw["server"]),
        renaming=RenamingConfig(**raw["renaming"]),
        config_path=path,
        tmdb_api_key_from_env=bool(env_api_key),
    )

    if create_dirs:
        for d in (
            cfg.paths.incoming_movies,
            cfg.paths.incoming_tv,
            cfg.paths.archive_movies,
            cfg.paths.archive_tv,
        ):
            d.mkdir(parents=True, exist_ok=True)
        cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.logging.file.parent.mkdir(parents=True, exist_ok=True)

    return cfg


_EDITABLE_KEYS = {
    "paths": {"incoming_movies", "incoming_tv", "archive_movies", "archive_tv"},
    "tmdb": {"api_key"},
    "server": {"cors_origins", "api_token"},
    "notifications": {
        "webhook_url", "discord_webhook_url", "telegram_bot_token", "telegram_chat_id",
        "pushover_api_token", "pushover_user_key",
    },
    "omdb": {"api_key"},
    "tracker": {"auto_track_new", "digest_mode", "digest_interval_days"},
    "media_server": {"plex_url", "plex_token", "jellyfin_url", "jellyfin_api_key"},
    "subtitles": {"keep_languages"},
    "renaming": {"movie_folder", "tv_season_folder", "tv_file", "collision_policy"},
}


def update_settings(config_path: Path | str, updates: dict[str, dict]) -> AppConfig:
    """Merge `updates` (e.g. {"paths": {"incoming_movies": "/media/movies"}}) into
    config.yaml, restricted to the fields the Settings UI is allowed to touch,
    then reload and return the resulting AppConfig.
    """
    config_path = Path(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    raw = _merge_defaults(raw or {})

    for section, fields in updates.items():
        if section not in _EDITABLE_KEYS:
            continue
        for key, value in fields.items():
            if key in _EDITABLE_KEYS[section]:
                raw[section][key] = value

    if config_path.exists():
        # Single rolling backup of the pre-change file -- not a versioned
        # history, just a way back from a settings save gone wrong (e.g. a
        # path typo that then fails the permissions check).
        config_path.with_suffix(config_path.suffix + ".bak").write_bytes(config_path.read_bytes())

    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    # create_dirs=False: don't silently mkdir a path the user just typed —
    # if it doesn't exist yet, the Settings UI's permissions check will say so.
    return load_config(config_path, create_dirs=False)
