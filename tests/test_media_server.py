from __future__ import annotations

from unittest.mock import patch

from app.config_loader import MediaServerConfig
from app.core.media_server import notify_media_servers


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
