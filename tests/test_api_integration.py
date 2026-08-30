from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config_loader import (
    AppConfig,
    LoggingConfig,
    NotificationsConfig,
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
    incoming_movies = tmp_path / "incoming" / "movies"
    incoming_tv = tmp_path / "incoming" / "tv"
    archive_movies = tmp_path / "archive" / "movies"
    archive_tv = tmp_path / "archive" / "tv"
    for d in (incoming_movies, incoming_tv, archive_movies, archive_tv):
        d.mkdir(parents=True)

    config = AppConfig(
        paths=PathsConfig(
            incoming_movies=incoming_movies,
            incoming_tv=incoming_tv,
            archive_movies=archive_movies,
            archive_tv=archive_tv,
        ),
        database_path=tmp_path / "test.db",
        tmdb=TMDBConfig(api_key="", language="en-US"),
        subtitles=SubtitlesConfig(),
        tracker=TrackerConfig(),
        notifications=NotificationsConfig(),
        logging=LoggingConfig(file=tmp_path / "test.log"),
        server=ServerConfig(),
        config_path=tmp_path / "config.yaml",
    )
    config.config_path.write_text("", encoding="utf-8")
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
        yield c, incoming_movies

    app.dependency_overrides.clear()


def test_status_endpoint(client):
    c, _ = client
    resp = c.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["tmdb_mode"] == "scraper"


def test_scan_preview_confirm_flow(client):
    c, incoming_movies = client

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
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


def test_preview_flags_duplicate_against_existing_media_item(client):
    c, incoming_movies = client

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    video2 = incoming_movies / "Sample.Movie.2020.720p.mkv"
    video2.write_bytes(b"other copy")
    preview2 = c.post("/api/archive/preview", json={"paths": [str(video2)]}).json()

    assert preview2["items"][0]["duplicate"] is True


def test_preview_not_duplicate_for_different_year(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    assert preview["items"][0]["duplicate"] is False


def test_preview_honors_tmdb_override(client):
    c, incoming_movies = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=777, title="Override Title", media_type="movie", year=1999, overview="different match"
    )

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post(
        "/api/archive/preview",
        json={"paths": [str(video)], "tmdb_overrides": {str(video): 777}},
    ).json()

    item = preview["items"][0]
    assert item["tmdb_id"] == 777
    assert item["title"] == "Override Title"


def test_search_tmdb_endpoint_returns_candidates(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [
        MediaResult(tmdb_id=1, title="A", media_type="movie", year=2000),
        MediaResult(tmdb_id=2, title="B", media_type="movie", year=2001),
    ]

    resp = c.get("/api/archive/search", params={"title": "A", "media_type": "movie"})
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 2


def test_undo_archive_deletes_copy_and_media_item(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    op_id = c.get("/api/archive/history").json()["operations"][0]["id"]
    dest_path = Path(preview["items"][0]["dest_path"])
    assert dest_path.exists()

    undo_resp = c.post(f"/api/archive/history/{op_id}/undo")
    assert undo_resp.status_code == 200
    assert not dest_path.exists()
    assert c.get("/api/stats").json()["total_movies"] == 0


def test_undo_twice_fails_second_time(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    op_id = c.get("/api/archive/history").json()["operations"][0]["id"]

    assert c.post(f"/api/archive/history/{op_id}/undo").status_code == 200
    assert c.post(f"/api/archive/history/{op_id}/undo").status_code == 400


def test_history_filters_by_operation_type(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    all_ops = c.get("/api/archive/history").json()["operations"]
    archive_ops = c.get("/api/archive/history", params={"operation_type": "archive"}).json()["operations"]
    rename_ops = c.get("/api/archive/history", params={"operation_type": "rename"}).json()["operations"]
    assert len(all_ops) == 1
    assert len(archive_ops) == 1
    assert rename_ops == []


def test_tracker_mute_excludes_from_notifications_but_stays_listed(client):
    c, _ = client
    add_resp = c.post("/api/tracker/add", json={"tmdb_id": 5, "media_type": "tv", "title": "Show"})
    tracker_id = add_resp.json()["tracker"]["id"]

    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=5, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )
    c.post(f"/api/tracker/{tracker_id}/check-now")
    assert len(c.get("/api/tracker/notifications").json()["notifications"]) == 1

    c.post(f"/api/tracker/{tracker_id}/mute", json={"muted": True})
    assert c.get("/api/tracker/notifications").json()["notifications"] == []
    listed = c.get("/api/tracker/list").json()["tracked"]
    assert len(listed) == 1
    assert listed[0]["muted"] is True


def test_watched_batch_update(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    resp = c.post("/api/library/watched-batch", json={"ids": [item_id], "watched": True})
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    assert c.get("/api/library/movies").json()["items"][0]["watched"] is True


def test_settings_webhook_url_round_trips(client):
    c, _ = client
    resp = c.post("/api/settings", json={"webhook_url": "https://example.com/hook"})
    assert resp.status_code == 200
    assert resp.json()["webhook_url"] == "https://example.com/hook"


def test_permissions_check_reports_free_space(client):
    c, _ = client
    resp = c.get("/api/settings/permissions-check")
    body = resp.json()
    assert all(p["free_bytes"] is not None and p["free_bytes"] > 0 for p in body["paths"])


def test_rematch_imdb_updates_unmatched_item(client):
    c, incoming_movies = client
    video = incoming_movies / "Unmatched.File.mkv"
    video.write_bytes(b"data")

    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = []  # simulate no automatic match
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item = c.get("/api/library/movies").json()["items"][0]
    assert item["tmdb_id"] is None

    fake_tmdb.find_by_imdb_id.return_value = MediaResult(
        tmdb_id=278, title="The Shawshank Redemption", media_type="movie", year=1994, overview="...", poster_path="/p.jpg"
    )
    resp = c.post(
        "/api/library/rematch-imdb",
        json={"ids": [item["id"]], "imdb_id": "tt0111161", "media_type": "movie"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["tmdb_id"] == 278

    updated_item = c.get("/api/library/movies").json()["items"][0]
    assert updated_item["tmdb_id"] == 278
    assert updated_item["title"] == "The Shawshank Redemption"
    assert updated_item["poster_path"] == "/p.jpg"


def test_rematch_imdb_applies_to_multiple_episode_ids(client):
    c, incoming_movies = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()

    tv_dir = incoming_movies.parent / "tv"
    tv_dir.mkdir(exist_ok=True)
    for ep in ("S01E01", "S01E02"):
        f = tv_dir / f"Show.{ep}.mkv"
        f.write_bytes(b"data")
        fake_tmdb.search_tv.return_value = []
        preview = c.post("/api/archive/preview", json={"paths": [str(f)]}).json()
        c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    ids = [i["id"] for i in c.get("/api/library/tv").json()["items"]]
    assert len(ids) == 2

    fake_tmdb.find_by_imdb_id.return_value = MediaResult(
        tmdb_id=99, title="Show", media_type="tv", year=2020, overview="desc", poster_path="/s.jpg"
    )
    resp = c.post("/api/library/rematch-imdb", json={"ids": ids, "imdb_id": "tt1234567", "media_type": "tv"})
    assert resp.json()["updated"] == 2
    assert all(i["tmdb_id"] == 99 for i in c.get("/api/library/tv").json()["items"])


def test_rematch_imdb_404_when_not_found(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.find_by_imdb_id.return_value = None

    resp = c.post("/api/library/rematch-imdb", json={"ids": [1], "imdb_id": "tt0000000", "media_type": "movie"})
    assert resp.status_code == 404
