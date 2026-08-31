from __future__ import annotations

import os
from pathlib import Path

from app.config_loader import (
    SubtitlesConfig,
    get_config_history_diff,
    keep_languages_for,
    list_config_history,
    load_config,
    rollback_config_version,
    update_settings,
)


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


def test_update_settings_creates_config_history_entry(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)

    assert list_config_history(config_path) == []
    update_settings(config_path, {"tmdb": {"api_key": "abc123"}})

    history = list_config_history(config_path)
    assert len(history) == 1
    assert history[0]["version"].endswith(".yaml")
    assert history[0]["size_bytes"] > 0


def test_config_history_lists_newest_first(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    history_dir = tmp_path / "config_history"
    history_dir.mkdir()
    (history_dir / "20260101T000000000000Z.yaml").write_text("paths: {}", encoding="utf-8")
    (history_dir / "20260102T000000000000Z.yaml").write_text("paths: {}", encoding="utf-8")

    history = list_config_history(config_path)
    assert len(history) == 2
    assert history[0]["version"] == "20260102T000000000000Z.yaml"
    assert history[1]["version"] == "20260101T000000000000Z.yaml"


def test_config_history_prunes_beyond_keep_limit(tmp_path, monkeypatch):
    import app.config_loader as config_loader_module

    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    monkeypatch.setattr(config_loader_module, "_CONFIG_HISTORY_KEEP", 3)

    for i in range(5):
        # Distinct filenames without a real sleep between saves --
        # _snapshot_config_history's own collision check only skips a
        # write when the timestamp-derived filename already exists, so
        # writing the history files directly (same effect, no timing
        # dependency) exercises the same prune path list_config_history
        # would see after 5 real saves in 5 different seconds.
        (config_path.parent / "config_history").mkdir(exist_ok=True)
        (config_path.parent / "config_history" / f"2026010{i+1}T000000000000Z.yaml").write_text("x", encoding="utf-8")
    assert len(list_config_history(config_path)) == 5

    # list_config_history itself doesn't prune -- pruning happens inside
    # _snapshot_config_history at write time. Call it directly to confirm
    # it trims the 5 pre-existing entries plus its own new one down to
    # _CONFIG_HISTORY_KEEP.
    config_loader_module._snapshot_config_history(config_path)
    assert len(list_config_history(config_path)) == 3


def test_get_config_history_diff_shows_changed_line(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    update_settings(config_path, {"tmdb": {"api_key": "abc123"}})

    version = list_config_history(config_path)[0]["version"]
    diff = get_config_history_diff(config_path, version)
    diff_text = "".join(diff)
    assert "api_key" in diff_text


def test_get_config_history_diff_rejects_path_traversal(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    update_settings(config_path, {"tmdb": {"api_key": "abc123"}})

    import pytest
    with pytest.raises(FileNotFoundError):
        get_config_history_diff(config_path, "../../../../etc/passwd")


def test_rollback_config_version_restores_previous_settings(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    # This save's own pre-change snapshot captures the default (empty
    # api_key) config -- grabbing that version (rather than depending on a
    # second save landing in a different wall-clock second, which
    # _snapshot_config_history's same-second dedup would otherwise skip)
    # keeps this test deterministic.
    update_settings(config_path, {"tmdb": {"api_key": "original-key"}})
    version = list_config_history(config_path)[0]["version"]
    update_settings(config_path, {"tmdb": {"api_key": "changed-key"}})

    cfg = rollback_config_version(config_path, version)

    assert cfg.tmdb.api_key == ""


def test_rollback_config_version_snapshots_current_state_first(tmp_path):
    config_path = tmp_path / "config.yaml"
    load_config(config_path)
    history_dir = config_path.parent / "config_history"
    history_dir.mkdir()
    (history_dir / "20260101T000000000000Z.yaml").write_text("paths: {}", encoding="utf-8")

    rollback_config_version(config_path, "20260101T000000000000Z.yaml")

    # the pre-rollback config.yaml is itself now preserved as a second entry
    assert len(list_config_history(config_path)) == 2


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
