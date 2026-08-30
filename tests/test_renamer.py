from __future__ import annotations

from pathlib import Path

from app.core.renamer import plan_movie_rename, plan_tv_rename, sanitize_filename
from app.core.tmdb_client import MediaResult


def test_sanitize_filename_removes_invalid_chars():
    assert sanitize_filename('Movie: The "Sequel"?') == "Movie The Sequel"


def test_plan_movie_rename_builds_expected_structure(tmp_path):
    source = tmp_path / "incoming" / "some.movie.file.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(
        tmdb_id=1, title="Some Movie", media_type="movie", year=2021,
        poster_path="/poster.jpg", overview="A movie.",
        raw={"vote_average": 7.5, "genres": [{"id": 18, "name": "Drama"}]},
    )
    plan = plan_movie_rename(source, archive_root, media)

    assert plan.dest_path == archive_root / "Some Movie (2021)" / "Some Movie (2021).mkv"
    assert plan.poster_path == "/poster.jpg"
    assert plan.overview == "A movie."
    assert plan.vote_average == 7.5
    assert plan.genres == ["Drama"]


def test_plan_tv_rename_builds_expected_structure(tmp_path):
    source = tmp_path / "incoming" / "show.s01e02.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=2, title="Show", media_type="tv")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=2, episode_title="Pilot")

    assert plan.dest_path == archive_root / "Show" / "Season 01" / "Show - S01E02 - Pilot.mkv"


def test_plan_movie_rename_avoids_collision(tmp_path):
    archive_root = tmp_path / "archive"
    existing_dir = archive_root / "Movie (2020)"
    existing_dir.mkdir(parents=True)
    (existing_dir / "Movie (2020).mkv").write_text("existing")

    source = tmp_path / "incoming" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("new")

    media = MediaResult(tmdb_id=3, title="Movie", media_type="movie", year=2020)
    plan = plan_movie_rename(source, archive_root, media)

    assert plan.dest_path.name == "Movie (2020) (2).mkv"
