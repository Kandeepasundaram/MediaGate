from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.config_loader import MediaServerConfig
from app.core.media_server import (
    jellyfin_item_id_for_imdb,
    list_jellyfin_sessions,
    notify_media_servers,
    play_on_jellyfin_session,
    sync_watched_from_media_servers,
)


class _FakeConfig:
    def __init__(self, **kwargs):
        self.media_server = MediaServerConfig(**kwargs)


def test_notify_media_servers_noop_when_unconfigured():
    with patch("app.core.media_server.requests.get") as mock_get, patch(
        "app.core.media_server.requests.post"
    ) as mock_post:
        notify_media_servers(_FakeConfig())
    mock_get.assert_not_called()
    mock_post.assert_not_called()


def test_notify_media_servers_calls_plex_when_configured():
    config = _FakeConfig(plex_url="http://plex.local:32400", plex_token="tok")
    with patch("app.core.media_server.requests.get") as mock_get:
        notify_media_servers(config)
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == "http://plex.local:32400/library/sections/all/refresh"
    assert mock_get.call_args.kwargs["params"]["X-Plex-Token"] == "tok"


def test_notify_media_servers_calls_jellyfin_when_configured():
    config = _FakeConfig(jellyfin_url="http://jellyfin.local:8096", jellyfin_api_key="key")
    with patch("app.core.media_server.requests.post") as mock_post:
        notify_media_servers(config)
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "http://jellyfin.local:8096/Library/Refresh"
    assert mock_post.call_args.kwargs["headers"]["X-Emby-Token"] == "key"


def test_notify_media_servers_requires_both_url_and_credential():
    config = _FakeConfig(plex_url="http://plex.local:32400")  # no token
    with patch("app.core.media_server.requests.get") as mock_get:
        notify_media_servers(config)
    mock_get.assert_not_called()


def test_notify_media_servers_swallows_request_errors():
    import requests

    config = _FakeConfig(plex_url="http://plex.local:32400", plex_token="tok")
    with patch("app.core.media_server.requests.get", side_effect=requests.RequestException("down")):
        notify_media_servers(config)  # must not raise


def _seed_movie(db, imdb_id=None, watched=0, title="Movie"):
    return db.create_media_item(
        original_path="x", final_path=f"/archive/{title}.mkv", title=title, media_type="movie",
        watched=watched, imdb_id=imdb_id,
    )


def test_sync_watched_marks_movies_plex_reports_as_viewed(db):
    config = _FakeConfig(plex_url="http://plex.local:32400", plex_token="tok")
    watched_id = _seed_movie(db, imdb_id="tt0000001")
    already_watched_id = _seed_movie(db, imdb_id="tt0000002", watched=1, title="Already")
    unwatched_id = _seed_movie(db, imdb_id="tt0000003", title="Unwatched")

    plex_resp = MagicMock()
    plex_resp.json.return_value = {
        "MediaContainer": {
            "Metadata": [
                {"viewCount": 1, "Guid": [{"id": "imdb://tt0000001"}]},
                {"viewCount": 0, "Guid": [{"id": "imdb://tt0000003"}]},
            ]
        }
    }
    with patch("app.core.media_server.requests.get", return_value=plex_resp):
        updated = sync_watched_from_media_servers(config, db)

    assert updated == 1
    assert db.get_media_item(watched_id)["watched"] == 1
    assert db.get_media_item(already_watched_id)["watched"] == 1
    assert db.get_media_item(unwatched_id)["watched"] == 0


def test_sync_watched_never_unwatches(db):
    config = _FakeConfig(plex_url="http://plex.local:32400", plex_token="tok")
    item_id = _seed_movie(db, imdb_id="tt0000009", watched=1)

    plex_resp = MagicMock()
    plex_resp.json.return_value = {"MediaContainer": {"Metadata": []}}
    with patch("app.core.media_server.requests.get", return_value=plex_resp):
        updated = sync_watched_from_media_servers(config, db)

    assert updated == 0
    assert db.get_media_item(item_id)["watched"] == 1


def test_sync_watched_reads_jellyfin_provider_ids(db):
    config = _FakeConfig(jellyfin_url="http://jf.local:8096", jellyfin_api_key="key")
    item_id = _seed_movie(db, imdb_id="tt0000005")

    users_resp = MagicMock()
    users_resp.json.return_value = [{"Id": "user-1"}]
    items_resp = MagicMock()
    items_resp.json.return_value = {"Items": [{"ProviderIds": {"Imdb": "tt0000005"}}]}
    with patch("app.core.media_server.requests.get", side_effect=[users_resp, items_resp]):
        updated = sync_watched_from_media_servers(config, db)

    assert updated == 1
    assert db.get_media_item(item_id)["watched"] == 1


def test_sync_watched_noop_when_no_server_configured(db):
    config = _FakeConfig()
    _seed_movie(db, imdb_id="tt0000001")
    with patch("app.core.media_server.requests.get") as mock_get:
        updated = sync_watched_from_media_servers(config, db)
    mock_get.assert_not_called()
    assert updated == 0


def test_sync_watched_swallows_request_errors(db):
    import requests

    config = _FakeConfig(plex_url="http://plex.local:32400", plex_token="tok")
    _seed_movie(db, imdb_id="tt0000001")
    with patch("app.core.media_server.requests.get", side_effect=requests.RequestException("down")):
        updated = sync_watched_from_media_servers(config, db)  # must not raise
    assert updated == 0


def test_jellyfin_item_id_for_imdb_matches_provider_id():
    items_resp = MagicMock()
    items_resp.json.return_value = {
        "Items": [
            {"Id": "abc123", "ProviderIds": {"Imdb": "tt0000001"}},
            {"Id": "xyz789", "ProviderIds": {"Imdb": "tt0000002"}},
        ]
    }
    with patch("app.core.media_server.requests.get", return_value=items_resp) as mock_get:
        result = jellyfin_item_id_for_imdb("http://jf.local:8096", "key", "tt0000002")

    assert result == "xyz789"
    assert mock_get.call_args.kwargs["headers"]["X-Emby-Token"] == "key"


def test_jellyfin_item_id_for_imdb_returns_none_when_no_match():
    items_resp = MagicMock()
    items_resp.json.return_value = {"Items": [{"Id": "abc123", "ProviderIds": {"Imdb": "tt0000001"}}]}
    with patch("app.core.media_server.requests.get", return_value=items_resp):
        result = jellyfin_item_id_for_imdb("http://jf.local:8096", "key", "tt9999999")
    assert result is None


def test_jellyfin_item_id_for_imdb_swallows_request_errors():
    import requests

    with patch("app.core.media_server.requests.get", side_effect=requests.RequestException("down")):
        result = jellyfin_item_id_for_imdb("http://jf.local:8096", "key", "tt0000001")  # must not raise
    assert result is None


def test_list_jellyfin_sessions_filters_remote_controllable():
    sessions_resp = MagicMock()
    sessions_resp.json.return_value = [
        {"Id": "s1", "DeviceName": "Kodi - Living Room", "SupportsRemoteControl": True},
        {"Id": "s2", "Client": "Some Browser", "SupportsRemoteControl": False},
    ]
    with patch("app.core.media_server.requests.get", return_value=sessions_resp):
        sessions = list_jellyfin_sessions("http://jf.local:8096", "key")

    assert sessions == [{"id": "s1", "name": "Kodi - Living Room"}]


def test_list_jellyfin_sessions_swallows_request_errors():
    import requests

    with patch("app.core.media_server.requests.get", side_effect=requests.RequestException("down")):
        sessions = list_jellyfin_sessions("http://jf.local:8096", "key")  # must not raise
    assert sessions == []


def test_play_on_jellyfin_session_posts_expected_params():
    post_resp = MagicMock(ok=True)
    with patch("app.core.media_server.requests.post", return_value=post_resp) as mock_post:
        success = play_on_jellyfin_session("http://jf.local:8096", "key", "session-1", "item-1")

    assert success is True
    assert mock_post.call_args.args[0] == "http://jf.local:8096/Sessions/session-1/Playing"
    assert mock_post.call_args.kwargs["params"] == {"playCommand": "PlayNow", "itemIds": "item-1"}
    assert mock_post.call_args.kwargs["headers"]["X-Emby-Token"] == "key"


def test_play_on_jellyfin_session_swallows_request_errors():
    import requests

    with patch("app.core.media_server.requests.post", side_effect=requests.RequestException("down")):
        success = play_on_jellyfin_session("http://jf.local:8096", "key", "session-1", "item-1")  # must not raise
    assert success is False
