from __future__ import annotations

import os

from app.config_loader import load_config


def test_load_config_creates_default_file_if_missing(tmp_path):
    config_path = tmp_path / "config.yaml"
    cfg = load_config(config_path)

    assert config_path.exists()
    assert cfg.server.port == 8000
    assert cfg.subtitles.keep_languages == ["en", "eng", "english"]
    assert cfg.paths.active_dir.exists()  # create_dirs defaults to True


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


def test_load_config_no_create_dirs(tmp_path):
    config_path = tmp_path / "config.yaml"
    target_dir = tmp_path / "would_be_created" / "incoming"
    config_path.write_text(f"paths:\n  active_dir: {target_dir.as_posix()!r}\n")

    cfg = load_config(config_path, create_dirs=False)

    assert cfg.paths.active_dir == target_dir
    assert not target_dir.exists()
