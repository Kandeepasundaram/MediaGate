from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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
from app.core.backup import prune_old_backups, run_backup, upload_to_webdav


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


def _ok_response(status_code=201):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def test_run_backup_skips_webdav_when_not_configured(tmp_path):
    config = _config(tmp_path)
    with patch("app.core.backup.requests.put") as mock_put, patch("app.core.backup.requests.request") as mock_req:
        run_backup(config)
    mock_put.assert_not_called()
    mock_req.assert_not_called()


def test_run_backup_pushes_to_webdav_when_configured(tmp_path):
    config = _config(tmp_path)
    config.backup.webdav_url = "https://cloud.example.com/dav"
    config.backup.webdav_username = "user"
    config.backup.webdav_password = "pass"

    with patch("app.core.backup.requests.request", return_value=_ok_response(201)) as mock_mkcol, \
         patch("app.core.backup.requests.put", return_value=_ok_response(201)) as mock_put:
        dest_dir = run_backup(config)

    assert mock_mkcol.call_count == 2  # remote root + timestamp subfolder
    put_urls = [c.args[0] for c in mock_put.call_args_list]
    assert any(dest_dir.name in url and "media_manager.db" in url for url in put_urls)
    assert any(dest_dir.name in url and "config.yaml" in url for url in put_urls)
    for call in mock_put.call_args_list:
        assert call.kwargs["auth"] == ("user", "pass")


def test_upload_to_webdav_returns_false_on_put_failure(tmp_path):
    config = _config(tmp_path)
    config.backup.webdav_url = "https://cloud.example.com/dav"
    dest_dir = config.database_path.parent / "backups" / "20260101T000000Z"
    dest_dir.mkdir(parents=True)
    (dest_dir / "media_manager.db").write_bytes(b"x")

    with patch("app.core.backup.requests.request", return_value=_ok_response(201)), \
         patch("app.core.backup.requests.put", return_value=_ok_response(500)):
        ok = upload_to_webdav(config, dest_dir)
    assert ok is False


def test_upload_to_webdav_swallows_connection_errors(tmp_path):
    import requests

    config = _config(tmp_path)
    config.backup.webdav_url = "https://cloud.example.com/dav"
    dest_dir = config.database_path.parent / "backups" / "20260101T000000Z"
    dest_dir.mkdir(parents=True)
    (dest_dir / "media_manager.db").write_bytes(b"x")

    with patch("app.core.backup.requests.request", side_effect=requests.RequestException("down")), \
         patch("app.core.backup.requests.put", side_effect=requests.RequestException("down")):
        ok = upload_to_webdav(config, dest_dir)  # must not raise
    assert ok is False


def test_upload_to_webdav_uses_configured_remote_path(tmp_path):
    config = _config(tmp_path)
    config.backup.webdav_url = "https://cloud.example.com/dav"
    config.backup.webdav_remote_path = "custom-folder"
    dest_dir = config.database_path.parent / "backups" / "20260101T000000Z"
    dest_dir.mkdir(parents=True)
    (dest_dir / "media_manager.db").write_bytes(b"x")

    with patch("app.core.backup.requests.request", return_value=_ok_response(201)) as mock_mkcol, \
         patch("app.core.backup.requests.put", return_value=_ok_response(201)):
        upload_to_webdav(config, dest_dir)

    mkcol_urls = [c.args[1] for c in mock_mkcol.call_args_list]
    assert any("custom-folder" in url for url in mkcol_urls)
