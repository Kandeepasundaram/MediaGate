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


def test_create_and_list_universe(client):
    c, _db, _tmdb = client
    resp = c.post("/api/universes", json={"name": "Vampire Diaries", "media_type": "tv"})
    assert resp.status_code == 200
    universe = resp.json()
    assert universe["name"] == "Vampire Diaries"
    assert universe["media_type"] == "tv"
    assert universe["member_count"] == 0

    resp = c.get("/api/universes", params={"media_type": "tv"})
    assert resp.status_code == 200
    assert len(resp.json()["universes"]) == 1

    resp = c.get("/api/universes", params={"media_type": "movie"})
    assert resp.json()["universes"] == []


def test_add_member_upserts_tracker_row(client):
    c, db, _tmdb = client
    universe = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()

    resp = c.post(
        f"/api/universes/{universe['id']}/members",
        json={"tmdb_id": 1771, "title": "Captain America: Civil War", "poster_path": "/poster.jpg"},
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["universe"]["member_count"] == 1
    assert detail["members"][0]["tmdb_id"] == 1771
    assert detail["members"][0]["title"] == "Captain America: Civil War"

    # Adding a member auto-registers it with the existing tracker engine.
    tracker_row = db.get_tracker(1771, "movie")
    assert tracker_row is not None
    assert tracker_row["title"] == "Captain America: Civil War"


def test_add_member_is_idempotent(client):
    c, _db, _tmdb = client
    universe = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()
    payload = {"tmdb_id": 1771, "title": "Captain America: Civil War"}
    c.post(f"/api/universes/{universe['id']}/members", json=payload)
    resp = c.post(f"/api/universes/{universe['id']}/members", json=payload)
    assert resp.json()["universe"]["member_count"] == 1


def test_remove_member_leaves_tracker_row_intact(client):
    c, db, _tmdb = client
    universe = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()
    detail = c.post(
        f"/api/universes/{universe['id']}/members", json={"tmdb_id": 1771, "title": "Civil War"}
    ).json()
    member_id = detail["members"][0]["id"]

    resp = c.delete(f"/api/universes/{universe['id']}/members/{member_id}")
    assert resp.status_code == 200
    assert resp.json()["universe"]["member_count"] == 0
    # Removing from the universe doesn't stop tracking the title standalone.
    assert db.get_tracker(1771, "movie") is not None


def test_delete_universe_cascades_members(client):
    c, db, _tmdb = client
    universe = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()
    c.post(f"/api/universes/{universe['id']}/members", json={"tmdb_id": 1771, "title": "Civil War"})

    resp = c.delete(f"/api/universes/{universe['id']}")
    assert resp.status_code == 200
    assert c.get(f"/api/universes/{universe['id']}").status_code == 404
    assert db.list_universe_members(universe["id"]) == []


def test_get_universe_detail_404_for_missing(client):
    c, _db, _tmdb = client
    assert c.get("/api/universes/999").status_code == 404


def test_suggestions_require_tmdb_api_mode(client):
    c, _db, _tmdb = client
    universe = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()
    resp = c.get(f"/api/universes/{universe['id']}/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_configured"] is False
    assert body["items"] == []


def test_movie_suggestions_from_collection(client):
    c, _db, tmdb = client
    tmdb.mode = "api"
    universe = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()
    c.post(f"/api/universes/{universe['id']}/members", json={"tmdb_id": 1771, "title": "Civil War"})

    tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=1771, title="Civil War", media_type="movie", year=2016,
        raw={"belongs_to_collection": {"id": 131296}},
    )
    tmdb.get_collection_movies.return_value = [
        MediaResult(tmdb_id=1771, title="Civil War", media_type="movie", year=2016),
        MediaResult(tmdb_id=1772, title="Avengers: Infinity War", media_type="movie", year=2018),
    ]

    resp = c.get(f"/api/universes/{universe['id']}/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_configured"] is True
    # The member itself is excluded; only the new collection entry remains.
    assert [i["tmdb_id"] for i in body["items"]] == [1772]
    tmdb.get_collection_movies.assert_called_once_with(131296)


def test_movie_suggestions_excludes_titles_already_in_another_universe(client):
    c, _db, tmdb = client
    tmdb.mode = "api"
    u1 = c.post("/api/universes", json={"name": "MCU", "media_type": "movie"}).json()
    u2 = c.post("/api/universes", json={"name": "DCU", "media_type": "movie"}).json()
    c.post(f"/api/universes/{u1['id']}/members", json={"tmdb_id": 1771, "title": "Civil War"})
    # Already tracked in a different movie universe -- must not be re-suggested.
    c.post(f"/api/universes/{u2['id']}/members", json={"tmdb_id": 1772, "title": "Man of Steel"})

    tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=1771, title="Civil War", media_type="movie", year=2016,
        raw={"belongs_to_collection": {"id": 131296}},
    )
    tmdb.get_collection_movies.return_value = [
        MediaResult(tmdb_id=1772, title="Man of Steel", media_type="movie", year=2013),
        MediaResult(tmdb_id=1773, title="Iron Man", media_type="movie", year=2008),
    ]

    resp = c.get(f"/api/universes/{u1['id']}/suggestions")
    assert [i["tmdb_id"] for i in resp.json()["items"]] == [1773]


def test_tv_suggestions_from_similar_titles(client):
    c, _db, tmdb = client
    tmdb.mode = "api"
    universe = c.post("/api/universes", json={"name": "Vampire Diaries", "media_type": "tv"}).json()
    c.post(f"/api/universes/{universe['id']}/members", json={"tmdb_id": 1, "title": "The Vampire Diaries"})

    tmdb.get_similar_titles.return_value = [
        MediaResult(tmdb_id=2, title="The Originals", media_type="tv", year=2013),
    ]

    resp = c.get(f"/api/universes/{universe['id']}/suggestions")
    body = resp.json()
    assert body["tmdb_configured"] is True
    assert [i["title"] for i in body["items"]] == ["The Originals"]
    tmdb.get_similar_titles.assert_called_once_with(1, "tv")
