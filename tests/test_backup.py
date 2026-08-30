from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config_loader import (
    AppConfig,
    BackupConfig,
    LoggingConfig,
    MediaServerConfig,
    NotificationsConfig,
    OMDbConfig,
    PathsConfig,
    ServerConfig,
    SubtitlesConfig,
    TMDBConfig,
    TrackerConfig,
)
from app.core.backup import prune_old_backups, run_backup


def _config(tmp_path) -> AppConfig:
    dirs = {name: tmp_path / name for name in ("incoming_movies", "incoming_tv", "archive_movies", "archive_tv")}
    for d in dirs.values():
        d.mkdir(parents=True)
    db_path = tmp_path / "data" / "media_manager.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"fake db content")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}", encoding="utf-8")
    return AppConfig(
        paths=PathsConfig(**dirs),
        database_path=db_path,
        tmdb=TMDBConfig(api_key="", language="en-US"),
        subtitles=SubtitlesConfig(),
        tracker=TrackerConfig(),
        notifications=NotificationsConfig(),
        omdb=OMDbConfig(),
        backup=BackupConfig(),
        media_server=MediaServerConfig(),
        logging=LoggingConfig(file=tmp_path / "test.log"),
        server=ServerConfig(),
        config_path=config_path,
    )


def test_run_backup_copies_db_and_config(tmp_path):
    config = _config(tmp_path)

    dest_dir = run_backup(config)

    assert (dest_dir / "media_manager.db").read_bytes() == b"fake db content"
    assert (dest_dir / "config.yaml").exists()


def test_run_backup_skips_missing_files_without_error(tmp_path):
    config = _config(tmp_path)
    config.database_path.unlink()

    dest_dir = run_backup(config)

    assert not (dest_dir / "media_manager.db").exists()
    assert (dest_dir / "config.yaml").exists()


def test_prune_old_backups_removes_only_expired(tmp_path):
    config = _config(tmp_path)
    backups_dir = config.database_path.parent / "backups"
    backups_dir.mkdir()

    old = backups_dir / (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%dT%H%M%SZ")
    old.mkdir()
    recent = backups_dir / (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
    recent.mkdir()

    removed = prune_old_backups(config, retention_days=14)

    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_prune_old_backups_ignores_non_backup_entries(tmp_path):
    config = _config(tmp_path)
    backups_dir = config.database_path.parent / "backups"
    backups_dir.mkdir()
    (backups_dir / "not-a-timestamp").mkdir()
    (backups_dir / "stray-file.txt").write_text("x")

    removed = prune_old_backups(config, retention_days=14)

    assert removed == 0
    assert (backups_dir / "not-a-timestamp").exists()


def test_prune_old_backups_noop_when_no_backups_dir(tmp_path):
    config = _config(tmp_path)
    assert prune_old_backups(config, retention_days=14) == 0
