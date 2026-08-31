from __future__ import annotations

import os
from pathlib import Path

from app.config_loader import SubtitlesConfig, keep_languages_for, load_config, update_settings


def test_load_config_creates_default_file_if_missing(tmp_path):
    config_path = tmp_path / "config.yaml"
    cfg = load_config(config_path)

    assert config_path.exists()
    assert cfg.server.port == 8000
    assert cfg.subtitles.keep_languages == ["en", "eng", "english"]
    assert cfg.renaming.movie_folder == "{title}{year_suffix}"
    assert cfg.watcher.enabled is False
    assert cfg.paths.incoming_movies.exists()  # create_dirs defaults to True
    assert cfg.paths.incoming_tv.exists()


def test_load_config_env_override_for_tmdb_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "secret-from-env")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.tmdb.api_key == "secret-from-env"


def test_load_config_merges_partial_user_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9999\n")

    cfg = load_config(config_path)

    assert cfg.server.port == 9999
    assert cfg.server.host == "0.0.0.0"  # default preserved for unspecified keys


def test_update_settings_writes_editable_paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)  # create the default file

    new_incoming = tmp_path / "custom" / "incoming_movies"
    cfg = update_settings(config_path, {"paths": {"incoming_movies": str(new_incoming)}})

    assert cfg.paths.incoming_movies == new_incoming
    # untouched sibling paths keep their previous values
    assert cfg.paths.incoming_tv == Path("./sample_media/incoming/tv")
    assert cfg.paths.archive_movies == Path("./sample_media/archive/movies")


def test_update_settings_ignores_non_editable_keys(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)

    cfg = update_settings(config_path, {"database": {"path": "/should/not/change.db"}})

    assert cfg.database_path == Path("./data/media_manager.db")


def test_update_settings_tmdb_key_round_trips(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)

    cfg = update_settings(config_path, {"tmdb": {"api_key": "abc123"}})

    assert cfg.tmdb.api_key == "abc123"
    assert cfg.tmdb_api_key_from_env is False


def test_update_settings_webhook_url_round_trips(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)

    cfg = update_settings(config_path, {"notifications": {"webhook_url": "https://example.com/hook"}})

    assert cfg.notifications.webhook_url == "https://example.com/hook"


def test_update_settings_backs_up_previous_file(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    original_contents = config_path.read_bytes()

    update_settings(config_path, {"tmdb": {"api_key": "abc123"}})

    backup_path = config_path.with_suffix(".yaml.bak")
    assert backup_path.exists()
    assert backup_path.read_bytes() == original_contents


def test_update_settings_renaming_template_round_trips(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)

    cfg = update_settings(config_path, {"renaming": {"movie_folder": "[{year}] {title}"}})

    assert cfg.renaming.movie_folder == "[{year}] {title}"
    # untouched sibling templates keep their previous values
    assert cfg.renaming.tv_file == "{show_name} - {code}{episode_title_suffix}"


def test_keep_languages_for_falls_back_to_default_when_no_override():
    subtitles = SubtitlesConfig(keep_languages=["en"])
    assert keep_languages_for(subtitles, "movie") == ["en"]
    assert keep_languages_for(subtitles, "tv") == ["en"]


def test_keep_languages_for_honors_per_type_override():
    subtitles = SubtitlesConfig(keep_languages=["en"], keep_languages_movies=["en", "fr"], keep_languages_tv=["en", "ja"])
    assert keep_languages_for(subtitles, "movie") == ["en", "fr"]
    assert keep_languages_for(subtitles, "tv") == ["en", "ja"]


def test_update_settings_per_type_subtitle_languages_round_trip(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)

    cfg = update_settings(config_path, {"subtitles": {"keep_languages_tv": ["en", "ja"]}})

    assert cfg.subtitles.keep_languages_tv == ["en", "ja"]
    assert cfg.subtitles.keep_languages_movies == []  # untouched, still falls back to default


def test_load_config_no_create_dirs(tmp_path):
    config_path = tmp_path / "config.yaml"
    target_dir = tmp_path / "would_be_created" / "incoming_movies"
    config_path.write_text(f"paths:\n  incoming_movies: {target_dir.as_posix()!r}\n")

    cfg = load_config(config_path, create_dirs=False)

    assert cfg.paths.incoming_movies == target_dir
    assert not target_dir.exists()
