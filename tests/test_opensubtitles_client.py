from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.core.opensubtitles_client import OpenSubtitlesClient, SubtitleMatch, _normalize_language


def test_normalize_language_aliases():
    assert _normalize_language("eng") == "en"
    assert _normalize_language("english") == "en"
    assert _normalize_language("EN") == "en"
    assert _normalize_language("fra") == "fr"  # best-effort fallback: first 2 chars


def test_client_disabled_without_api_key():
    client = OpenSubtitlesClient(api_key="")
    assert client.enabled is False
    assert client.find_subtitle(123, "en", "movie") is None
    assert client.download_subtitle(1) is None


def _search_response(results):
    resp = MagicMock()
    resp.json.return_value = {"data": results}
    return resp


def test_find_subtitle_returns_best_match():
    client = OpenSubtitlesClient(api_key="key")
    results = [
        {"attributes": {"language": "en", "release": "WEB-DL", "files": [{"file_id": 555}]}},
        {"attributes": {"language": "en", "release": "BluRay", "files": [{"file_id": 999}]}},
    ]
    with patch("app.core.opensubtitles_client.requests.get", return_value=_search_response(results)) as mock_get:
        match = client.find_subtitle(123, "en", "movie")

    assert match == SubtitleMatch(file_id=555, language="en", release="WEB-DL")
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["tmdb_id"] == 123
    assert call_kwargs["params"]["languages"] == "en"
    assert call_kwargs["params"]["type"] == "movie"
    assert call_kwargs["headers"]["Api-Key"] == "key"


def test_find_subtitle_tv_includes_season_episode():
    client = OpenSubtitlesClient(api_key="key")
    with patch("app.core.opensubtitles_client.requests.get", return_value=_search_response([])) as mock_get:
        client.find_subtitle(9, "en", "tv", season=1, episode=2)

    params = mock_get.call_args.kwargs["params"]
    assert params["type"] == "episode"
    assert params["season_number"] == 1
    assert params["episode_number"] == 2


def test_find_subtitle_returns_none_when_no_results():
    client = OpenSubtitlesClient(api_key="key")
    with patch("app.core.opensubtitles_client.requests.get", return_value=_search_response([])):
        assert client.find_subtitle(123, "en", "movie") is None


def test_find_subtitle_returns_none_when_result_has_no_files():
    client = OpenSubtitlesClient(api_key="key")
    results = [{"attributes": {"language": "en", "files": []}}]
    with patch("app.core.opensubtitles_client.requests.get", return_value=_search_response(results)):
        assert client.find_subtitle(123, "en", "movie") is None


def test_find_subtitle_swallows_request_errors():
    client = OpenSubtitlesClient(api_key="key")
    with patch("app.core.opensubtitles_client.requests.get", side_effect=requests.RequestException("down")):
        assert client.find_subtitle(123, "en", "movie") is None


def test_download_subtitle_fetches_link_content():
    client = OpenSubtitlesClient(api_key="key")
    download_resp = MagicMock()
    download_resp.json.return_value = {"link": "https://dl.example.com/sub.srt"}
    content_resp = MagicMock()
    content_resp.content = b"subtitle bytes"

    with patch("app.core.opensubtitles_client.requests.post", return_value=download_resp) as mock_post, \
         patch("app.core.opensubtitles_client.requests.get", return_value=content_resp) as mock_get:
        content = client.download_subtitle(42)

    assert content == b"subtitle bytes"
    assert mock_post.call_args.kwargs["json"] == {"file_id": 42}
    assert mock_get.call_args.args[0] == "https://dl.example.com/sub.srt"


def test_download_subtitle_returns_none_when_no_link():
    client = OpenSubtitlesClient(api_key="key")
    download_resp = MagicMock()
    download_resp.json.return_value = {}
    with patch("app.core.opensubtitles_client.requests.post", return_value=download_resp):
        assert client.download_subtitle(42) is None


def test_download_subtitle_swallows_request_errors():
    client = OpenSubtitlesClient(api_key="key")
    with patch("app.core.opensubtitles_client.requests.post", side_effect=requests.RequestException("down")):
        assert client.download_subtitle(42) is None
