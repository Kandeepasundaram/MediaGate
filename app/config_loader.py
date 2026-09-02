"""Loads config.yaml, applies env var overrides, exposes typed config objects."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
        "keep_languages_movies": [],
        "keep_languages_tv": [],
        "opensubtitles_api_key": "",
        "auto_fetch_missing_subtitles": False,
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
    "tvmaze": {"enabled": False},
    "backup": {"enabled": True, "retention_days": 14},
    "reports": {"enabled": False, "frequency": "monthly", "cron_time": "08:00"},
    "watcher": {"enabled": False},
    "media_server": {
        "plex_url": "", "plex_token": "", "jellyfin_url": "", "jellyfin_api_key": "", "write_nfo_files": True,
    },
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
    # Per-media-type overrides -- empty (the default) means "use
    # keep_languages above for this type too". Lets e.g. anime TV keep
    # Japanese subtitles while movies stay English-only, without the two
    # media types being forced to share one list.
    keep_languages_movies: list[str] = field(default_factory=list)
    keep_languages_tv: list[str] = field(default_factory=list)
    # Opt-in: auto-fetches a subtitle in the first configured keep-language
    # the file doesn't already have, right before the purge step runs (see
    # subtitle_purger.py / archive.py's confirm_archive) -- "no key" means
    # "no fetch", same pattern as OMDb/TMDB's own optional keys.
    opensubtitles_api_key: str = ""
    auto_fetch_missing_subtitles: bool = False


def keep_languages_for(subtitles: SubtitlesConfig, media_type: str) -> list[str]:
    override = subtitles.keep_languages_movies if media_type == "movie" else subtitles.keep_languages_tv
    return override or subtitles.keep_languages


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
    # Opt-in: fires through the same channels above when a configured media
    # path drops below this free-space threshold. Off by default since
    # someone with a chronically near-full drive doesn't want a push every
    # dashboard load once the low-disk-alert cooldown lapses.
    low_disk_alert_enabled: bool = False
    low_disk_threshold_gb: float = 10.0


@dataclass
class OMDbConfig:
    api_key: str = ""


@dataclass
class TVmazeConfig:
    # No key needed (TVmaze's API is free/keyless) -- this toggle just
    # opts into the extra network calls (episode air dates, show
    # status/network, next-episode tracking; see app/core/tvmaze_client.py),
    # off by default like every other optional integration in this app.
    enabled: bool = False


@dataclass
class BackupConfig:
    enabled: bool = True
    retention_days: int = 14
    # Optional remote copy over WebDAV (Nextcloud, many NAS boxes, etc) --
    # picked over S3/rclone since it needs no new dependency (plain HTTP PUT
    # via `requests`, already used everywhere else in this app) and no
    # extra binary baked into the container image. Off unless webdav_url is set.
    webdav_url: str = ""
    webdav_username: str = ""
    webdav_password: str = ""
    webdav_remote_path: str = "media-manager-backups"


@dataclass
class ReportDeliveryConfig:
    """Opt-in periodic push of the Reports tab's own summary (see
    app/core/report_delivery.py) through the same Discord/Telegram/Pushover/
    generic-webhook channels notifications.* already uses -- no separate
    channel config, just a schedule for when to fire. Off by default, like
    every other optional push in this app."""

    enabled: bool = False
    # "weekly" (last full Mon-Sun week), "monthly" (previous calendar month),
    # or "quarterly" (previous calendar quarter) -- see
    # report_delivery.py::previous_complete_period.
    frequency: str = "monthly"
    cron_time: str = "08:00"


@dataclass
class WatcherConfig:
    # Off by default: a native OS filesystem watch (inotify/ReadDirectoryChangesW)
    # can behave oddly on some network-mounted volumes (NFS/SMB), which is a
    # common homelab setup for incoming/archive folders -- opt-in avoids
    # surprising a deploy that doesn't need it. Never auto-archives anything;
    # see app/core/fs_watcher.py.
    enabled: bool = False


@dataclass
class MediaServerConfig:
    plex_url: str = ""
    plex_token: str = ""
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""
    # On by default -- matches the app's behavior before this toggle existed.
    # Off skips both the .nfo and poster.jpg written alongside every
    # archived/organized file (see archiver.py/organizer.py's
    # _write_nfo_best_effort/_download_artwork_best_effort), for someone who
    # doesn't run Plex/Jellyfin and would rather the archive tree only ever
    # contain the video file itself.
    write_nfo_files: bool = True


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
    {episode_title_suffix} (" - Title" or "" when there's no episode title),
    {absolute_episode} (cumulative episode count across seasons, for
    anime-style naming), {part_suffix} (" - CD1"/"Part2"/etc for a detected
    multi-part rip, else ""), {air_date} (episode air date, "" when unknown --
    only populated when TVmaze integration is enabled, see tvmaze_client.py).
    A movie's multi-part suffix isn't template-controlled -- see
    plan_movie_rename.
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
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    tvmaze: TVmazeConfig = field(default_factory=TVmazeConfig)
    reports: ReportDeliveryConfig = field(default_factory=ReportDeliveryConfig)
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
            keep_languages_movies=[s.lower() for s in raw["subtitles"].get("keep_languages_movies", [])],
            keep_languages_tv=[s.lower() for s in raw["subtitles"].get("keep_languages_tv", [])],
            opensubtitles_api_key=raw["subtitles"].get("opensubtitles_api_key", ""),
            auto_fetch_missing_subtitles=raw["subtitles"].get("auto_fetch_missing_subtitles", False),
        ),
        tracker=TrackerConfig(**raw["tracker"]),
        notifications=NotificationsConfig(**raw["notifications"]),
        omdb=OMDbConfig(**raw["omdb"]),
        tvmaze=TVmazeConfig(**raw["tvmaze"]),
        backup=BackupConfig(**raw["backup"]),
        reports=ReportDeliveryConfig(**raw["reports"]),
        media_server=MediaServerConfig(**raw["media_server"]),
        logging=LoggingConfig(
            level=raw["logging"]["level"],
            file=Path(raw["logging"]["file"]),
        ),
        server=ServerConfig(**raw["server"]),
        renaming=RenamingConfig(**raw["renaming"]),
        watcher=WatcherConfig(**raw["watcher"]),
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
        "low_disk_alert_enabled", "low_disk_threshold_gb",
    },
    "omdb": {"api_key"},
    "tvmaze": {"enabled"},
    "tracker": {"auto_track_new", "digest_mode", "digest_interval_days"},
    "media_server": {"plex_url", "plex_token", "jellyfin_url", "jellyfin_api_key", "write_nfo_files"},
    "subtitles": {
        "keep_languages", "keep_languages_movies", "keep_languages_tv",
        "opensubtitles_api_key", "auto_fetch_missing_subtitles",
    },
    "renaming": {"movie_folder", "tv_season_folder", "tv_file", "collision_policy"},
    "watcher": {"enabled"},
    "backup": {"webdav_url", "webdav_username", "webdav_password", "webdav_remote_path"},
    "reports": {"enabled", "frequency", "cron_time"},
}


_CONFIG_HISTORY_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"  # microsecond precision -- two saves in the same second (e.g. a double-clicked Save) still get distinct entries
_CONFIG_HISTORY_KEEP = 20


def _config_history_dir(config_path: Path) -> Path:
    return config_path.parent / "config_history"


def _snapshot_config_history(config_path: Path) -> None:
    """Copies the current config.yaml into config_history/ before it's
    overwritten -- a real version history (unlike the single-file
    config.yaml.bak below, which only ever holds the *previous* save), so
    the Settings tab can show a version list and roll back to any of the
    last _CONFIG_HISTORY_KEEP saves, not just the immediately preceding one.
    """
    if not config_path.exists():
        return
    history_dir = _config_history_dir(config_path)
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime(_CONFIG_HISTORY_TIMESTAMP_FORMAT)
    dest = history_dir / f"{stamp}.yaml"
    if dest.exists():
        return  # same-microsecond collision (practically never) -- keep the first, not worth a suffix
    dest.write_bytes(config_path.read_bytes())

    entries = sorted(history_dir.glob("*.yaml"))
    for stale in entries[:-_CONFIG_HISTORY_KEEP]:
        stale.unlink(missing_ok=True)


def list_config_history(config_path: Path | str) -> list[dict]:
    """Newest first. Each entry's `version` is its filename -- opaque to the
    caller, just round-tripped back into get_config_history_diff/
    rollback_config_version."""
    history_dir = _config_history_dir(Path(config_path))
    if not history_dir.exists():
        return []
    entries = []
    for path in sorted(history_dir.glob("*.yaml"), reverse=True):
        stamp = path.stem
        try:
            timestamp = datetime.strptime(stamp, _CONFIG_HISTORY_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        entries.append({"version": path.name, "timestamp": timestamp.isoformat(), "size_bytes": path.stat().st_size})
    return entries


def get_config_history_diff(config_path: Path | str, version: str) -> list[str]:
    """Unified diff of a historical version against the current config.yaml
    -- stdlib difflib, no new dependency for what's a small text file."""
    import difflib

    config_path = Path(config_path)
    history_file = _resolve_history_version(config_path, version)
    old_lines = history_file.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True) if config_path.exists() else []
    return list(difflib.unified_diff(old_lines, new_lines, fromfile=version, tofile="config.yaml (current)"))


def _resolve_history_version(config_path: Path, version: str) -> Path:
    """`version` is a filename, but it's still client-supplied -- resolve
    strictly inside config_history/ so a crafted value like `../../etc`
    can't be used to read or restore an arbitrary file."""
    history_dir = _config_history_dir(config_path)
    candidate = (history_dir / version).resolve()
    if candidate.parent != history_dir.resolve() or not candidate.exists():
        raise FileNotFoundError(f"No config history version {version!r}")
    return candidate


def rollback_config_version(config_path: Path | str, version: str) -> AppConfig:
    """Restores config.yaml from a historical version -- snapshots the
    *current* (about-to-be-replaced) config into history first, same as a
    normal save, so a rollback is itself undoable."""
    config_path = Path(config_path)
    history_file = _resolve_history_version(config_path, version)
    _snapshot_config_history(config_path)
    config_path.write_bytes(history_file.read_bytes())
    return load_config(config_path, create_dirs=False)


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
        _snapshot_config_history(config_path)

    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    # create_dirs=False: don't silently mkdir a path the user just typed —
    # if it doesn't exist yet, the Settings UI's permissions check will say so.
    return load_config(config_path, create_dirs=False)
