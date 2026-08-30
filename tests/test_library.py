from __future__ import annotations

import json
from pathlib import Path
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
    assert item["vote_average"] is None
    assert item["genres"] == []


def test_list_movies_returns_vote_average_and_genres(client):
    c, db = client
    _seed_movie(db, metadata={"poster_path": "/p.jpg", "overview": "", "vote_average": 8.1, "genres": ["Action"]})

    item = c.get("/api/library/movies").json()["items"][0]
    assert item["vote_average"] == 8.1
    assert item["genres"] == ["Action"]
    assert item["watched"] is False
    assert item["file_name"] == "Movie (2020).mkv"
    assert item["size_bytes"] is None  # final_path doesn't point at a real file in this fixture


def test_list_movies_reports_real_file_size(client):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x" * 1234)
    db.create_media_item(
        original_path="x", final_path=str(video), title="Movie", year=2020, media_type="movie",
        metadata={"poster_path": None, "overview": ""},
    )

    resp = c.get("/api/library/movies")
    item = resp.json()["items"][0]
    assert item["file_name"] == "Movie (2020).mkv"
    assert item["size_bytes"] == 1234


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


def test_list_tv_returns_episode_title(client):
    c, db = client
    db.create_media_item(
        original_path="/incoming/show.s01e01.mkv",
        title="Show",
        media_type="tv",
        season_number=1,
        episode_number=1,
        metadata={"poster_path": None, "overview": "", "episode_title": "Pilot"},
    )

    tv = c.get("/api/library/tv").json()["items"]
    assert tv[0]["episode_title"] == "Pilot"


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


def _archive_movies_dir(c) -> Path:
    return Path(c.get("/api/settings").json()["archive_movies"])


def test_browse_finds_untracked_file(client):
    c, _ = client
    archive_dir = _archive_movies_dir(c)
    (archive_dir / "Random.Movie.2019.mkv").write_bytes(b"data")

    resp = c.get("/api/library/browse", params={"media_type": "movie"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["tracked"] is False
    assert items[0]["media_id"] is None


def test_browse_marks_tracked_file(client):
    c, db = client
    archive_dir = _archive_movies_dir(c)
    organized = archive_dir / "Movie (2020)"
    organized.mkdir()
    video = organized / "Movie (2020).mkv"
    video.write_bytes(b"data")
    media_id = db.create_media_item(
        original_path="x", final_path=str(video), title="Movie", year=2020, media_type="movie"
    )

    resp = c.get("/api/library/browse", params={"media_type": "movie"})
    item = resp.json()["items"][0]
    assert item["tracked"] is True
    assert item["media_id"] == media_id
    assert item["tmdb_id"] is None  # tracked but not yet TMDB-matched


def test_browse_reports_tmdb_id_for_matched_file(client):
    c, db = client
    archive_dir = _archive_movies_dir(c)
    organized = archive_dir / "Movie (2020)"
    organized.mkdir()
    video = organized / "Movie (2020).mkv"
    video.write_bytes(b"data")
    db.create_media_item(
        original_path="x", final_path=str(video), title="Movie", year=2020, media_type="movie", tmdb_id=603
    )

    resp = c.get("/api/library/browse", params={"media_type": "movie"})
    item = resp.json()["items"][0]
    assert item["tmdb_id"] == 603


def test_browse_untracked_file_has_no_tmdb_id(client):
    c, _ = client
    (_archive_movies_dir(c) / "Random.Movie.2019.mkv").write_bytes(b"data")

    resp = c.get("/api/library/browse", params={"media_type": "movie"})
    item = resp.json()["items"][0]
    assert item["tracked"] is False
    assert item["tmdb_id"] is None


def test_browse_filters_by_media_type(client):
    c, _ = client
    (_archive_movies_dir(c) / "movie.mkv").write_bytes(b"1")

    tv_items = c.get("/api/library/browse", params={"media_type": "tv"}).json()["items"]
    assert tv_items == []


def test_delete_file_removes_untracked_file(client):
    c, _ = client
    video = _archive_movies_dir(c) / "junk.mkv"
    video.write_bytes(b"data")

    resp = c.post("/api/library/delete-file", json={"path": str(video)})
    assert resp.status_code == 200
    assert not video.exists()


def test_delete_file_removes_tracked_file_and_db_row(client):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.write_bytes(b"data")
    media_id = db.create_media_item(
        original_path="x", final_path=str(video), title="Movie", media_type="movie"
    )

    resp = c.post("/api/library/delete-file", json={"path": str(video)})
    assert resp.status_code == 200
    assert not video.exists()
    assert db.get_media_item(media_id) is None

    ops = db.list_operations(operation_type="delete")
    assert ops[0]["status"] == "success"


def test_delete_file_404_when_missing(client):
    c, _ = client
    resp = c.post("/api/library/delete-file", json={"path": str(_archive_movies_dir(c) / "nope.mkv")})
    assert resp.status_code == 404


def test_delete_file_rejects_path_outside_media_dirs(client, tmp_path):
    c, _ = client
    outside = tmp_path.parent / "outside.mkv"
    outside.write_bytes(b"data")

    resp = c.post("/api/library/delete-file", json={"path": str(outside)})
    assert resp.status_code == 400
    assert outside.exists()


def test_list_movies_auto_adopts_untracked_archive_files(client):
    """Stage 1 of the manual library browser: a file already sitting in
    archive_movies (e.g. imported by Radarr) shows up in the gallery on the
    next load, with no TMDB match yet (that's the background backfill's job)."""
    c, _ = client
    (_archive_movies_dir(c) / "Radarr.Movie.2019.mkv").write_bytes(b"data")

    resp = c.get("/api/library/movies")
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Radarr Movie"
    assert items[0]["poster_path"] is None  # not matched yet


def test_list_movies_does_not_reduplicate_on_repeated_calls(client):
    c, _ = client
    (_archive_movies_dir(c) / "Movie.2020.mkv").write_bytes(b"data")

    c.get("/api/library/movies")
    resp = c.get("/api/library/movies")

    assert len(resp.json()["items"]) == 1


def test_metadata_status_reports_pending_count(client):
    c, _ = client
    (_archive_movies_dir(c) / "Movie.2020.mkv").write_bytes(b"data")
    c.get("/api/library/movies")  # triggers adoption

    resp = c.get("/api/library/metadata-status")
    assert resp.json()["pending"] == 1

    resp_filtered = c.get("/api/library/metadata-status", params={"media_type": "tv"})
    assert resp_filtered.json()["pending"] == 0


def _organize_item(source: Path, dest: Path, **overrides) -> dict:
    defaults = dict(
        source_path=str(source),
        dest_path=str(dest),
        media_type="movie",
        title="Movie",
        year=2020,
        tmdb_id=42,
        poster_path="/poster.jpg",
        overview="Plot.",
    )
    defaults.update(overrides)
    return defaults


def test_organize_moves_untracked_file_and_creates_one_row(client):
    c, db = client
    archive_dir = _archive_movies_dir(c)
    source = archive_dir / "Movie.2020.720p.mkv"
    source.write_bytes(b"data")
    dest = archive_dir / "Movie (2020)" / "Movie (2020).mkv"

    resp = c.post("/api/library/organize", json={"items": [_organize_item(source, dest)]})
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "success"

    assert not source.exists()
    assert dest.exists()
    items = db.list_media_items(media_type="movie")
    assert len(items) == 1
    assert items[0]["final_path"] == str(dest)


def test_organize_updates_existing_tracked_row_without_duplicating(client):
    c, db = client
    archive_dir = _archive_movies_dir(c)
    source = archive_dir / "Movie.2020.720p.mkv"
    source.write_bytes(b"data")
    existing_id = db.create_media_item(
        original_path=str(source), final_path=str(source), title="Movie", year=2020, media_type="movie"
    )
    dest = archive_dir / "Movie (2020)" / "Movie (2020).mkv"

    c.post("/api/library/organize", json={"items": [_organize_item(source, dest)]})

    items = db.list_media_items(media_type="movie")
    assert len(items) == 1
    assert items[0]["id"] == existing_id
    assert items[0]["final_path"] == str(dest)


def test_organize_is_reflected_in_browse_and_no_longer_duplicated(client):
    """After organizing, a rescan via Browse shouldn't show the file twice
    (once at the old path, once at the new) -- it moved, it didn't copy."""
    c, _ = client
    archive_dir = _archive_movies_dir(c)
    source = archive_dir / "Movie.2020.720p.mkv"
    source.write_bytes(b"data")
    dest = archive_dir / "Movie (2020)" / "Movie (2020).mkv"

    c.post("/api/library/organize", json={"items": [_organize_item(source, dest)]})

    browse = c.get("/api/library/browse", params={"media_type": "movie"}).json()
    assert len(browse["items"]) == 1
    assert browse["items"][0]["path"] == str(dest)
    assert browse["items"][0]["tracked"] is True


def test_file_info_returns_size_and_probe_result(client, monkeypatch):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x" * 999)
    media_id = _seed_movie(db, final_path=str(video))

    from app.core import media_probe as mp

    fake_probe = mp.MediaProbeResult(duration_seconds=120.5, width=1920, height=1080, video_codec="h264", audio_codec="aac", bitrate=5000, container="mov,mp4")
    monkeypatch.setattr(mp, "probe_file", lambda path: fake_probe)
    monkeypatch.setattr(mp, "ffprobe_available", lambda: True)

    resp = c.get(f"/api/library/{media_id}/file-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_name"] == "Movie (2020).mkv"
    assert body["size_bytes"] == 999
    assert body["duration_seconds"] == 120.5
    assert body["width"] == 1920
    assert body["video_codec"] == "h264"
    assert body["probe_available"] is True


def test_file_info_works_without_ffprobe(client, monkeypatch):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x" * 42)
    media_id = _seed_movie(db, final_path=str(video))

    from app.core import media_probe as mp

    monkeypatch.setattr(mp, "probe_file", lambda path: None)
    monkeypatch.setattr(mp, "ffprobe_available", lambda: False)

    resp = c.get(f"/api/library/{media_id}/file-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["size_bytes"] == 42
    assert body["duration_seconds"] is None
    assert body["probe_available"] is False


def test_file_info_404_when_file_missing(client):
    c, db = client
    media_id = _seed_movie(db, final_path=str(_archive_movies_dir(c) / "Gone.mkv"))

    resp = c.get(f"/api/library/{media_id}/file-info")
    assert resp.status_code == 404


def test_file_info_404_for_unknown_item(client):
    c, _ = client
    resp = c.get("/api/library/999999/file-info")
    assert resp.status_code == 404


def test_file_info_caches_probe_result_and_exposes_resolution(client, monkeypatch):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x" * 100)
    media_id = _seed_movie(db, final_path=str(video))

    from app.core import media_probe as mp

    fake_probe = mp.MediaProbeResult(width=1920, height=1080, video_codec="h264")
    monkeypatch.setattr(mp, "probe_file", lambda path: fake_probe)
    monkeypatch.setattr(mp, "ffprobe_available", lambda: True)

    c.get(f"/api/library/{media_id}/file-info")

    item = db.get_media_item(media_id)
    assert json.loads(item["metadata"])["height"] == 1080

    gallery_item = c.get("/api/library/movies").json()["items"][0]
    assert gallery_item["resolution"] == "1080p"


def test_rematch_preserves_cached_resolution(client, monkeypatch):
    c, db = client
    media_id = _seed_movie(db)
    db.update_media_item(media_id, metadata={"poster_path": "/p.jpg", "overview": "", "width": 1920, "height": 1080})

    fake_tmdb = MagicMock(mode="scraper")
    fake_tmdb.get_movie_details.return_value = MagicMock(
        tmdb_id=42, title="Real Title", year=2020, poster_path="/p2.jpg", overview="Plot", raw={}
    )
    app.dependency_overrides[get_tmdb_client] = lambda: fake_tmdb
    try:
        c.post("/api/library/rematch-tmdb", json={"ids": [media_id], "tmdb_id": 42, "media_type": "movie"})
    finally:
        app.dependency_overrides[get_tmdb_client] = lambda: MagicMock(mode="scraper")

    item = db.get_media_item(media_id)
    metadata = json.loads(item["metadata"])
    assert metadata["height"] == 1080
    assert metadata["poster_path"] == "/p2.jpg"


def test_retry_failed_matches_resets_cooldown(client):
    c, db = client
    failed_id = db.create_media_item(original_path="a", title="A", media_type="movie")
    db.update_media_item(failed_id, match_attempted_at="2026-01-01T00:00:00+00:00")

    resp = c.post("/api/library/retry-failed-matches")
    assert resp.status_code == 200
    assert resp.json()["reset"] == 1
    assert db.get_media_item(failed_id)["match_attempted_at"] is None


def test_export_returns_all_media_items(client):
    c, db = client
    _seed_movie(db)
    db.create_media_item(
        original_path="/incoming/show.s01e01.mkv", title="Show", media_type="tv",
        season_number=1, episode_number=1,
    )

    resp = c.get("/api/library/export")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["exported_at"]
    movie = next(i for i in body["items"] if i["media_type"] == "movie")
    assert movie["title"] == "Movie"
    assert movie["metadata"]["poster_path"] == "/poster.jpg"


def test_import_creates_new_items(client):
    c, db = client
    export_item = dict(
        original_path="/incoming/x.mkv", title="Imported Movie", year=2021,
        tmdb_id=None, media_type="movie", season_number=None, episode_number=None,
        final_path="/archive/Imported Movie (2021)/Imported Movie (2021).mkv",
        archived_at=None, watched=False, metadata={}, imdb_id=None, manual_override=False,
    )

    resp = c.post("/api/library/import", json={"items": [export_item]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0

    items = db.list_media_items(media_type="movie")
    assert len(items) == 1
    assert items[0]["title"] == "Imported Movie"


def test_import_skips_item_already_tracked_by_final_path(client):
    c, db = client
    existing = _seed_movie(db)
    export_item = dict(
        original_path="/incoming/movie.mkv", title="Movie", year=2020,
        tmdb_id=None, media_type="movie", season_number=None, episode_number=None,
        final_path="/archive/Movie (2020)/Movie (2020).mkv",
        archived_at=None, watched=False, metadata={}, imdb_id=None, manual_override=False,
    )

    resp = c.post("/api/library/import", json={"items": [export_item]})
    body = resp.json()
    assert body["imported"] == 0
    assert body["skipped"] == 1
    assert len(db.list_media_items(media_type="movie")) == 1
    assert db.get_media_item(existing)["title"] == "Movie"


def test_export_import_round_trips(client):
    c, db = client
    _seed_movie(db)

    exported = c.get("/api/library/export").json()

    # Simulate restoring into a fresh, empty database.
    for item_id in [i["id"] for i in db.list_media_items()]:
        db.delete_media_item(item_id)
    assert db.list_media_items() == []

    resp = c.post("/api/library/import", json={"items": exported["items"]})
    assert resp.json()["imported"] == 1
    assert db.list_media_items(media_type="movie")[0]["title"] == "Movie"


def test_manual_override_sets_title_and_clears_tmdb_id(client):
    c, db = client
    media_id = db.create_media_item(
        original_path="a", title="Untitled Home Video", media_type="movie", tmdb_id=999,
    )

    resp = c.post(f"/api/library/{media_id}/override", json={"title": "Family Vacation 2019", "year": 2019})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Family Vacation 2019"
    assert body["year"] == 2019
    assert body["tmdb_id"] is None
    assert body["manual_override"] is True


def test_manual_override_excluded_from_unmatched_count(client):
    c, db = client
    media_id = db.create_media_item(original_path="a", title="X", media_type="movie")
    c.post(f"/api/library/{media_id}/override", json={"title": "Custom Title"})

    resp = c.get("/api/library/metadata-status")
    assert resp.json()["pending"] == 0


def test_manual_override_404_for_missing_item(client):
    c, _ = client
    resp = c.post("/api/library/9999/override", json={"title": "X"})
    assert resp.status_code == 404


def test_rematch_tmdb_clears_manual_override(client):
    c, db = client
    media_id = db.create_media_item(original_path="a", title="X", media_type="movie", manual_override=1)

    fake_tmdb = MagicMock(mode="scraper")
    fake_tmdb.get_movie_details.return_value = MagicMock(
        tmdb_id=42, title="Real Title", year=2020, poster_path="/p.jpg", overview="Plot", raw={}
    )
    app.dependency_overrides[get_tmdb_client] = lambda: fake_tmdb
    try:
        resp = c.post("/api/library/rematch-tmdb", json={"ids": [media_id], "tmdb_id": 42, "media_type": "movie"})
        assert resp.status_code == 200
    finally:
        app.dependency_overrides[get_tmdb_client] = lambda: MagicMock(mode="scraper")

    assert db.get_media_item(media_id)["manual_override"] == 0


def test_health_reports_orphan_when_final_path_missing(client):
    c, db = client
    _seed_movie(db, final_path=str(_archive_movies_dir(c) / "Gone.mkv"))

    resp = c.get("/api/library/health")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["orphans"]) == 1
    assert body["duplicates"] == []


def test_health_ignores_item_whose_file_exists(client):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"data")
    _seed_movie(db, final_path=str(video))

    resp = c.get("/api/library/health")
    assert resp.json()["orphans"] == []


def test_health_reports_duplicate_group_for_same_tmdb_episode(client):
    c, db = client
    db.create_media_item(
        original_path="a", final_path="/archive/a.mkv", title="Show", media_type="tv",
        tmdb_id=42, season_number=1, episode_number=1,
    )
    db.create_media_item(
        original_path="b", final_path="/archive/b.mkv", title="Show", media_type="tv",
        tmdb_id=42, season_number=1, episode_number=1,
    )

    resp = c.get("/api/library/health")
    duplicates = resp.json()["duplicates"]
    assert len(duplicates) == 1
    assert len(duplicates[0]) == 2


def test_health_does_not_group_items_without_tmdb_id(client):
    c, db = client
    db.create_media_item(original_path="a", final_path="/archive/a.mkv", title="A", media_type="movie")
    db.create_media_item(original_path="b", final_path="/archive/b.mkv", title="B", media_type="movie")

    resp = c.get("/api/library/health")
    assert resp.json()["duplicates"] == []


def test_cleanup_orphans_removes_only_missing_files(client):
    c, db = client
    video = _archive_movies_dir(c) / "Movie (2020).mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"data")
    kept_id = _seed_movie(db, final_path=str(video))
    orphan_id = _seed_movie(db, final_path=str(_archive_movies_dir(c) / "Gone.mkv"))

    resp = c.post("/api/library/orphans/cleanup")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1

    assert db.get_media_item(kept_id) is not None
    assert db.get_media_item(orphan_id) is None
    ops = db.list_operations(operation_type="delete")
    assert json.loads(ops[0]["details"])["reason"] == "orphan_cleanup"
