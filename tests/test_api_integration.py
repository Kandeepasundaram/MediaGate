from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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
from app.core.fs_watcher import NewFileTracker
from app.core.omdb_client import OMDbClient
from app.core.opensubtitles_client import SubtitleMatch
from app.core.tmdb_client import MediaResult
from app.core.tvmaze_client import TVmazeEpisode, TVmazeShowInfo
from app.database import Database
from app.dependencies import (
    get_config,
    get_database,
    get_new_file_tracker,
    get_omdb_client,
    get_opensubtitles_client,
    get_tmdb_client,
    get_tvmaze_client,
)
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
        omdb=OMDbConfig(),
        backup=BackupConfig(),
        media_server=MediaServerConfig(),
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
    # Unconfigured by default -- a bare MagicMock() is not a valid stand-in
    # for "no TMDB result" (e.g. GET /api/tracker/list's poster backfill
    # checks `media is None`); tests that need a real lookup set these
    # explicitly.
    fake_tmdb.get_movie_details.return_value = None
    fake_tmdb.get_tv_details.return_value = None

    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_tmdb_client] = lambda: fake_tmdb
    app.dependency_overrides[get_omdb_client] = lambda: OMDbClient(api_key="")

    with TestClient(app) as c:
        yield c, incoming_movies

    app.dependency_overrides.clear()


def test_status_endpoint(client):
    c, _ = client
    resp = c.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_mode"] == "scraper"
    assert isinstance(body["ffprobe_available"], bool)
    assert body["uptime_seconds"] >= 0
    assert body["next_tracker_check_in_seconds"] > 0


def test_new_files_status_reflects_tracker_count(client):
    c, _ = client
    tracker = NewFileTracker()
    app.dependency_overrides[get_new_file_tracker] = lambda: tracker

    assert c.get("/api/scan/new-files").json()["count"] == 0

    tracker.add(Path("/incoming/movies/New.Movie.mkv"))
    assert c.get("/api/scan/new-files").json()["count"] == 1


def test_scan_clears_new_files_tracker(client):
    c, incoming_movies = client
    tracker = NewFileTracker()
    app.dependency_overrides[get_new_file_tracker] = lambda: tracker
    tracker.add(incoming_movies / "watched-in.mkv")

    c.get("/api/scan")

    assert tracker.count() == 0


def test_storage_status_reports_combined_label_for_shared_path(client):
    c, _ = client
    resp = c.get("/api/status/storage")
    assert resp.status_code == 200
    body = resp.json()
    # the fixture config points incoming and archive at different tmp_path
    # subdirectories per media type, but incoming_movies == archive_movies
    # is the documented common case -- here they're distinct dirs, so
    # expect one row per unique configured path (4 total), not merged.
    assert len(body["paths"]) == 4
    for p in body["paths"]:
        assert p["exists"] is True
        assert p["total_bytes"] > 0
        assert p["free_bytes"] >= 0


def test_storage_status_merges_rows_for_identical_paths(client):
    c, _ = client
    config = app.dependency_overrides[get_config]()
    config.paths.archive_movies = config.paths.incoming_movies

    body = c.get("/api/status/storage").json()
    merged = next(p for p in body["paths"] if p["path"] == str(config.paths.incoming_movies))
    assert merged["label"] == "Movies incoming / Movies archive"
    assert len(body["paths"]) == 3  # was 4 unique paths, now 3


def test_storage_status_reports_missing_directory(client, tmp_path):
    c, _ = client
    config = app.dependency_overrides[get_config]()
    config.paths.archive_tv = tmp_path / "does_not_exist_tv_archive"

    body = c.get("/api/status/storage").json()
    missing = next(p for p in body["paths"] if p["path"] == str(config.paths.archive_tv))
    assert missing["exists"] is False
    assert missing["total_bytes"] is None


def test_storage_status_records_snapshot_and_reports_no_forecast_on_first_call(client):
    c, _ = client
    body = c.get("/api/status/storage").json()
    for p in body["paths"]:
        assert p["days_to_full"] is None
        assert p["history_days"] == 0


def test_storage_status_forecasts_days_to_full_from_history(client):
    from datetime import datetime, timedelta, timezone

    c, _ = client
    db = app.dependency_overrides[get_database]()
    c.get("/api/status/storage")  # first snapshot, recorded for today

    # Backdate a snapshot from 5 days ago with less usage than today's real
    # (already-recorded) one, so the two-point slope is unambiguous growth.
    five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    db.execute_query(
        "UPDATE storage_snapshots SET used_bytes = ?, created_at = ? WHERE label = ?",
        (0, five_days_ago, "Movies incoming"),
    )
    db.record_storage_snapshot("Movies incoming", 1000 * 1024 * 1024 * 1024, 2000 * 1024 * 1024 * 1024)

    body = c.get("/api/status/storage").json()
    row = next(p for p in body["paths"] if p["label"] == "Movies incoming")
    assert row["history_days"] == 2
    assert row["days_to_full"] is not None
    assert row["days_to_full"] > 0


def test_storage_status_no_forecast_when_usage_shrinking(client):
    from datetime import datetime, timedelta, timezone

    c, _ = client
    db = app.dependency_overrides[get_database]()
    c.get("/api/status/storage")  # first snapshot

    five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    db.execute_query(
        "UPDATE storage_snapshots SET used_bytes = ?, created_at = ? WHERE label = ?",
        (999999999999, five_days_ago, "Movies incoming"),
    )

    body = c.get("/api/status/storage").json()
    row = next(p for p in body["paths"] if p["label"] == "Movies incoming")
    assert row["days_to_full"] is None
    assert row["history_days"] == 2


def test_storage_status_fires_low_disk_alert_when_enabled_and_below_threshold(client):
    c, _ = client
    config = app.dependency_overrides[get_config]()
    config.notifications.low_disk_alert_enabled = True
    config.notifications.low_disk_threshold_gb = 1e12  # absurdly high -- guaranteed below threshold
    config.notifications.discord_webhook_url = "https://discord/x"

    from app.core import low_disk_alert
    low_disk_alert._last_alerted.clear()
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        c.get("/api/status/storage")
    assert mock_discord.called


def test_storage_status_no_low_disk_alert_when_disabled(client):
    c, _ = client
    config = app.dependency_overrides[get_config]()
    config.notifications.low_disk_alert_enabled = False

    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        c.get("/api/status/storage")
    mock_discord.assert_not_called()


def test_background_tasks_status_defaults(client):
    c, _ = client
    resp = c.get("/api/status/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracker"]["last_check_at"] is None
    assert body["tracker"]["next_check_in_seconds"] > 0
    assert body["backfill"] == {"pending": 0, "failed": 0}
    assert body["backup"]["enabled"] is True
    assert body["backup"]["last_run_at"] is None
    assert body["maintenance"]["last_run_at"] is None


def test_background_tasks_status_reflects_last_tracker_check(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.log_operation(operation_type="tracker_check", status="success", details={"tmdb_id": 1})

    body = c.get("/api/status/tasks").json()
    assert body["tracker"]["last_check_at"] is not None
    assert body["tracker"]["last_check_status"] == "success"


def test_background_tasks_status_reflects_pending_backfill(client):
    c, incoming_movies = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = []
    video = incoming_movies / "Unmatched.File.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    body = c.get("/api/status/tasks").json()
    assert body["backfill"]["pending"] == 1  # no tmdb_id yet, not attempted by the backfill loop in this test


def test_background_tasks_status_reflects_backup_run(client, monkeypatch):
    from app.core import scheduler

    monkeypatch.setitem(scheduler._task_status, "backup", {"last_run_at": "2026-01-01T00:00:00+00:00", "last_error": None})
    c, _ = client
    body = c.get("/api/status/tasks").json()
    assert body["backup"]["last_run_at"] == "2026-01-01T00:00:00+00:00"


def test_archive_confirm_purge_subtitles_honors_configured_keep_languages(client):
    c, incoming_movies = client
    # Override the fixture's default config to keep only French subtitles --
    # confirms purge_subtitles reads config.subtitles.keep_languages instead
    # of always falling back to subtitle_purger's hardcoded English default.
    config = app.dependency_overrides[get_config]()
    config.subtitles.keep_languages = ["fr"]

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")
    en_sub = incoming_movies / "Sample.Movie.2020.1080p.en.srt"
    en_sub.write_text("english subtitle")
    fr_sub = incoming_movies / "Sample.Movie.2020.1080p.fr.srt"
    fr_sub.write_text("french subtitle")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": True})

    assert not en_sub.exists()  # not in the configured keep list -- purged
    assert fr_sub.exists()  # configured keep language -- kept


def test_archive_confirm_skips_nfo_when_disabled_in_settings(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.media_server.write_nfo_files = False

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    confirm = c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False}).json()

    dest = Path(preview["items"][0]["dest_path"])
    assert confirm["results"][0]["status"] == "success"
    assert not (dest.parent / "movie.nfo").exists()


def test_archive_confirm_writes_nfo_by_default(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    dest = Path(preview["items"][0]["dest_path"])
    assert (dest.parent / "movie.nfo").exists()


def test_archive_confirm_fetches_missing_subtitle_when_enabled(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.subtitles.keep_languages = ["en"]
    config.subtitles.auto_fetch_missing_subtitles = True

    fake_os_client = MagicMock()
    fake_os_client.enabled = True
    fake_os_client.find_subtitle.return_value = SubtitleMatch(file_id=42, language="en", release="")
    fake_os_client.download_subtitle.return_value = b"fetched subtitle content"
    app.dependency_overrides[get_opensubtitles_client] = lambda: fake_os_client

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    fetched = incoming_movies / "Sample.Movie.2020.1080p.en.srt"
    assert fetched.exists()
    assert fetched.read_bytes() == b"fetched subtitle content"
    fake_os_client.find_subtitle.assert_called_once()


def test_archive_confirm_skips_fetch_when_language_already_present(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.subtitles.keep_languages = ["en"]
    config.subtitles.auto_fetch_missing_subtitles = True

    fake_os_client = MagicMock()
    fake_os_client.enabled = True
    app.dependency_overrides[get_opensubtitles_client] = lambda: fake_os_client

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")
    (incoming_movies / "Sample.Movie.2020.1080p.en.srt").write_text("already have english")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    fake_os_client.find_subtitle.assert_not_called()


def test_archive_confirm_skips_fetch_when_disabled(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.subtitles.auto_fetch_missing_subtitles = False

    fake_os_client = MagicMock()
    fake_os_client.enabled = True
    app.dependency_overrides[get_opensubtitles_client] = lambda: fake_os_client

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    fake_os_client.find_subtitle.assert_not_called()


def test_archive_confirm_purge_subtitles_honors_per_movie_type_override(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.subtitles.keep_languages = ["en"]
    config.subtitles.keep_languages_movies = ["fr"]  # movie-specific override wins over the default above

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")
    en_sub = incoming_movies / "Sample.Movie.2020.1080p.en.srt"
    en_sub.write_text("english subtitle")
    fr_sub = incoming_movies / "Sample.Movie.2020.1080p.fr.srt"
    fr_sub.write_text("french subtitle")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": True})

    assert not en_sub.exists()  # default keep list doesn't apply -- movie override took over
    assert fr_sub.exists()


def test_archive_preview_skip_collision_policy_reports_error(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.renaming.collision_policy = "skip"

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")
    existing_dest = config.paths.archive_movies / "Sample Movie (2020)" / "Sample Movie (2020).mkv"
    existing_dest.parent.mkdir(parents=True)
    existing_dest.write_bytes(b"already archived")

    resp = c.post("/api/archive/preview", json={"paths": [str(video)]})
    body = resp.json()
    assert body["items"] == []
    assert len(body["errors"]) == 1
    assert "collision policy is 'skip'" in body["errors"][0]


def test_archive_confirm_dry_run_does_not_touch_disk_or_db(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"fake video data")

    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    dest = Path(preview["items"][0]["dest_path"])

    resp = c.post("/api/archive/confirm", json={"items": preview["items"], "dry_run": True})
    result = resp.json()["results"][0]
    assert result["status"] == "success"
    assert result["media_id"] is None

    assert video.exists()  # untouched
    assert not dest.exists()  # never created
    assert c.get("/api/archive/history").json()["operations"] == []


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
    stats = stats_resp.json()
    assert stats["total_movies"] == 1
    assert stats["movies_size_bytes"] == len(b"fake video data")
    assert stats["tv_size_bytes"] == 0
    assert stats["total_size_bytes"] == stats["movies_size_bytes"]


def test_archive_history_filters_by_status_and_date_range(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("archive", "success", "2026-01-05T00:00:00+00:00"),
    )
    db.execute_query(
        "INSERT INTO operation_log (operation_type, status, created_at) VALUES (?, ?, ?)",
        ("archive", "failed", "2026-02-10T00:00:00+00:00"),
    )

    resp = c.get("/api/archive/history", params={"status": "failed"})
    assert resp.status_code == 200
    assert len(resp.json()["operations"]) == 1
    assert resp.json()["operations"][0]["status"] == "failed"

    resp = c.get("/api/archive/history", params={"since": "2026-01-01", "until": "2026-01-31"})
    assert len(resp.json()["operations"]) == 1
    assert resp.json()["operations"][0]["created_at"] == "2026-01-05T00:00:00+00:00"


def test_stats_insights_empty_library(client):
    c, _ = client
    body = c.get("/api/stats/insights").json()
    assert body == {"top_genres": [], "resolution_breakdown": [], "growth_by_month": []}


def test_stats_insights_aggregates_genres_resolution_and_growth(client):
    c, incoming_movies = client
    db = app.dependency_overrides[get_database]()

    movie1 = incoming_movies / "movie1.mkv"
    movie1.write_bytes(b"0" * 1000)
    movie2 = incoming_movies / "movie2.mkv"
    movie2.write_bytes(b"0" * 3000)

    db.create_media_item(
        original_path="x", final_path=str(movie1), title="Movie 1", media_type="movie",
        archived_at="2026-01-15T00:00:00+00:00",
        metadata={"genres": ["Drama", "Action"], "height": 1080},
    )
    db.create_media_item(
        original_path="x", final_path=str(movie2), title="Movie 2", media_type="movie",
        archived_at="2026-01-20T00:00:00+00:00",
        metadata={"genres": ["Drama"], "height": 2160},
    )

    body = c.get("/api/stats/insights").json()

    genres = {g["genre"]: g["count"] for g in body["top_genres"]}
    assert genres == {"Drama": 2, "Action": 1}

    resolutions = {r["resolution"]: (r["count"], r["avg_size_bytes"]) for r in body["resolution_breakdown"]}
    assert resolutions["1080p"] == (1, 1000)
    assert resolutions["4K"] == (1, 3000)

    assert body["growth_by_month"] == [{"month": "2026-01", "count": 2}]


def test_stats_insights_resolution_unknown_when_no_height_probed(client):
    c, incoming_movies = client
    db = app.dependency_overrides[get_database]()
    movie = incoming_movies / "movie.mkv"
    movie.write_bytes(b"0" * 500)

    db.create_media_item(
        original_path="x", final_path=str(movie), title="Movie", media_type="movie", metadata={},
    )

    body = c.get("/api/stats/insights").json()
    resolutions = {r["resolution"]: r["count"] for r in body["resolution_breakdown"]}
    assert resolutions == {"Unknown": 1}


def test_stats_insights_skips_missing_files_for_resolution_stats(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(
        original_path="x", final_path="/does/not/exist.mkv", title="Gone", media_type="movie",
        metadata={"height": 1080},
    )

    body = c.get("/api/stats/insights").json()
    assert body["resolution_breakdown"] == []


def test_scan_excludes_extras_and_sample_files(client):
    c, incoming_movies = client
    real = incoming_movies / "Some.Movie.2020.1080p.mkv"
    real.write_bytes(b"real movie")
    (incoming_movies / "Some.Movie.2020.sample.mkv").write_bytes(b"sample clip")
    extras_dir = incoming_movies / "Featurettes"
    extras_dir.mkdir()
    (extras_dir / "Behind the Scenes.mkv").write_bytes(b"featurette")

    files = c.get("/api/scan").json()["files"]
    assert len(files) == 1
    assert files[0]["path"] == str(real)


def test_archive_preview_multi_part_movie_gets_distinct_dest_paths(client):
    c, incoming_movies = client
    cd1 = incoming_movies / "Old.Movie.1999.CD1.mkv"
    cd2 = incoming_movies / "Old.Movie.1999.CD2.mkv"
    cd1.write_bytes(b"part one")
    cd2.write_bytes(b"part two")

    preview = c.post("/api/archive/preview", json={"paths": [str(cd1), str(cd2)]}).json()
    dest_paths = {item["dest_path"] for item in preview["items"]}
    assert len(dest_paths) == 2  # not collision-suffixed onto the same name
    assert any(d.endswith("Cd1.mkv") for d in dest_paths)
    assert any(d.endswith("Cd2.mkv") for d in dest_paths)
    # same movie folder for both parts
    assert len({Path(d).parent for d in dest_paths}) == 1


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
    assert add_resp.json()["tracker"]["category"] == "watching"

    status_resp = c.get("/api/tracker/status")
    assert status_resp.json()["total_tracked"] == 1


def test_tracker_add_with_interested_category(client):
    c, _ = client
    add_resp = c.post(
        "/api/tracker/add",
        json={"tmdb_id": 5, "media_type": "tv", "title": "Show", "category": "interested"},
    )
    assert add_resp.json()["tracker"]["category"] == "interested"


def test_tracker_set_category(client):
    c, _ = client
    add_resp = c.post("/api/tracker/add", json={"tmdb_id": 5, "media_type": "tv", "title": "Show"})
    tracker_id = add_resp.json()["tracker"]["id"]

    resp = c.post(f"/api/tracker/{tracker_id}/category", json={"category": "watched"})
    assert resp.status_code == 200
    assert resp.json()["tracker"]["category"] == "watched"
    assert c.get("/api/tracker/list").json()["tracked"][0]["category"] == "watched"


def test_tracker_set_category_404_for_missing_tracker(client):
    c, _ = client
    resp = c.post("/api/tracker/999/category", json={"category": "watching"})
    assert resp.status_code == 404


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


def test_tracker_snooze_clears_notification_and_sets_snoozed_until(client):
    c, _ = client
    add_resp = c.post("/api/tracker/add", json={"tmdb_id": 5, "media_type": "tv", "title": "Show"})
    tracker_id = add_resp.json()["tracker"]["id"]

    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=5, title="Show", media_type="tv", raw={"number_of_seasons": 3}
    )
    c.post(f"/api/tracker/{tracker_id}/check-now")
    assert len(c.get("/api/tracker/notifications").json()["notifications"]) == 1

    resp = c.post(f"/api/tracker/{tracker_id}/snooze", json={"days": 7})
    assert resp.status_code == 200
    assert resp.json()["tracker"]["snoozed_until"] is not None
    assert c.get("/api/tracker/notifications").json()["notifications"] == []


def test_tracker_snooze_404_for_missing_tracker(client):
    c, _ = client
    resp = c.post("/api/tracker/9999/snooze", json={"days": 7})
    assert resp.status_code == 404


def test_tracker_set_and_clear_check_interval(client):
    c, _ = client
    add_resp = c.post("/api/tracker/add", json={"tmdb_id": 5, "media_type": "tv", "title": "Show"})
    tracker_id = add_resp.json()["tracker"]["id"]

    resp = c.post(f"/api/tracker/{tracker_id}/interval", json={"hours": 12})
    assert resp.status_code == 200
    assert resp.json()["tracker"]["check_interval_hours"] == 12

    resp2 = c.post(f"/api/tracker/{tracker_id}/interval", json={"hours": None})
    assert resp2.json()["tracker"]["check_interval_hours"] is None


def test_upcoming_releases_includes_movie_and_tv_within_window(client):
    from datetime import datetime, timedelta, timezone

    c, _ = client
    c.post("/api/tracker/add", json={"tmdb_id": 5, "media_type": "tv", "title": "Show"})
    c.post("/api/tracker/add", json={"tmdb_id": 6, "media_type": "movie", "title": "Movie"})

    soon = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=6, title="Movie", media_type="movie", source="api", raw={"release_date": soon}
    )
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=5, title="Show", media_type="tv", source="api",
        raw={"next_episode_to_air": {"air_date": soon, "season_number": 2, "episode_number": 3}},
    )

    resp = c.get("/api/tracker/upcoming")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["title"] for i in items} == {"Show", "Movie"}
    show_entry = next(i for i in items if i["title"] == "Show")
    assert show_entry["label"] == "S02E03"


def test_upcoming_releases_excludes_dates_outside_window(client):
    from datetime import datetime, timedelta, timezone

    c, _ = client
    c.post("/api/tracker/add", json={"tmdb_id": 6, "media_type": "movie", "title": "Movie"})

    far_future = (datetime.now(timezone.utc).date() + timedelta(days=400)).isoformat()
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=6, title="Movie", media_type="movie", source="api", raw={"release_date": far_future}
    )

    resp = c.get("/api/tracker/upcoming")
    assert resp.json()["items"] == []


def test_upcoming_releases_excludes_muted_trackers(client):
    from datetime import datetime, timedelta, timezone

    c, _ = client
    add_resp = c.post("/api/tracker/add", json={"tmdb_id": 6, "media_type": "movie", "title": "Movie"})
    tracker_id = add_resp.json()["tracker"]["id"]
    c.post(f"/api/tracker/{tracker_id}/mute", json={"muted": True})

    soon = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=6, title="Movie", media_type="movie", source="api", raw={"release_date": soon}
    )

    resp = c.get("/api/tracker/upcoming")
    assert resp.json()["items"] == []


def test_upcoming_releases_skips_scraper_mode(client):
    from datetime import datetime, timedelta, timezone

    c, _ = client
    c.post("/api/tracker/add", json={"tmdb_id": 6, "media_type": "movie", "title": "Movie"})

    soon = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=6, title="Movie", media_type="movie", source="scraper", raw={"release_date": soon}
    )

    resp = c.get("/api/tracker/upcoming")
    assert resp.json()["items"] == []


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


def test_notification_feed_rss_returns_valid_xml(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.upsert_tracker(tmdb_id=5, media_type="tv", title="Show")
    tracker_row = db.get_tracker(5, "tv")
    db.log_notification(tracker_row["id"], 5, "tv", "Show", "New season available for Show")

    resp = c.get("/api/tracker/feed.rss")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/rss+xml")

    import xml.etree.ElementTree as ET

    root = ET.fromstring(resp.text)
    titles = [item.find("title").text for item in root.findall(".//item")]
    assert "Show" in titles


def test_notification_feed_rss_empty_when_no_history(client):
    c, _ = client
    resp = c.get("/api/tracker/feed.rss")
    assert resp.status_code == 200
    assert "<channel>" in resp.text


def test_confirm_archive_notifies_media_servers_on_success(client):
    c, incoming_movies = client
    app.dependency_overrides[get_config]().media_server.plex_url = "http://plex.local:32400"
    app.dependency_overrides[get_config]().media_server.plex_token = "tok"

    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()

    with patch("app.api.routes.archive.notify_media_servers") as mock_notify:
        c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    mock_notify.assert_called_once()


def test_arr_webhook_adopts_movie_on_radarr_payload(client):
    c, _ = client
    archive_movies = Path(app.dependency_overrides[get_config]().paths.archive_movies)
    (archive_movies / "Radarr.Import.2020.mkv").write_bytes(b"data")

    resp = c.post("/api/webhooks/arr", json={"movie": {"title": "Radarr Import"}, "eventType": "Download"})
    assert resp.status_code == 200
    assert resp.json()["adopted"] == {"movie": 1}

    movies = c.get("/api/library/movies").json()["items"]
    assert len(movies) == 1


def test_arr_webhook_adopts_tv_on_sonarr_payload(client):
    c, _ = client
    archive_tv = Path(app.dependency_overrides[get_config]().paths.archive_tv)
    (archive_tv / "Show.S01E01.mkv").write_bytes(b"data")

    resp = c.post("/api/webhooks/arr", json={"series": {"title": "Show"}, "eventType": "Download"})
    assert resp.status_code == 200
    assert resp.json()["adopted"] == {"tv": 1}


def test_arr_webhook_runs_both_scans_for_unrecognized_payload(client):
    c, _ = client
    resp = c.post("/api/webhooks/arr", json={"eventType": "Test"})
    assert resp.status_code == 200
    assert resp.json()["adopted"] == {"movie": 0, "tv": 0}


def test_api_token_disabled_by_default(client):
    c, _ = client
    resp = c.get("/api/status")
    assert resp.status_code == 200


def test_api_token_gate_rejects_missing_or_wrong_token(client):
    c, _ = client
    app.dependency_overrides[get_config]().server.api_token = "secret123"
    try:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", headers={"X-API-Token": "wrong"}).status_code == 401
        assert c.get("/api/status", headers={"X-API-Token": "secret123"}).status_code == 200
    finally:
        app.dependency_overrides[get_config]().server.api_token = ""


def test_named_api_token_alone_gates_and_grants_access(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    create_resp = c.post("/api/settings/tokens", json={"name": "phone"})
    assert create_resp.status_code == 200
    token = create_resp.json()["token"]

    try:
        # no legacy server.api_token was ever set, but a named token now
        # exists, so the gate is on -- an unauthenticated request 401s.
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", headers={"X-API-Token": "wrong"}).status_code == 401
        assert c.get("/api/status", headers={"X-API-Token": token}).status_code == 200
    finally:
        for row in db.list_api_tokens():
            db.delete_api_token(row["id"])


def test_named_api_token_updates_last_used_at(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    token = c.post("/api/settings/tokens", json={"name": "phone"}).json()["token"]

    try:
        # Checked directly against the DB, not through an authenticated
        # HTTP call -- an authenticated GET would touch last_used_at as a
        # side effect of its own auth check, before the assertion below.
        assert db.get_api_token_by_value(token)["last_used_at"] is None
        c.get("/api/status", headers={"X-API-Token": token})
        assert db.get_api_token_by_value(token)["last_used_at"] is not None
    finally:
        for row in db.list_api_tokens():
            db.delete_api_token(row["id"])


def test_revoked_named_api_token_no_longer_grants_access(client):
    c, _ = client
    # Keep the legacy token set too, so the gate stays on after the named
    # token is revoked below -- revoking the *only* configured token of
    # either kind opens the API back up entirely, which is the existing,
    # intentional "Disable API Token" behavior, not what this test is
    # checking (that a revoked token specifically stops working).
    app.dependency_overrides[get_config]().server.api_token = "legacy-secret"
    try:
        create_resp = c.post("/api/settings/tokens", json={"name": "phone"}, headers={"X-API-Token": "legacy-secret"})
        token, token_id = create_resp.json()["token"], create_resp.json()["id"]

        assert c.get("/api/status", headers={"X-API-Token": token}).status_code == 200
        c.delete(f"/api/settings/tokens/{token_id}", headers={"X-API-Token": "legacy-secret"})
        assert c.get("/api/status", headers={"X-API-Token": token}).status_code == 401
        assert c.get("/api/status", headers={"X-API-Token": "legacy-secret"}).status_code == 200
    finally:
        app.dependency_overrides[get_config]().server.api_token = ""


def test_create_api_token_requires_a_name(client):
    c, _ = client
    resp = c.post("/api/settings/tokens", json={"name": "  "})
    assert resp.status_code == 400


def test_create_api_token_rejects_invalid_scope(client):
    c, _ = client
    resp = c.post("/api/settings/tokens", json={"name": "phone", "scope": "admin"})
    assert resp.status_code == 400


def test_create_api_token_defaults_to_read_write(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    resp = c.post("/api/settings/tokens", json={"name": "phone"})
    assert resp.json()["scope"] == "read_write"
    try:
        assert db.list_api_tokens()[0]["scope"] == "read_write"
    finally:
        for row in db.list_api_tokens():
            db.delete_api_token(row["id"])


def test_read_only_api_token_allows_get_but_blocks_post(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    create_resp = c.post("/api/settings/tokens", json={"name": "readonly-device", "scope": "read_only"})
    assert create_resp.json()["scope"] == "read_only"
    token = create_resp.json()["token"]

    try:
        assert c.get("/api/status", headers={"X-API-Token": token}).status_code == 200
        resp = c.post("/api/settings/tokens", headers={"X-API-Token": token}, json={"name": "should-fail"})
        assert resp.status_code == 403
        resp = c.delete(f"/api/settings/tokens/{create_resp.json()['id']}", headers={"X-API-Token": token})
        assert resp.status_code == 403
    finally:
        for row in db.list_api_tokens():
            db.delete_api_token(row["id"])


def test_read_write_api_token_allows_post(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    token = c.post("/api/settings/tokens", json={"name": "phone", "scope": "read_write"}).json()["token"]

    try:
        resp = c.post("/api/settings/tokens", headers={"X-API-Token": token}, json={"name": "second-device"})
        assert resp.status_code == 200
    finally:
        for row in db.list_api_tokens():
            db.delete_api_token(row["id"])


def test_legacy_shared_token_is_never_scope_restricted(client):
    c, _ = client
    app.dependency_overrides[get_config]().server.api_token = "legacy-secret"
    try:
        resp = c.post("/api/settings/tokens", headers={"X-API-Token": "legacy-secret"}, json={"name": "x"})
        assert resp.status_code == 200
    finally:
        app.dependency_overrides[get_config]().server.api_token = ""
        db = app.dependency_overrides[get_database]()
        for row in db.list_api_tokens():
            db.delete_api_token(row["id"])


def test_pwa_manifest_and_service_worker_are_served(client):
    c, _ = client
    manifest = c.get("/manifest.json")
    assert manifest.status_code == 200
    assert manifest.json()["name"] == "MediAerie"

    sw = c.get("/sw.js")
    assert sw.status_code == 200

    icon = c.get("/icon.png")
    assert icon.status_code == 200


def test_api_token_gate_leaves_static_assets_open(client):
    c, _ = client
    app.dependency_overrides[get_config]().server.api_token = "secret123"
    try:
        resp = c.get("/index.html")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides[get_config]().server.api_token = ""


def test_settings_auto_track_new_round_trips(client):
    c, _ = client
    resp = c.post("/api/settings", json={"auto_track_new": True})
    assert resp.status_code == 200
    assert resp.json()["auto_track_new"] is True


def test_settings_media_server_fields_round_trip(client):
    c, _ = client
    resp = c.post(
        "/api/settings",
        json={"plex_url": "http://plex.local:32400", "plex_token": "tok", "jellyfin_url": "http://j.local:8096"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plex_url"] == "http://plex.local:32400"
    assert body["plex_token_set"] is True
    assert body["jellyfin_url"] == "http://j.local:8096"
    assert body["jellyfin_api_key_set"] is False


def test_settings_api_token_round_trips_as_set_flag_only(client):
    c, _ = client
    resp = c.post("/api/settings", json={"api_token": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["api_token_set"] is True
    # the raw token value is never echoed back, only whether one is set
    assert "api_token" not in resp.json()


def test_confirm_archive_auto_tracks_when_enabled(client):
    c, incoming_movies = client
    app.dependency_overrides[get_config]().tracker.auto_track_new = True

    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [
        MediaResult(tmdb_id=603, title="The Matrix", media_type="movie", year=1999)
    ]
    video = incoming_movies / "The.Matrix.1999.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    tracked = c.get("/api/tracker/list").json()["tracked"]
    assert any(t["tmdb_id"] == 603 for t in tracked)


def test_confirm_archive_does_not_auto_track_by_default(client):
    c, incoming_movies = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [
        MediaResult(tmdb_id=603, title="The Matrix", media_type="movie", year=1999)
    ]
    video = incoming_movies / "The.Matrix.1999.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    assert c.get("/api/tracker/list").json()["tracked"] == []


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


def test_archive_preview_computes_absolute_episode_when_template_uses_it(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.renaming.tv_file = "{show_name} - {absolute_episode:03d}"
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_tv.return_value = [MediaResult(tmdb_id=50, title="Show", media_type="tv")]
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=50, title="Show", media_type="tv",
        raw={"seasons": [{"season_number": 1, "episode_count": 12}, {"season_number": 2, "episode_count": 10}]},
    )

    tv_dir = incoming_movies.parent / "tv"
    tv_dir.mkdir(exist_ok=True)
    f = tv_dir / "Show.S02E03.mkv"
    f.write_bytes(b"data")

    preview = c.post("/api/archive/preview", json={"paths": [str(f)]}).json()
    assert preview["items"][0]["dest_path"].endswith("Show - 015.mkv")
    fake_tmdb.get_tv_details.assert_called_once_with(50)


def test_archive_preview_skips_absolute_episode_lookup_when_template_does_not_use_it(client):
    c, incoming_movies = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_tv.return_value = [MediaResult(tmdb_id=51, title="Show", media_type="tv")]

    tv_dir = incoming_movies.parent / "tv"
    tv_dir.mkdir(exist_ok=True)
    f = tv_dir / "Show.S02E03.mkv"
    f.write_bytes(b"data")

    resp = c.post("/api/archive/preview", json={"paths": [str(f)]})
    assert resp.status_code == 200
    fake_tmdb.get_tv_details.assert_not_called()


def test_archive_preview_and_library_include_air_date_from_tvmaze(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.tvmaze.enabled = True
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_tv.return_value = [MediaResult(tmdb_id=52, title="Show", media_type="tv")]
    fake_tmdb.get_external_imdb_id.return_value = "tt0000052"

    fake_tvmaze = MagicMock()
    fake_tvmaze.enabled = True
    fake_tvmaze.get_episode_by_imdb.return_value = TVmazeEpisode(
        season=1, episode=1, name=None, air_date="2026-01-15"
    )
    app.dependency_overrides[get_tvmaze_client] = lambda: fake_tvmaze

    tv_dir = incoming_movies.parent / "tv"
    tv_dir.mkdir(exist_ok=True)
    f = tv_dir / "Show.S01E01.mkv"
    f.write_bytes(b"data")

    preview = c.post("/api/archive/preview", json={"paths": [str(f)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})

    fake_tvmaze.get_episode_by_imdb.assert_called_once_with("tt0000052", 1, 1)
    item = c.get("/api/library/tv").json()["items"][0]
    assert item["air_date"] == "2026-01-15"


def test_archive_preview_skips_tvmaze_when_disabled(client):
    c, incoming_movies = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_tv.return_value = [MediaResult(tmdb_id=53, title="Show", media_type="tv")]

    fake_tvmaze = MagicMock()
    fake_tvmaze.enabled = False
    app.dependency_overrides[get_tvmaze_client] = lambda: fake_tvmaze

    tv_dir = incoming_movies.parent / "tv"
    tv_dir.mkdir(exist_ok=True)
    f = tv_dir / "Show.S01E01.mkv"
    f.write_bytes(b"data")

    c.post("/api/archive/preview", json={"paths": [str(f)]})

    fake_tvmaze.get_episode_by_imdb.assert_not_called()
    fake_tmdb.get_external_imdb_id.assert_not_called()


def test_tv_status_prefers_tvmaze_status_and_adds_network_and_next_episode(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.tvmaze.enabled = True
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=54, title="Show", media_type="tv", raw={"status": "Returning Series", "number_of_seasons": 1},
    )
    fake_tmdb.get_external_imdb_id.return_value = "tt0000054"

    fake_tvmaze = MagicMock()
    fake_tvmaze.enabled = True
    fake_tvmaze.lookup_show_id_by_imdb.return_value = 1
    fake_tvmaze.get_show_info.return_value = TVmazeShowInfo(
        tvmaze_id=1, status="Running", network="AMC",
        next_episode_air_date="2026-09-10", next_episode_code="S02E01",
    )
    fake_tvmaze.get_episodes.return_value = []
    app.dependency_overrides[get_tvmaze_client] = lambda: fake_tvmaze

    resp = c.get("/api/library/tv-status", params={"tmdb_id": 54})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Running"  # TVmaze preferred over TMDB's "Returning Series"
    assert body["network"] == "AMC"
    assert body["next_episode_air_date"] == "2026-09-10"
    assert body["next_episode_code"] == "S02E01"


def test_tv_status_includes_aired_count_per_season_from_tvmaze(client):
    c, incoming_movies = client
    config = app.dependency_overrides[get_config]()
    config.tvmaze.enabled = True
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=55, title="Show", media_type="tv",
        raw={"status": "Returning Series", "number_of_seasons": 2,
             "seasons": [{"season_number": 1, "episode_count": 8}, {"season_number": 2, "episode_count": 8}]},
    )
    fake_tmdb.get_external_imdb_id.return_value = "tt0000055"

    fake_tvmaze = MagicMock()
    fake_tvmaze.enabled = True
    fake_tvmaze.lookup_show_id_by_imdb.return_value = 5
    fake_tvmaze.get_show_info.return_value = TVmazeShowInfo(
        tvmaze_id=5, status="Running", network=None, next_episode_air_date=None, next_episode_code=None,
    )
    fake_tvmaze.get_episodes.return_value = [
        TVmazeEpisode(season=1, episode=n, name=None, air_date="2025-01-01") for n in range(1, 9)
    ] + [
        TVmazeEpisode(season=2, episode=n, name=None, air_date="2025-06-01" if n <= 3 else None)
        for n in range(1, 9)
    ]
    app.dependency_overrides[get_tvmaze_client] = lambda: fake_tvmaze

    resp = c.get("/api/library/tv-status", params={"tmdb_id": 55})
    assert resp.status_code == 200
    seasons = {s["season_number"]: s for s in resp.json()["seasons"]}
    assert seasons[1]["episode_count"] == 8
    assert seasons[1]["aired_count"] == 8
    assert seasons[2]["episode_count"] == 8
    assert seasons[2]["aired_count"] == 3


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


def test_rematch_imdb_persists_imdb_id(client):
    c, incoming_movies = client
    video = incoming_movies / "Unmatched.File.mkv"
    video.write_bytes(b"data")
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = []
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb.find_by_imdb_id.return_value = MediaResult(tmdb_id=278, title="X", media_type="movie", year=1994)
    c.post("/api/library/rematch-imdb", json={"ids": [item_id], "imdb_id": "tt0111161", "media_type": "movie"})

    ratings_resp = c.get(f"/api/library/{item_id}/ratings")
    assert ratings_resp.json()["imdb_id"] == "tt0111161"
    fake_tmdb.get_external_imdb_id.assert_not_called()  # already known, no TMDB round trip needed


def test_rematch_tmdb_updates_item(client):
    c, incoming_movies = client
    video = incoming_movies / "Wrong.Match.2020.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=550, title="Correct Title", media_type="movie", year=1999, overview="right one"
    )
    resp = c.post("/api/library/rematch-tmdb", json={"ids": [item_id], "tmdb_id": 550, "media_type": "movie"})
    assert resp.status_code == 200
    assert resp.json()["tmdb_id"] == 550

    updated = c.get("/api/library/movies").json()["items"][0]
    assert updated["tmdb_id"] == 550
    assert updated["title"] == "Correct Title"


def test_rematch_tmdb_404_when_not_found(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = None

    resp = c.post("/api/library/rematch-tmdb", json={"ids": [1], "tmdb_id": 999999, "media_type": "movie"})
    assert resp.status_code == 404


def test_ratings_resolves_imdb_id_lazily_from_tmdb_id(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [MediaResult(tmdb_id=99, title="Sample Movie", media_type="movie", year=2020)]
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb.get_external_imdb_id.return_value = "tt0111161"
    resp = c.get(f"/api/library/{item_id}/ratings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["imdb_id"] == "tt0111161"
    assert body["omdb_configured"] is False  # no OMDb key in this fixture's config

    # Cached on the row now -- a second call shouldn't need another TMDB lookup.
    fake_tmdb.get_external_imdb_id.reset_mock()
    c.get(f"/api/library/{item_id}/ratings")
    fake_tmdb.get_external_imdb_id.assert_not_called()


def test_ratings_404_for_missing_item(client):
    c, _ = client
    resp = c.get("/api/library/999999/ratings")
    assert resp.status_code == 404


def test_ratings_returns_values_when_omdb_configured(client, monkeypatch):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_external_imdb_id.return_value = "tt0111161"

    from app.core.omdb_client import RatingsResult

    fake_omdb = MagicMock()
    fake_omdb.enabled = True
    fake_omdb.get_ratings.return_value = RatingsResult(
        imdb_rating=9.3, imdb_votes="2,900,000", rotten_tomatoes="91%", metacritic="80"
    )
    app.dependency_overrides[get_omdb_client] = lambda: fake_omdb

    resp = c.get(f"/api/library/{item_id}/ratings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["imdb_rating"] == 9.3
    assert body["rotten_tomatoes"] == "91%"
    assert body["omdb_configured"] is True


def test_download_movie_note_returns_markdown_with_attachment_header(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"data")
    item_id = db.create_media_item(
        original_path="x", final_path=str(video), title="Some Movie", year=2020, media_type="movie",
        tmdb_id=42, imdb_id="tt0000001", metadata={"poster_path": "/p.jpg", "overview": "plot", "genres": ["Drama"]},
    )

    resp = c.get(f"/api/library/{item_id}/note")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert 'filename="Some Movie (2020).md"' in resp.headers["content-disposition"]
    assert "title: Some Movie" in resp.text
    assert "type: movie" in resp.text


def test_download_movie_note_uses_omdb_when_configured(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"data")
    item_id = db.create_media_item(
        original_path="x", final_path=str(video), title="Some Movie", year=2020, media_type="movie",
        tmdb_id=42, imdb_id="tt0000001", metadata={"poster_path": "/p.jpg", "overview": "tmdb plot"},
    )

    from app.core.omdb_client import OMDbFullResult

    fake_omdb = MagicMock()
    fake_omdb.enabled = True
    fake_omdb.get_full_details.return_value = OMDbFullResult(
        title="Some Movie", year="2020", imdb_id="tt0000001", plot="omdb plot",
        genres=["Drama"], director=["A Director"], writer=["A Writer"], actors=["An Actor"],
        runtime="100 min", imdb_rating=7.5, poster_url="https://example.com/p.jpg", released="01 Jan 2020",
    )
    app.dependency_overrides[get_omdb_client] = lambda: fake_omdb

    resp = c.get(f"/api/library/{item_id}/note")
    assert resp.status_code == 200
    assert "dataSource: OMDbAPI" in resp.text
    assert "omdb plot" in resp.text
    assert "A Director" in resp.text
    fake_omdb.get_full_details.assert_called_once_with("tt0000001")


def test_download_movie_note_handles_non_ascii_title_in_filename(client, tmp_path):
    """Regression: a title with an en dash/accented char (routine in real
    movie/TV titles) previously 500'd -- HTTP headers are Latin-1 only,
    and Content-Disposition was built with the raw filename verbatim."""
    c, _ = client
    db = app.dependency_overrides[get_database]()
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"x")
    item_id = db.create_media_item(
        original_path="x", final_path=str(video), title="Amélie – Special Edition", year=2001,
        media_type="movie", metadata={},
    )

    resp = c.get(f"/api/library/{item_id}/note")
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "Am%C3%A9lie" in disposition or "Amélie" in disposition


def test_download_tv_note_handles_non_ascii_title_in_filename(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    db.create_media_item(
        original_path="x", final_path=str(video), title="9-1-1", media_type="tv",
        tmdb_id=75219, imdb_id="tt7235466", season_number=1, episode_number=1, metadata={},
    )

    from app.core.omdb_client import OMDbFullResult

    fake_omdb = MagicMock()
    fake_omdb.enabled = True
    fake_omdb.get_full_details.return_value = OMDbFullResult(
        title="9-1-1", year="2018–", imdb_id="tt7235466", plot="p",
        genres=["Drama"], director=["N/A"], writer=["N/A"], actors=["N/A"],
        runtime="43 min", imdb_rating=7.9, poster_url="https://example.com/p.jpg", released="03 Jan 2018",
    )
    app.dependency_overrides[get_omdb_client] = lambda: fake_omdb

    resp = c.get("/api/library/tv-shows/75219/note")
    assert resp.status_code == 200
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]


def test_download_movie_note_404_for_missing_item(client):
    c, _ = client
    resp = c.get("/api/library/999999/note")
    assert resp.status_code == 404


def test_download_movie_note_400_for_tv_item(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    item_id = db.create_media_item(
        original_path="x", final_path="/x/e.mkv", title="Show", media_type="tv",
        season_number=1, episode_number=1, metadata={},
    )
    resp = c.get(f"/api/library/{item_id}/note")
    assert resp.status_code == 400


def test_save_movie_note_writes_file_alongside_video(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    movie_dir = tmp_path / "Some Movie (2020)"
    movie_dir.mkdir()
    video = movie_dir / "Some Movie (2020).mkv"
    video.write_bytes(b"data")
    item_id = db.create_media_item(
        original_path="x", final_path=str(video), title="Some Movie", year=2020, media_type="movie",
        metadata={"overview": "plot"},
    )

    resp = c.post(f"/api/library/{item_id}/note/save")
    assert resp.status_code == 200
    note_path = Path(resp.json()["path"])
    assert note_path.exists()
    assert note_path.parent == movie_dir
    assert "title: Some Movie" in note_path.read_text(encoding="utf-8")


def test_save_movie_note_404_when_no_archived_file(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    item_id = db.create_media_item(
        original_path="x", final_path=None, title="Some Movie", year=2020, media_type="movie", metadata={},
    )
    resp = c.post(f"/api/library/{item_id}/note/save")
    assert resp.status_code == 400


def test_download_tv_note_aggregates_across_episodes(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    show_dir = tmp_path / "Show"
    (show_dir / "Season 01").mkdir(parents=True)
    ep1 = show_dir / "Season 01" / "Show - S01E01.mkv"
    ep2 = show_dir / "Season 01" / "Show - S01E02.mkv"
    ep1.write_bytes(b"x")
    ep2.write_bytes(b"x")
    db.create_media_item(
        original_path="x", final_path=str(ep1), title="Show", media_type="tv",
        tmdb_id=75219, imdb_id="tt7235466", season_number=1, episode_number=1,
        watched=True, metadata={"poster_path": "/p.jpg", "overview": "plot", "genres": ["Drama"]},
    )
    db.create_media_item(
        original_path="x", final_path=str(ep2), title="Show", media_type="tv",
        tmdb_id=75219, imdb_id="tt7235466", season_number=1, episode_number=2,
        watched=False, metadata={"poster_path": "/p.jpg", "overview": "plot", "genres": ["Drama"]},
    )

    resp = c.get("/api/library/tv-shows/75219/note")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "type: series" in resp.text
    assert "episodes: 2" in resp.text
    assert "watched: false" in resp.text  # not every episode is watched


def test_download_tv_note_all_watched_reports_true(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    db.create_media_item(
        original_path="x", final_path=str(video), title="Show", media_type="tv",
        tmdb_id=75219, season_number=1, episode_number=1, watched=True, metadata={},
    )

    resp = c.get("/api/library/tv-shows/75219/note")
    assert "watched: true" in resp.text
    assert "lastWatched: S1" in resp.text


def test_download_tv_note_404_when_no_episodes_for_tmdb_id(client):
    c, _ = client
    resp = c.get("/api/library/tv-shows/999999/note")
    assert resp.status_code == 404


def test_download_tv_note_uses_omdb_and_omits_director(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    db.create_media_item(
        original_path="x", final_path=str(video), title="Show", media_type="tv",
        tmdb_id=75219, imdb_id="tt7235466", season_number=1, episode_number=1, metadata={},
    )

    from app.core.omdb_client import OMDbFullResult

    fake_omdb = MagicMock()
    fake_omdb.enabled = True
    fake_omdb.get_full_details.return_value = OMDbFullResult(
        title="Show", year="2018–", imdb_id="tt7235466", plot="omdb plot",
        genres=["Drama"], director=["N/A"], writer=["A Writer"], actors=["An Actor"],
        runtime="43 min", imdb_rating=7.9, poster_url="https://example.com/p.jpg", released="03 Jan 2018",
    )
    app.dependency_overrides[get_omdb_client] = lambda: fake_omdb

    resp = c.get("/api/library/tv-shows/75219/note")
    assert "dataSource: OMDbAPI" in resp.text
    assert "director:" not in resp.text
    fake_omdb.get_full_details.assert_called_once_with("tt7235466")


def test_save_tv_note_writes_to_show_folder_not_season_folder(client, tmp_path):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    show_dir = tmp_path / "Show"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    video = season_dir / "Show - S01E01.mkv"
    video.write_bytes(b"x")
    db.create_media_item(
        original_path="x", final_path=str(video), title="Show", media_type="tv",
        tmdb_id=75219, season_number=1, episode_number=1, metadata={},
    )

    resp = c.post("/api/library/tv-shows/75219/note/save")
    assert resp.status_code == 200
    note_path = Path(resp.json()["path"])
    assert note_path.exists()
    assert note_path.parent == show_dir  # not season_dir
    assert "type: series" in note_path.read_text(encoding="utf-8")


def test_save_tv_note_400_when_no_archived_episodes(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(
        original_path="x", final_path=None, title="Show", media_type="tv",
        tmdb_id=75219, season_number=1, episode_number=1, metadata={},
    )
    resp = c.post("/api/library/tv-shows/75219/note/save")
    assert resp.status_code == 400


def test_trailer_returns_youtube_key(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [MediaResult(tmdb_id=99, title="Sample Movie", media_type="movie", year=2020)]
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb.mode = "api"
    fake_tmdb.get_trailer_key.return_value = "dQw4w9WgXcQ"

    resp = c.get(f"/api/library/{item_id}/trailer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["youtube_key"] == "dQw4w9WgXcQ"
    assert body["tmdb_configured"] is True
    fake_tmdb.get_trailer_key.assert_called_once_with(99, "movie")


def test_trailer_404_for_missing_item(client):
    c, _ = client
    resp = c.get("/api/library/999999/trailer")
    assert resp.status_code == 404


def test_trailer_reports_not_configured_without_api_key(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [MediaResult(tmdb_id=99, title="Sample Movie", media_type="movie", year=2020)]
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb.mode = "scraper"
    fake_tmdb.get_trailer_key.return_value = None

    resp = c.get(f"/api/library/{item_id}/trailer")
    body = resp.json()
    assert body["youtube_key"] is None
    assert body["tmdb_configured"] is False


def test_more_info_returns_cast_and_similar(client):
    c, incoming_movies = client
    video = incoming_movies / "Sample.Movie.2020.1080p.mkv"
    video.write_bytes(b"data")
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = [MediaResult(tmdb_id=99, title="Sample Movie", media_type="movie", year=2020)]
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    fake_tmdb.mode = "api"
    fake_tmdb.get_cast.return_value = [{"name": "Actor One", "character": "Hero", "profile_path": "/a.jpg"}]
    fake_tmdb.get_similar_titles.return_value = [
        MediaResult(tmdb_id=7, title="Related Movie", media_type="movie", year=2018, poster_path="/r.jpg")
    ]

    resp = c.get(f"/api/library/{item_id}/more-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_configured"] is True
    assert body["cast"] == [{"id": None, "name": "Actor One", "character": "Hero", "profile_path": "/a.jpg"}]
    assert body["similar"][0]["title"] == "Related Movie"
    assert body["similar"][0]["year"] == 2018
    fake_tmdb.get_cast.assert_called_once_with(99, "movie")
    fake_tmdb.get_similar_titles.assert_called_once_with(99, "movie")


def test_more_info_404_for_missing_item(client):
    c, _ = client
    resp = c.get("/api/library/999999/more-info")
    assert resp.status_code == 404


def test_more_info_empty_without_tmdb_match(client):
    c, incoming_movies = client
    video = incoming_movies / "Unmatched.File.mkv"
    video.write_bytes(b"data")
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = []
    preview = c.post("/api/archive/preview", json={"paths": [str(video)]}).json()
    c.post("/api/archive/confirm", json={"items": preview["items"], "purge_subtitles": False})
    item_id = c.get("/api/library/movies").json()["items"][0]["id"]

    resp = c.get(f"/api/library/{item_id}/more-info")
    body = resp.json()
    assert body["cast"] == []
    assert body["similar"] == []


def test_ratings_by_tmdb_needs_no_media_item(client):
    """tmdb_id-keyed sibling used by the Tracker tab, where a tracked title
    has no media_items row at all."""
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_external_imdb_id.return_value = "tt0111161"

    fake_omdb = MagicMock()
    fake_omdb.enabled = True
    from app.core.omdb_client import RatingsResult

    fake_omdb.get_ratings.return_value = RatingsResult(
        imdb_rating=9.3, imdb_votes="2,900,000", rotten_tomatoes="91%", metacritic="80"
    )
    app.dependency_overrides[get_omdb_client] = lambda: fake_omdb

    resp = c.get("/api/library/ratings", params={"tmdb_id": 278, "media_type": "movie"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["imdb_id"] == "tt0111161"
    assert body["imdb_rating"] == 9.3
    assert body["omdb_configured"] is True
    fake_tmdb.get_external_imdb_id.assert_called_once_with(278, "movie")


def test_trailer_by_tmdb_needs_no_media_item(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.mode = "api"
    fake_tmdb.get_trailer_key.return_value = "dQw4w9WgXcQ"

    resp = c.get("/api/library/trailer", params={"tmdb_id": 1399, "media_type": "tv"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["youtube_key"] == "dQw4w9WgXcQ"
    assert body["tmdb_configured"] is True
    fake_tmdb.get_trailer_key.assert_called_once_with(1399, "tv")


def test_more_info_by_tmdb_needs_no_media_item(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.mode = "api"
    fake_tmdb.get_cast.return_value = [{"name": "Actor One", "character": "Hero", "profile_path": "/a.jpg"}]
    fake_tmdb.get_similar_titles.return_value = [
        MediaResult(tmdb_id=7, title="Related Show", media_type="tv", year=2018, poster_path="/r.jpg")
    ]

    resp = c.get("/api/library/more-info", params={"tmdb_id": 1399, "media_type": "tv"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_configured"] is True
    assert body["cast"] == [{"id": None, "name": "Actor One", "character": "Hero", "profile_path": "/a.jpg"}]
    assert body["similar"][0]["title"] == "Related Show"
    fake_tmdb.get_cast.assert_called_once_with(1399, "tv")
    fake_tmdb.get_similar_titles.assert_called_once_with(1399, "tv")


def test_tv_season_episodes_from_tmdb(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_season_episodes.return_value = [
        {"episode_number": 1, "name": "Pilot", "air_date": "2020-01-01", "overview": "The first one."},
        {"episode_number": 2, "name": "Second", "air_date": "2020-01-08", "overview": None},
    ]

    resp = c.get("/api/library/tv-season", params={"tmdb_id": 1399, "season_number": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert [e["name"] for e in body["episodes"]] == ["Pilot", "Second"]
    fake_tmdb.get_season_episodes.assert_called_once_with(1399, 1)


def test_tv_season_episodes_falls_back_to_tvmaze(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_season_episodes.return_value = []
    fake_tmdb.get_external_imdb_id.return_value = "tt0000052"

    fake_tvmaze = MagicMock()
    fake_tvmaze.enabled = True
    fake_tvmaze.lookup_show_id_by_imdb.return_value = 5
    fake_tvmaze.get_episodes.return_value = [
        TVmazeEpisode(season=1, episode=2, name="Second", air_date="2020-01-08"),
        TVmazeEpisode(season=1, episode=1, name="Pilot", air_date="2020-01-01"),
        TVmazeEpisode(season=2, episode=1, name="Other Season", air_date="2021-01-01"),
    ]
    app.dependency_overrides[get_tvmaze_client] = lambda: fake_tvmaze

    resp = c.get("/api/library/tv-season", params={"tmdb_id": 1399, "season_number": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert [e["episode_number"] for e in body["episodes"]] == [1, 2]  # sorted, season 2 excluded
    assert body["episodes"][0]["name"] == "Pilot"


def test_tv_season_episodes_unavailable_without_any_source(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_season_episodes.return_value = []

    fake_tvmaze = MagicMock()
    fake_tvmaze.enabled = False
    app.dependency_overrides[get_tvmaze_client] = lambda: fake_tvmaze

    resp = c.get("/api/library/tv-season", params={"tmdb_id": 1399, "season_number": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is False
    assert body["episodes"] == []


def test_settings_omdb_key_round_trips(client):
    c, _ = client
    resp = c.post("/api/settings", json={"omdb_api_key": "abc123"})
    assert resp.status_code == 200
    assert resp.json()["omdb_api_key_set"] is True
    assert "abc123" not in resp.text


def test_tv_status_reports_latest_season_and_episode_count(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1399, title="Show", media_type="tv", source="api",
        raw={
            "number_of_seasons": 3, "number_of_episodes": 26,
            "status": "Returning Series", "latest_season_episode_count": 6,
        },
    )

    resp = c.get("/api/library/tv-status", params={"tmdb_id": 1399})
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_known_season"] == 3
    assert body["status"] == "Returning Series"
    assert body["latest_season_episode_count"] == 6
    assert body["total_episodes"] == 26
    assert body["data_available"] is True


def test_tv_status_reports_per_season_episode_counts_excluding_specials(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1399, title="Show", media_type="tv", source="api",
        raw={
            "number_of_seasons": 2, "number_of_episodes": 14,
            "seasons": [
                {"season_number": 0, "episode_count": 3},  # specials -- excluded
                {"season_number": 1, "episode_count": 8},
                {"season_number": 2, "episode_count": 6},
            ],
        },
    )

    resp = c.get("/api/library/tv-status", params={"tmdb_id": 1399})
    assert resp.status_code == 200
    seasons = resp.json()["seasons"]
    assert seasons == [
        {"season_number": 1, "episode_count": 8, "aired_count": None},
        {"season_number": 2, "episode_count": 6, "aired_count": None},
    ]


def test_tv_status_scraper_mode_reports_data_unavailable(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = MediaResult(
        tmdb_id=1399, title="Show", media_type="tv", source="scraper", raw={}
    )

    resp = c.get("/api/library/tv-status", params={"tmdb_id": 1399})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is False
    assert body["latest_known_season"] is None


def test_tv_status_404_when_tmdb_has_no_match(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_tv_details.return_value = None

    resp = c.get("/api/library/tv-status", params={"tmdb_id": 999999})
    assert resp.status_code == 404


def test_movie_status_reports_related_collection_titles(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=603, title="The Matrix", media_type="movie", source="api",
        raw={"belongs_to_collection": {"id": 2344, "name": "The Matrix Collection"}},
    )
    fake_tmdb.get_collection_movies.return_value = [
        MediaResult(tmdb_id=603, title="The Matrix", media_type="movie", year=1999),
        MediaResult(tmdb_id=604, title="The Matrix Reloaded", media_type="movie", year=2003),
        MediaResult(tmdb_id=605, title="The Matrix Revolutions", media_type="movie", year=2003),
    ]

    resp = c.get("/api/library/movie-status", params={"tmdb_id": 603})
    assert resp.status_code == 200
    body = resp.json()
    assert body["collection_id"] == 2344
    assert body["data_available"] is True
    related_ids = {r["tmdb_id"] for r in body["related"]}
    assert related_ids == {604, 605}  # excludes the queried movie itself
    fake_tmdb.get_collection_movies.assert_called_once_with(2344)


def test_movie_status_no_collection_returns_empty_related(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=100, title="Standalone Movie", media_type="movie", source="api",
        raw={"belongs_to_collection": None},
    )

    resp = c.get("/api/library/movie-status", params={"tmdb_id": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert body["collection_id"] is None
    assert body["related"] == []
    fake_tmdb.get_collection_movies.assert_not_called()


def test_movie_status_scraper_mode_reports_data_unavailable(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = MediaResult(
        tmdb_id=603, title="The Matrix", media_type="movie", source="scraper", raw={}
    )

    resp = c.get("/api/library/movie-status", params={"tmdb_id": 603})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is False
    assert body["collection_id"] is None


def test_movie_status_404_when_tmdb_has_no_match(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.get_movie_details.return_value = None

    resp = c.get("/api/library/movie-status", params={"tmdb_id": 999999})
    assert resp.status_code == 404


# ---- Watchlist ----


def test_watchlist_needs_download_reflects_pending_tracker(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.upsert_tracker(tmdb_id=5, media_type="tv", title="Show", pending_notification=1, latest_known_season=3)

    resp = c.get("/api/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["needs_download"]) == 1
    assert body["needs_download"][0]["title"] == "Show"
    assert body["needs_watching"] == []


def test_watchlist_needs_download_excludes_muted(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.upsert_tracker(tmdb_id=5, media_type="tv", title="Show", pending_notification=1, muted=1)

    resp = c.get("/api/watchlist")
    assert resp.json()["needs_download"] == []


def test_watchlist_needs_watching_groups_by_show_and_picks_next_unwatched(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(
        original_path="e1", final_path="e1.mkv", title="Show", media_type="tv",
        tmdb_id=7, season_number=1, episode_number=1, watched=1,
    )
    db.create_media_item(
        original_path="e2", final_path="e2.mkv", title="Show", media_type="tv",
        tmdb_id=7, season_number=1, episode_number=2, watched=0,
        metadata={"episode_title": "The Sequel", "poster_path": "/p.jpg"},
    )

    resp = c.get("/api/watchlist")
    assert resp.status_code == 200
    watching = resp.json()["needs_watching"]
    assert len(watching) == 1
    show = watching[0]
    assert show["tmdb_id"] == 7
    assert show["unwatched_count"] == 1
    assert show["total_count"] == 2
    assert show["next_up"]["season_number"] == 1
    assert show["next_up"]["episode_number"] == 2
    assert show["next_up"]["episode_title"] == "The Sequel"


def test_watchlist_excludes_fully_watched_show(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(
        original_path="e1", final_path="e1.mkv", title="Show", media_type="tv",
        tmdb_id=7, season_number=1, episode_number=1, watched=1,
    )

    resp = c.get("/api/watchlist")
    assert resp.json()["needs_watching"] == []


# ---- Reports ----


def test_report_summary_counts_growth_within_range(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(
        original_path="m1", final_path="m1.mkv", title="In Range", media_type="movie",
        archived_at="2026-02-15T00:00:00+00:00",
    )
    db.create_media_item(
        original_path="m2", final_path="m2.mkv", title="Out of Range", media_type="movie",
        archived_at="2026-05-01T00:00:00+00:00",
    )

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["growth"]["movies_added"] == 1
    assert body["growth"]["tv_episodes_added"] == 0


def test_report_summary_counts_watch_activity_within_range(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    item_id = db.create_media_item(
        original_path="m1", final_path="m1.mkv", title="Watched In Range", media_type="movie",
    )
    db.update_media_item(item_id, watched=1, watched_at="2026-02-15T00:00:00+00:00")
    other_id = db.create_media_item(
        original_path="m2", final_path="m2.mkv", title="Watched Out of Range", media_type="movie",
    )
    db.update_media_item(other_id, watched=1, watched_at="2026-05-01T00:00:00+00:00")

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    assert resp.json()["watch_activity"]["movies_watched"] == 1


def test_report_summary_rejects_start_after_end(client):
    c, _ = client
    resp = c.get("/api/reports/summary", params={"start": "2026-06-01", "end": "2026-01-01"})
    assert resp.status_code == 400


def test_report_summary_counts_tracker_activity_within_range(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    in_range_id = db.log_notification(None, 5, "tv", "In Range Show", "New season")
    out_of_range_id = db.log_notification(None, 6, "movie", "Out of Range Movie", "New release")
    with db.connect() as conn:
        conn.execute("UPDATE notification_history SET created_at = ? WHERE id = ?", ("2026-02-15T00:00:00+00:00", in_range_id))
        conn.execute("UPDATE notification_history SET created_at = ? WHERE id = ?", ("2026-05-01T00:00:00+00:00", out_of_range_id))

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    tracker_activity = resp.json()["tracker_activity"]
    assert tracker_activity["notifications_sent"] == 1
    assert tracker_activity["tv_shows_notified"] == 1
    assert tracker_activity["movies_notified"] == 0
    assert tracker_activity["titles"] == ["In Range Show"]


def test_report_summary_per_viewer_watch_activity(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    viewer_id = db.create_viewer("Alex")
    item_id = db.create_media_item(original_path="m1", final_path="m1.mkv", title="Movie", media_type="movie")
    db.set_viewer_watched(viewer_id, item_id, True)
    with db.connect() as conn:
        conn.execute(
            "UPDATE viewer_watched_items SET watched_at = ? WHERE viewer_id = ? AND media_item_id = ?",
            ("2026-02-15T00:00:00+00:00", viewer_id, item_id),
        )

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    by_viewer = resp.json()["watch_activity"]["by_viewer"]
    assert by_viewer == [{"viewer_id": viewer_id, "viewer_name": "Alex", "count": 1, "watch_seconds": 0.0}]


def test_report_summary_per_viewer_watch_seconds_sums_probed_duration(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    viewer_id = db.create_viewer("Alex")
    item1 = db.create_media_item(
        original_path="m1", final_path="m1.mkv", title="Movie 1", media_type="movie",
        metadata={"duration_seconds": 3600.0},
    )
    item2 = db.create_media_item(
        original_path="m2", final_path="m2.mkv", title="Movie 2", media_type="movie",
        metadata={"duration_seconds": 1800.0},
    )
    item3_never_probed = db.create_media_item(original_path="m3", final_path="m3.mkv", title="Movie 3", media_type="movie")
    for item_id in (item1, item2, item3_never_probed):
        db.set_viewer_watched(viewer_id, item_id, True)
    with db.connect() as conn:
        conn.execute(
            "UPDATE viewer_watched_items SET watched_at = ? WHERE viewer_id = ?",
            ("2026-02-15T00:00:00+00:00", viewer_id),
        )

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    by_viewer = resp.json()["watch_activity"]["by_viewer"]
    assert by_viewer == [{"viewer_id": viewer_id, "viewer_name": "Alex", "count": 3, "watch_seconds": 5400.0}]


def test_report_summary_previous_period_is_same_length_immediately_before(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(
        original_path="m1", final_path="m1.mkv", title="This Quarter", media_type="movie",
        archived_at="2026-02-15T00:00:00+00:00",
    )
    db.create_media_item(
        original_path="m2", final_path="m2.mkv", title="Last Quarter", media_type="movie",
        archived_at="2025-12-15T00:00:00+00:00",
    )

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    body = resp.json()
    prev = body["previous_period"]
    assert prev["start_date"] == "2025-10-03"
    assert prev["end_date"] == "2025-12-31"
    assert prev["movies_added"] == 1
    assert body["growth"]["movies_added"] == 1


def test_report_summary_metadata_backlog_counts_current_unmatched_items(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    db.create_media_item(original_path="m1", final_path="m1.mkv", title="Never Tried", media_type="movie")
    failed_id = db.create_media_item(original_path="m2", final_path="m2.mkv", title="Failed", media_type="tv")
    db.update_media_item(failed_id, match_attempted_at="2026-01-01T00:00:00+00:00")

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    backlog = resp.json()["metadata_backlog"]
    assert backlog["pending_movies"] == 1
    assert backlog["failed_movies"] == 0
    assert backlog["pending_tv"] == 1
    assert backlog["failed_tv"] == 1


def test_report_summary_cleanup_activity_counts_deletes_within_range(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    in_range = db.log_operation(operation_type="delete", status="success", details={"path": "/media/movies/Old Movie.mkv"})
    out_of_range = db.log_operation(operation_type="delete", status="success", details={"path": "/media/movies/Other.mkv"})
    failed = db.log_operation(operation_type="delete", status="failed", details={"path": "/media/movies/Locked.mkv"})
    with db.connect() as conn:
        conn.execute("UPDATE operation_log SET created_at = ? WHERE id = ?", ("2026-02-15T00:00:00+00:00", in_range))
        conn.execute("UPDATE operation_log SET created_at = ? WHERE id = ?", ("2026-05-01T00:00:00+00:00", out_of_range))
        conn.execute("UPDATE operation_log SET created_at = ? WHERE id = ?", ("2026-02-15T00:00:00+00:00", failed))

    resp = c.get("/api/reports/summary", params={"start": "2026-01-01", "end": "2026-03-31"})
    cleanup = resp.json()["cleanup_activity"]
    assert cleanup["deleted_count"] == 1
    assert cleanup["failed_count"] == 1
    assert cleanup["deleted_paths"] == ["Old Movie.mkv"]


# ---- Tracker bulk-add ----


def test_bulk_preview_reports_matched_and_unmatched_titles(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.side_effect = lambda title, year=None: (
        [MediaResult(tmdb_id=99, title="Sample Movie", media_type="movie", year=2020)] if title == "Known Movie" else []
    )

    resp = c.post(
        "/api/tracker/bulk-preview",
        json={"titles": ["Known Movie", "Unknown Movie"], "media_type": "movie"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert items[0]["matched"] is True
    assert items[0]["tmdb_id"] == 99
    assert items[1]["matched"] is False
    assert items[1]["tmdb_id"] is None


def test_bulk_preview_skips_blank_lines(client):
    c, _ = client
    fake_tmdb = app.dependency_overrides[get_tmdb_client]()
    fake_tmdb.search_movie.return_value = []

    resp = c.post("/api/tracker/bulk-preview", json={"titles": ["Title", "  ", ""], "media_type": "movie"})
    assert len(resp.json()["items"]) == 1


def test_bulk_add_creates_tracker_rows_for_every_item(client):
    c, _ = client
    db = app.dependency_overrides[get_database]()
    resp = c.post(
        "/api/tracker/bulk-add",
        json={
            "items": [
                {"tmdb_id": 1, "media_type": "movie", "title": "Movie One"},
                {"tmdb_id": 2, "media_type": "tv", "title": "Show Two", "poster_path": "/p.jpg"},
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["added"] == 2
    assert db.get_tracker(1, "movie") is not None
    tv_row = db.get_tracker(2, "tv")
    assert tv_row["title"] == "Show Two"
    assert tv_row["poster_path"] == "/p.jpg"
