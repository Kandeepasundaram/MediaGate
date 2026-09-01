from __future__ import annotations

from pathlib import Path

import pytest

from app.config_loader import RenamingConfig
from app.core.renamer import CollisionSkipped, plan_movie_rename, plan_tv_rename, sanitize_filename
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


def test_plan_movie_rename_appends_part_suffix(tmp_path):
    source = tmp_path / "incoming" / "movie.cd1.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=10, title="Movie", media_type="movie", year=2020)
    plan1 = plan_movie_rename(source, archive_root, media, part="Cd1")
    plan2 = plan_movie_rename(source, archive_root, media, part="Cd2")

    assert plan1.dest_path.name == "Movie (2020) - Cd1.mkv"
    assert plan2.dest_path.name == "Movie (2020) - Cd2.mkv"
    assert plan1.dest_path.parent == plan2.dest_path.parent  # same movie folder, distinct files


def test_plan_movie_rename_no_part_suffix_when_not_multi_part(tmp_path):
    source = tmp_path / "incoming" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=11, title="Movie", media_type="movie", year=2020)
    plan = plan_movie_rename(source, archive_root, media)

    assert plan.dest_path.name == "Movie (2020).mkv"


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


def test_plan_movie_rename_honors_custom_template(tmp_path):
    source = tmp_path / "incoming" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=4, title="Movie", media_type="movie", year=2020)
    renaming = RenamingConfig(movie_folder="[{year}] {title}")
    plan = plan_movie_rename(source, archive_root, media, renaming=renaming)

    assert plan.dest_path == archive_root / "[2020] Movie" / "[2020] Movie.mkv"


def test_plan_movie_rename_falls_back_to_default_on_bad_template(tmp_path):
    source = tmp_path / "incoming" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=5, title="Movie", media_type="movie", year=2020)
    renaming = RenamingConfig(movie_folder="{not_a_real_token}")
    plan = plan_movie_rename(source, archive_root, media, renaming=renaming)

    assert plan.dest_path == archive_root / "Movie (2020)" / "Movie (2020).mkv"


def test_plan_movie_rename_overwrite_policy_reuses_existing_path(tmp_path):
    archive_root = tmp_path / "archive"
    existing_dir = archive_root / "Movie (2020)"
    existing_dir.mkdir(parents=True)
    (existing_dir / "Movie (2020).mkv").write_text("existing")

    source = tmp_path / "incoming" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("new")

    media = MediaResult(tmdb_id=7, title="Movie", media_type="movie", year=2020)
    renaming = RenamingConfig(collision_policy="overwrite")
    plan = plan_movie_rename(source, archive_root, media, renaming=renaming)

    assert plan.dest_path == existing_dir / "Movie (2020).mkv"


def test_plan_movie_rename_skip_policy_raises_on_collision(tmp_path):
    archive_root = tmp_path / "archive"
    existing_dir = archive_root / "Movie (2020)"
    existing_dir.mkdir(parents=True)
    (existing_dir / "Movie (2020).mkv").write_text("existing")

    source = tmp_path / "incoming" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("new")

    media = MediaResult(tmdb_id=8, title="Movie", media_type="movie", year=2020)
    renaming = RenamingConfig(collision_policy="skip")

    with pytest.raises(CollisionSkipped):
        plan_movie_rename(source, archive_root, media, renaming=renaming)


def test_plan_tv_rename_honors_custom_templates(tmp_path):
    source = tmp_path / "incoming" / "show.s01e02.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=6, title="Show", media_type="tv")
    renaming = RenamingConfig(tv_season_folder="S{season:02d}", tv_file="{code} - {show_name}")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=2, renaming=renaming)

    assert plan.dest_path == archive_root / "Show" / "S01" / "S01E02 - Show.mkv"


def test_plan_tv_rename_anime_style_absolute_episode_template(tmp_path):
    source = tmp_path / "incoming" / "show.s02e03.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=7, title="Show", media_type="tv")
    renaming = RenamingConfig(tv_season_folder="Season {season:02d}", tv_file="{show_name} - {absolute_episode:03d}")
    plan = plan_tv_rename(source, archive_root, media, season=2, episode=3, renaming=renaming, absolute_episode=15)

    assert plan.dest_path == archive_root / "Show" / "Season 02" / "Show - 015.mkv"


def test_plan_tv_rename_part_suffix_token(tmp_path):
    source = tmp_path / "incoming" / "show.s01e01.cd1.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=12, title="Show", media_type="tv")
    renaming = RenamingConfig(tv_file="{show_name} - {code}{part_suffix}")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=1, renaming=renaming, part="Cd1")

    assert plan.dest_path.name == "Show - S01E01 - Cd1.mkv"


def test_plan_tv_rename_part_suffix_empty_when_no_part(tmp_path):
    source = tmp_path / "incoming" / "show.s01e01.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=13, title="Show", media_type="tv")
    renaming = RenamingConfig(tv_file="{show_name} - {code}{part_suffix}")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=1, renaming=renaming)

    assert plan.dest_path.name == "Show - S01E01.mkv"


def test_plan_tv_rename_stores_air_date(tmp_path):
    source = tmp_path / "incoming" / "show.s01e02.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=14, title="Show", media_type="tv")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=2, air_date="2008-01-27")

    assert plan.air_date == "2008-01-27"


def test_plan_tv_rename_air_date_template_token(tmp_path):
    source = tmp_path / "incoming" / "show.s01e02.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=15, title="Show", media_type="tv")
    renaming = RenamingConfig(tv_file="{show_name} - {code} - {air_date}")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=2, air_date="2008-01-27", renaming=renaming)

    assert plan.dest_path.name == "Show - S01E02 - 2008-01-27.mkv"


def test_plan_tv_rename_air_date_defaults_to_none(tmp_path):
    source = tmp_path / "incoming" / "show.s01e02.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=16, title="Show", media_type="tv")
    plan = plan_tv_rename(source, archive_root, media, season=1, episode=2)

    assert plan.air_date is None


def test_plan_tv_rename_absolute_episode_falls_back_to_in_season_episode(tmp_path):
    source = tmp_path / "incoming" / "show.s02e03.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("data")
    archive_root = tmp_path / "archive"

    media = MediaResult(tmdb_id=8, title="Show", media_type="tv")
    renaming = RenamingConfig(tv_season_folder="Season {season:02d}", tv_file="{show_name} - {absolute_episode:03d}")
    plan = plan_tv_rename(source, archive_root, media, season=2, episode=3, renaming=renaming)  # no absolute_episode passed

    assert plan.dest_path == archive_root / "Show" / "Season 02" / "Show - 003.mkv"
