from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config_loader import (
    AppConfig,
    LoggingConfig,
    PathsConfig,
    ServerConfig,
    SubtitlesConfig,
    TMDBConfig,
    TrackerConfig,
)
from app.core.tmdb_client import MediaResult
from app.database import Database
from app.dependencies import get_config, get_database, get_tmdb_client
from app.main import app


@pytest.fixture
def client(tmp_path):
    active_dir = tmp_path / "incoming"
    archive_movies = tmp_path / "archive" / "movies"
    archive_tv = tmp_path / "archive" / "tv"
    for d in (active_dir, archive_movies, archive_tv):
        d.mkdir(parents=True)

    config = AppConfig(
        paths=PathsConfig(active_dir=active_dir, archive_movies=archive_movies, archive_tv=archive_tv),
        database_path=tmp_path / "test.db",
        tmdb=TMDBConfig(api_key="", language="en-US"),
        subtitles=SubtitlesConfig(),
        tracker=TrackerConfig(),
        logging=LoggingConfig(file=tmp_path / "test.log"),
        server=ServerConfig(),
    )
    db = Database(config.database_path)
    db.init_db()

    fake_tmdb = MagicMock()
    fake_tmdb.mode = "scraper"
    fake_tmdb.search_movie.return_value = [
        MediaResult(tmdb_id=99, title="Sample Movie", media_type="movie", year=2020, overview="A test movie")
    ]

    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_tmdb_client] = lambda: fake_tmdb

    with TestClient(app) as c:
        yield c, active_dir

    app.dependency_overrides.clear()


def test_status_endpoint(client):
    c, _ = client
    resp = c.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["tmdb_mode"] == "scraper"


def test_scan_preview_confirm_flow(client):
    c, active_dir = client

    video = active_dir / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")

    scan_resp = c.get("/api/scan")
    assert scan_resp.status_code == 200
    files = scan_resp.json()["files"]
    assert len(files) == 1

    preview_resp = c.post("/api/archive/preview", json={"paths": [f["path"] for f in files]})
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert len(preview["items"]) == 1
    item = preview["items"][0]
    assert item["title"] == "Sample Movie"
    assert item["dest_path"].endswith("Sample Movie (2020).mkv")

    confirm_resp = c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    assert confirm_resp.status_code == 200
    result = confirm_resp.json()["results"][0]
    assert result["status"] == "success"
    assert video.exists()  # source untouched, archive copies rather than moves
    assert Path(item["dest_path"]).exists()

    history_resp = c.get("/api/archive/history")
    assert history_resp.status_code == 200
    assert len(history_resp.json()["operations"]) == 1

    stats_resp = c.get("/api/stats")
    assert stats_resp.json()["total_movies"] == 1


def test_scan_covers_archive_dirs_and_excludes_already_handled(client):
    """Organize-in-place setup: a raw file dropped straight into the movie
    archive root (no separate staging folder) should still be found, and
    once archived, neither the raw original nor the organized copy should
    reappear on a rescan."""
    c, _ = client
    archive_movies = Path(c.get("/api/settings").json()["archive_movies"])

    raw = archive_movies / "Some.Movie.2021.mkv"
    raw.write_bytes(b"data")

    scan_resp = c.get("/api/scan")
    files = scan_resp.json()["files"]
    assert len(files) == 1
    assert files[0]["path"] == str(raw)

    preview = c.post("/api/archive/preview", json={"paths": [files[0]["path"]]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    rescan = c.get("/api/scan").json()
    assert rescan["files"] == []


def test_scan_missing_directory_404(client):
    c, _ = client
    resp = c.post("/api/scan/directory", json={"directory": "/nonexistent/path/xyz"})
    assert resp.status_code == 404


def test_tracker_add_and_notifications(client):
    c, _ = client
    add_resp = c.post("/api/tracker/add", json={"tmdb_id": 5, "media_type": "tv", "title": "Show"})
    assert add_resp.status_code == 200

    status_resp = c.get("/api/tracker/status")
    assert status_resp.json()["total_tracked"] == 1
