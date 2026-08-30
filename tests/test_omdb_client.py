from __future__ import annotations

from unittest.mock import MagicMock

import requests

from app.core.omdb_client import OMDbClient


def _fake_response(json_data: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def test_disabled_without_api_key():
    client = OMDbClient(api_key="")
    assert client.enabled is False
    assert client.get_ratings("tt0111161") is None


def test_get_ratings_parses_imdb_and_rotten_tomatoes(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    monkeypatch.setattr(
        "app.core.omdb_client.requests.get",
        lambda *a, **kw: _fake_response(
            {
                "Response": "True",
                "imdbRating": "9.3",
                "imdbVotes": "2,900,000",
                "Metascore": "80",
                "Ratings": [
                    {"Source": "Internet Movie Database", "Value": "9.3/10"},
                    {"Source": "Rotten Tomatoes", "Value": "91%"},
                ],
            }
        ),
    )

    result = client.get_ratings("tt0111161")
    assert result.imdb_rating == 9.3
    assert result.imdb_votes == "2,900,000"
    assert result.rotten_tomatoes == "91%"
    assert result.metacritic == "80"


def test_get_ratings_returns_none_when_omdb_has_no_record(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    monkeypatch.setattr(
        "app.core.omdb_client.requests.get",
        lambda *a, **kw: _fake_response({"Response": "False", "Error": "Movie not found!"}),
    )

    assert client.get_ratings("tt0000000") is None


def test_get_ratings_handles_missing_rotten_tomatoes_entry(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    monkeypatch.setattr(
        "app.core.omdb_client.requests.get",
        lambda *a, **kw: _fake_response({"Response": "True", "imdbRating": "7.1", "Ratings": []}),
    )

    result = client.get_ratings("tt0111161")
    assert result.imdb_rating == 7.1
    assert result.rotten_tomatoes is None


def test_get_ratings_returns_none_on_request_failure(monkeypatch):
    client = OMDbClient(api_key="fake-key")

    def exploding_get(*a, **kw):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr("app.core.omdb_client.requests.get", exploding_get)
    assert client.get_ratings("tt0111161") is None
