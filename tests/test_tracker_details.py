from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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
from app.core.tmdb_client import MediaResult
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
        notifications=NotificationsConfig(),
        omdb=OMDbConfig(),
        backup=BackupConfig(),
        media_server=MediaServerConfig(),
        logging=LoggingConfig(file=tmp_path / "test.log"),
        server=ServerConfig(),
        config_path=tmp_path / "config.yaml",
    )
    db = Database(config.database_path)
    db.init_db()

    fake_tmdb = MagicMock(mode="scraper")

    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_tmdb_client] = lambda: fake_tmdb

    with TestClient(app) as c:
        yield c, db, fake_tmdb

    app.dependency_overrides.clear()



def test_tv_details_falls_back_to_owned_counts_without_tmdb_data(client):
    c, db, tmdb = client
    tmdb.get_tv_details.return_value = None
    db.upsert_tracker(tmdb_id=10, media_type="tv", title="Show", current_season_archived=1)
    for season, episode in [(1, 1), (1, 2), (2, 1)]:
        db.create_media_item(
            original_path=f"/x/{season}{episode}", final_path=f"/y/{season}{episode}", tmdb_id=10,
            media_type="tv", title="Show", season_number=season, episode_number=episode,
        )
    tracker_id = db.get_tracker(10, "tv")["id"]

    body = c.get(f"/api/tracker/{tracker_id}/details").json()

    assert body["media_type"] == "tv"
    assert body["owned_episodes"] == 3
    assert body["data_available"] is False
    assert body["pending_episodes"] is None
    assert [(s["season_number"], s["owned"]) for s in body["seasons"]] == [(1, 2), (2, 1)]


def test_details_unknown_tracker_is_404(client):
    c, _db, _tmdb = client
    assert c.get("/api/tracker/999/details").status_code == 404


def test_tv_details_reports_per_season_watch_state(client):
    c, db, tmdb = client
    tmdb.get_tv_details.return_value = None
    db.upsert_tracker(tmdb_id=11, media_type="tv", title="Show")
    ids = [
        db.create_media_item(
            original_path=f"/x/{e}", final_path=f"/y/{e}", tmdb_id=11,
            media_type="tv", title="Show", season_number=1, episode_number=e,
        )
        for e in (1, 2)
    ]
    db.update_media_item(ids[0], watched=1)
    tracker_id = db.get_tracker(11, "tv")["id"]

    season = c.get(f"/api/tracker/{tracker_id}/details").json()["seasons"][0]

    assert season["watched"] == 1
    assert sorted(season["episode_ids"]) == sorted(ids)


def test_details_include_watched_through_bookmark(client):
    c, db, tmdb = client
    tmdb.get_tv_details.return_value = None
    db.upsert_tracker(tmdb_id=12, media_type="tv", title="Show")
    tracker_id = db.get_tracker(12, "tv")["id"]
    c.post(f"/api/tracker/{tracker_id}/watch-progress", json={"season": 2, "episode": None})

    body = c.get(f"/api/tracker/{tracker_id}/details").json()

    assert (body["watched_through_season"], body["watched_through_episode"]) == (2, None)
