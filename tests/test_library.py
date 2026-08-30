from __future__ import annotations

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
from app.database import Database
from app.dependencies import get_config, get_database, get_tmdb_client
from app.main import app


@pytest.fixture
def client(tmp_path):
    dirs = {name: tmp_path / name for name in ("incoming_movies", "incoming_tv", "archive_movies", "archive_tv")}
    for d in dirs.values():
        d.mkdir(parents=True)

    config = AppConfig(
        paths=PathsConfig(**dirs),
        database_path=tmp_path / "test.db",
        tmdb=TMDBConfig(api_key="", language="en-US"),
        subtitles=SubtitlesConfig(),
        tracker=TrackerConfig(),
        logging=LoggingConfig(file=tmp_path / "test.log"),
        server=ServerConfig(),
    )
    db = Database(config.database_path)
    db.init_db()

    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_tmdb_client] = lambda: MagicMock(mode="scraper")

    with TestClient(app) as c:
        yield c, db

    app.dependency_overrides.clear()


def _seed_movie(db, **overrides) -> int:
    defaults = dict(
        original_path="/incoming/movie.mkv",
        final_path="/archive/Movie (2020)/Movie (2020).mkv",
        title="Movie",
        year=2020,
        media_type="movie",
        metadata={"poster_path": "/poster.jpg", "overview": "Plot."},
    )
    defaults.update(overrides)
    return db.create_media_item(**defaults)


def test_list_movies_empty(client):
    c, _ = client
    resp = c.get("/api/library/movies")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_list_movies_returns_poster_and_overview(client):
    c, db = client
    _seed_movie(db)

    resp = c.get("/api/library/movies")
    item = resp.json()["items"][0]
    assert item["title"] == "Movie"
    assert item["poster_path"] == "/poster.jpg"
    assert item["overview"] == "Plot."
    assert item["watched"] is False


def test_list_tv_filters_by_type(client):
    c, db = client
    _seed_movie(db)
    db.create_media_item(
        original_path="/incoming/show.s01e01.mkv",
        title="Show",
        media_type="tv",
        season_number=1,
        episode_number=1,
    )

    movies = c.get("/api/library/movies").json()["items"]
    tv = c.get("/api/library/tv").json()["items"]
    assert len(movies) == 1
    assert len(tv) == 1
    assert tv[0]["season_number"] == 1


def test_set_watched_toggles_and_persists(client):
    c, db = client
    media_id = _seed_movie(db)

    resp = c.post(f"/api/library/{media_id}/watched", json={"watched": True})
    assert resp.status_code == 200
    assert resp.json()["watched"] is True

    resp2 = c.get("/api/library/movies")
    assert resp2.json()["items"][0]["watched"] is True

    c.post(f"/api/library/{media_id}/watched", json={"watched": False})
    resp3 = c.get("/api/library/movies")
    assert resp3.json()["items"][0]["watched"] is False


def test_set_watched_404_for_missing_item(client):
    c, _ = client
    resp = c.post("/api/library/9999/watched", json={"watched": True})
    assert resp.status_code == 404


def test_list_handles_missing_metadata_gracefully(client):
    c, db = client
    _seed_movie(db, metadata=None)

    resp = c.get("/api/library/movies")
    item = resp.json()["items"][0]
    assert item["poster_path"] is None
    assert item["overview"] == ""
