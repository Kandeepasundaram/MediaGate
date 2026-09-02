from __future__ import annotations

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
from app.core.library_adopt import adopt_new_files


def _config(tmp_path) -> AppConfig:
    dirs = {name: tmp_path / name for name in ("incoming_movies", "incoming_tv", "archive_movies", "archive_tv")}
    for d in dirs.values():
        d.mkdir(parents=True)
    return AppConfig(
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
    )


def test_adopt_new_files_registers_untracked_movie(db, tmp_path):
    config = _config(tmp_path)
    (config.paths.archive_movies / "Movie.2020.mkv").write_bytes(b"data")

    adopted = adopt_new_files(db, config, "movie")

    assert adopted == 1
    items = db.list_media_items(media_type="movie")
    assert len(items) == 1
    assert items[0]["title"] == "Movie"
    assert items[0]["year"] == 2020
    assert items[0]["tmdb_id"] is None  # no network call made
    assert items[0]["final_path"] == items[0]["original_path"]  # no file was moved


def test_adopt_new_files_stamps_archived_at(db, tmp_path):
    """Regression: reports.py/status.py both key growth figures off
    archived_at, so a row left with archived_at=NULL is permanently
    invisible to "added this period" -- adoption must stamp it even though
    it copied/moved nothing."""
    config = _config(tmp_path)
    (config.paths.archive_movies / "Movie.2020.mkv").write_bytes(b"data")

    adopt_new_files(db, config, "movie")

    item = db.list_media_items(media_type="movie")[0]
    assert item["archived_at"] is not None


def test_adopt_new_files_skips_already_tracked(db, tmp_path):
    config = _config(tmp_path)
    video = config.paths.archive_movies / "Movie.2020.mkv"
    video.write_bytes(b"data")
    db.create_media_item(
        original_path=str(video), final_path=str(video), title="Movie", media_type="movie"
    )

    adopted = adopt_new_files(db, config, "movie")

    assert adopted == 0
    assert len(db.list_media_items(media_type="movie")) == 1


def test_adopt_new_files_parses_tv_season_episode(db, tmp_path):
    config = _config(tmp_path)
    show_dir = config.paths.archive_tv / "Show" / "Season 01"
    show_dir.mkdir(parents=True)
    (show_dir / "Show - S01E02.mkv").write_bytes(b"data")

    adopted = adopt_new_files(db, config, "tv")

    assert adopted == 1
    item = db.list_media_items(media_type="tv")[0]
    assert item["season_number"] == 1
    assert item["episode_number"] == 2


def test_adopt_new_files_empty_directory(db, tmp_path):
    config = _config(tmp_path)
    assert adopt_new_files(db, config, "movie") == 0
