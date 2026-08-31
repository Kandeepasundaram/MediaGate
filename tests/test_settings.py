from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config_loader import load_config
from app.database import Database
from app.dependencies import get_config, get_database, get_tmdb_client
from app.main import app


@pytest.fixture
def client(tmp_path):
    config_path = tmp_path / "config.yaml"
    incoming_movies = tmp_path / "incoming" / "movies"
    incoming_tv = tmp_path / "incoming" / "tv"
    archive_movies = tmp_path / "archive" / "movies"
    archive_tv = tmp_path / "archive" / "tv"

    load_config(config_path)
    for d in (incoming_movies, incoming_tv, archive_movies, archive_tv):
        d.mkdir(parents=True, exist_ok=True)
    # Point the freshly-created default config at real tmp_path dirs so the
    # permissions-check endpoint has something real to probe.
    from app.config_loader import update_settings

    update_settings(
        config_path,
        {
            "paths": {
                "incoming_movies": str(incoming_movies),
                "incoming_tv": str(incoming_tv),
                "archive_movies": str(archive_movies),
                "archive_tv": str(archive_tv),
            }
        },
    )

    db = Database(tmp_path / "test.db")
    db.init_db()
    fake_tmdb = MagicMock()
    fake_tmdb.mode = "scraper"

    # Reload fresh from disk on every call so a POST's write is immediately
    # visible to the next GET, mirroring the real reset_singletons() flow.
    app.dependency_overrides[get_config] = lambda: load_config(config_path, create_dirs=False)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_tmdb_client] = lambda: fake_tmdb

    with TestClient(app) as c:
        yield c, config_path

    app.dependency_overrides.clear()


def test_get_settings_reflects_config(client):
    c, _ = client
    resp = c.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_api_key_set"] is False
    assert body["tmdb_api_key_locked_by_env"] is False


def test_save_settings_updates_paths(client, tmp_path):
    c, _ = client
    new_dir = str(tmp_path / "new_incoming_movies")
    resp = c.post("/api/settings", json={"incoming_movies": new_dir})
    assert resp.status_code == 200
    assert resp.json()["incoming_movies"] == new_dir

    # Persisted: a subsequent GET reflects the change.
    resp2 = c.get("/api/settings")
    assert resp2.json()["incoming_movies"] == new_dir


def test_save_settings_tmdb_key_is_never_echoed_back(client):
    c, _ = client
    resp = c.post("/api/settings", json={"tmdb_api_key": "super-secret"})
    assert resp.status_code == 200
    assert "super-secret" not in resp.text
    assert resp.json()["tmdb_api_key_set"] is True


def test_save_settings_ignores_env_locked_key(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("TMDB_API_KEY", "from-env")

    resp = c.get("/api/settings")
    assert resp.json()["tmdb_api_key_locked_by_env"] is True

    # Attempting to overwrite it via the API is a no-op.
    c.post("/api/settings", json={"tmdb_api_key": "attempted-override"})
    resp2 = c.get("/api/settings")
    assert resp2.json()["tmdb_api_key_locked_by_env"] is True


def test_get_settings_reflects_default_subtitle_languages(client):
    c, _ = client
    resp = c.get("/api/settings")
    assert resp.json()["subtitle_keep_languages"] == ["en", "eng", "english"]


def test_save_settings_updates_subtitle_languages(client):
    c, _ = client
    resp = c.post("/api/settings", json={"subtitle_keep_languages": ["FR", " es ", ""]})
    assert resp.status_code == 200
    assert resp.json()["subtitle_keep_languages"] == ["fr", "es"]

    resp2 = c.get("/api/settings")
    assert resp2.json()["subtitle_keep_languages"] == ["fr", "es"]


def test_get_settings_reflects_default_per_type_subtitle_languages(client):
    c, _ = client
    body = c.get("/api/settings").json()
    assert body["subtitle_keep_languages_movies"] == []
    assert body["subtitle_keep_languages_tv"] == []


def test_save_settings_updates_per_type_subtitle_languages(client):
    c, _ = client
    resp = c.post("/api/settings", json={
        "subtitle_keep_languages_movies": ["FR"],
        "subtitle_keep_languages_tv": ["EN", "JA"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["subtitle_keep_languages_movies"] == ["fr"]
    assert body["subtitle_keep_languages_tv"] == ["en", "ja"]


def test_get_settings_reflects_default_naming_templates(client):
    c, _ = client
    body = c.get("/api/settings").json()
    assert body["movie_folder_template"] == "{title}{year_suffix}"
    assert body["tv_season_folder_template"] == "Season {season:02d}"
    assert body["tv_file_template"] == "{show_name} - {code}{episode_title_suffix}"


def test_save_settings_updates_naming_templates(client):
    c, _ = client
    resp = c.post("/api/settings", json={"movie_folder_template": "[{year}] {title}"})
    assert resp.status_code == 200
    assert resp.json()["movie_folder_template"] == "[{year}] {title}"

    resp2 = c.get("/api/settings")
    assert resp2.json()["movie_folder_template"] == "[{year}] {title}"
    # untouched fields keep their previous value
    assert resp2.json()["tv_file_template"] == "{show_name} - {code}{episode_title_suffix}"


def test_get_settings_reflects_default_collision_policy(client):
    c, _ = client
    assert c.get("/api/settings").json()["collision_policy"] == "suffix"


def test_save_settings_updates_collision_policy(client):
    c, _ = client
    resp = c.post("/api/settings", json={"collision_policy": "overwrite"})
    assert resp.status_code == 200
    assert resp.json()["collision_policy"] == "overwrite"

    resp2 = c.get("/api/settings")
    assert resp2.json()["collision_policy"] == "overwrite"


def test_save_settings_rejects_invalid_collision_policy(client):
    c, _ = client
    resp = c.post("/api/settings", json={"collision_policy": "explode"})
    assert resp.status_code == 400


def test_get_settings_reflects_default_notification_channels(client):
    c, _ = client
    body = c.get("/api/settings").json()
    assert body["discord_webhook_url"] == ""
    assert body["telegram_bot_token_set"] is False
    assert body["pushover_api_token_set"] is False
    assert body["pushover_user_key_set"] is False


def test_save_settings_updates_discord_and_telegram_chat_id(client):
    c, _ = client
    resp = c.post("/api/settings", json={
        "discord_webhook_url": "https://discord.com/api/webhooks/x",
        "telegram_chat_id": "12345",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["discord_webhook_url"] == "https://discord.com/api/webhooks/x"
    assert body["telegram_chat_id"] == "12345"


def test_save_settings_telegram_bot_token_is_never_echoed_back(client):
    c, _ = client
    resp = c.post("/api/settings", json={"telegram_bot_token": "super-secret-token"})
    assert resp.status_code == 200
    assert "super-secret-token" not in resp.text
    assert resp.json()["telegram_bot_token_set"] is True


def test_save_settings_pushover_credentials_are_never_echoed_back(client):
    c, _ = client
    resp = c.post("/api/settings", json={"pushover_api_token": "app-secret", "pushover_user_key": "user-secret"})
    assert resp.status_code == 200
    assert "app-secret" not in resp.text
    assert "user-secret" not in resp.text
    assert resp.json()["pushover_api_token_set"] is True
    assert resp.json()["pushover_user_key_set"] is True


def test_get_settings_reflects_default_digest_mode(client):
    c, _ = client
    body = c.get("/api/settings").json()
    assert body["digest_mode"] is False
    assert body["digest_interval_days"] == 1


def test_save_settings_updates_digest_mode_and_interval(client):
    c, _ = client
    resp = c.post("/api/settings", json={"digest_mode": True, "digest_interval_days": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["digest_mode"] is True
    assert body["digest_interval_days"] == 7


def test_save_settings_clamps_digest_interval_to_at_least_one_day(client):
    c, _ = client
    resp = c.post("/api/settings", json={"digest_interval_days": 0})
    assert resp.json()["digest_interval_days"] == 1


def test_get_settings_reflects_default_watcher_enabled(client):
    c, _ = client
    assert c.get("/api/settings").json()["watcher_enabled"] is False


def test_save_settings_updates_watcher_enabled(client):
    c, _ = client
    resp = c.post("/api/settings", json={"watcher_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["watcher_enabled"] is True

    resp2 = c.get("/api/settings")
    assert resp2.json()["watcher_enabled"] is True


def test_permissions_check_reports_writable_dirs(client):
    c, _ = client
    resp = c.get("/api/settings/permissions-check")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["paths"]) == 4
    assert all(p["writable"] for p in body["paths"])


def test_permissions_check_dedupes_when_incoming_equals_archive(client, tmp_path):
    c, _ = client
    same_dir = str(tmp_path / "movies_in_place")
    Path(same_dir).mkdir()
    c.post("/api/settings", json={"incoming_movies": same_dir, "archive_movies": same_dir})

    resp = c.get("/api/settings/permissions-check")
    body = resp.json()
    assert len(body["paths"]) == 3  # incoming_movies == archive_movies, deduped
    assert sum(1 for p in body["paths"] if p["path"] == same_dir) == 1


def test_permissions_check_reports_missing_dir(client, tmp_path):
    c, _ = client
    missing = str(tmp_path / "does_not_exist")
    c.post("/api/settings", json={"incoming_movies": missing})

    resp = c.get("/api/settings/permissions-check")
    body = resp.json()
    missing_check = next(p for p in body["paths"] if p["path"] == missing)
    assert missing_check["exists"] is False
    assert missing_check["writable"] is False
