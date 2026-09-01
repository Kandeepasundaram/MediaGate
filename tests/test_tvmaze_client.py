from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.core.tvmaze_client import TVmazeClient, TVmazeEpisode, TVmazeShowInfo


def _json_response(data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = data
    return resp


def test_client_disabled_by_default():
    client = TVmazeClient()
    assert client.enabled is False
    assert client.lookup_show_id_by_imdb("tt0903747") is None
    assert client.get_show_info(169) is None
    assert client.get_episodes(169) == []
    assert client.get_episode_by_imdb("tt0903747", 1, 1) is None
    assert client.get_show_info_by_imdb("tt0903747") is None


def test_lookup_show_id_by_imdb():
    client = TVmazeClient(enabled=True)
    with patch("app.core.tvmaze_client.requests.get", return_value=_json_response({"id": 169})) as mock_get:
        show_id = client.lookup_show_id_by_imdb("tt0903747")

    assert show_id == 169
    assert mock_get.call_args.kwargs["params"] == {"imdb": "tt0903747"}


def test_lookup_show_id_by_imdb_caches_result():
    client = TVmazeClient(enabled=True)
    with patch("app.core.tvmaze_client.requests.get", return_value=_json_response({"id": 169})) as mock_get:
        client.lookup_show_id_by_imdb("tt0903747")
        client.lookup_show_id_by_imdb("tt0903747")

    assert mock_get.call_count == 1


def test_get_show_info_parses_status_network_and_next_episode():
    client = TVmazeClient(enabled=True)
    data = {
        "status": "Ended",
        "network": {"name": "AMC"},
        "_embedded": {"nextepisode": {"season": 5, "number": 16, "airdate": "2013-09-29"}},
    }
    with patch("app.core.tvmaze_client.requests.get", return_value=_json_response(data)):
        info = client.get_show_info(169)

    assert info == TVmazeShowInfo(
        tvmaze_id=169, status="Ended", network="AMC",
        next_episode_air_date="2013-09-29", next_episode_code="S05E16",
    )


def test_get_show_info_handles_missing_next_episode_and_web_channel():
    client = TVmazeClient(enabled=True)
    data = {"status": "Running", "webChannel": {"name": "Netflix"}}
    with patch("app.core.tvmaze_client.requests.get", return_value=_json_response(data)):
        info = client.get_show_info(1)

    assert info.network == "Netflix"
    assert info.next_episode_air_date is None
    assert info.next_episode_code is None


def test_get_episodes_parses_list():
    client = TVmazeClient(enabled=True)
    data = [
        {"season": 1, "number": 1, "name": "Pilot", "airdate": "2008-01-20"},
        {"season": 1, "number": 2, "name": "Cat's in the Bag...", "airdate": "2008-01-27"},
    ]
    with patch("app.core.tvmaze_client.requests.get", return_value=_json_response(data)):
        episodes = client.get_episodes(169)

    assert episodes == [
        TVmazeEpisode(season=1, episode=1, name="Pilot", air_date="2008-01-20"),
        TVmazeEpisode(season=1, episode=2, name="Cat's in the Bag...", air_date="2008-01-27"),
    ]


def test_get_episode_by_imdb_chains_lookup_and_episode_list():
    client = TVmazeClient(enabled=True)
    lookup_resp = _json_response({"id": 169})
    episodes_resp = _json_response([{"season": 1, "number": 1, "name": "Pilot", "airdate": "2008-01-20"}])
    with patch("app.core.tvmaze_client.requests.get", side_effect=[lookup_resp, episodes_resp]):
        ep = client.get_episode_by_imdb("tt0903747", 1, 1)

    assert ep == TVmazeEpisode(season=1, episode=1, name="Pilot", air_date="2008-01-20")


def test_get_episode_by_imdb_returns_none_when_episode_not_found():
    client = TVmazeClient(enabled=True)
    lookup_resp = _json_response({"id": 169})
    episodes_resp = _json_response([{"season": 1, "number": 1, "name": "Pilot", "airdate": "2008-01-20"}])
    with patch("app.core.tvmaze_client.requests.get", side_effect=[lookup_resp, episodes_resp]):
        ep = client.get_episode_by_imdb("tt0903747", 9, 9)

    assert ep is None


def test_get_episode_by_imdb_returns_none_when_show_not_found():
    client = TVmazeClient(enabled=True)
    with patch("app.core.tvmaze_client.requests.get", return_value=MagicMock(status_code=404)):
        assert client.get_episode_by_imdb("tt9999999", 1, 1) is None


def test_requests_swallows_errors():
    client = TVmazeClient(enabled=True)
    with patch("app.core.tvmaze_client.requests.get", side_effect=requests.RequestException("down")):
        assert client.lookup_show_id_by_imdb("tt0903747") is None
        assert client.get_show_info(169) is None
        assert client.get_episodes(169) == []
