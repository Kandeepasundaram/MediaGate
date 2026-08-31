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


def test_get_full_details_disabled_without_api_key():
    client = OMDbClient(api_key="")
    assert client.get_full_details("tt9663764") is None


def test_get_full_details_parses_full_record(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    monkeypatch.setattr(
        "app.core.omdb_client.requests.get",
        lambda *a, **kw: _fake_response(
            {
                "Response": "True",
                "Title": "Aquaman and the Lost Kingdom",
                "Year": "2023",
                "imdbID": "tt9663764",
                "Plot": "Black Manta seeks revenge on Aquaman.",
                "Genre": "Action, Adventure, Fantasy",
                "Director": "James Wan",
                "Writer": "David Leslie Johnson-McGoldrick (screenplay by), James Wan, Jason Momoa",
                "Actors": "Jason Momoa, Patrick Wilson, Yahya Abdul-Mateen II",
                "Runtime": "124 min",
                "imdbRating": "5.9",
                "Poster": "https://example.com/poster.jpg",
                "Released": "22 Dec 2023",
            }
        ),
    )

    result = client.get_full_details("tt9663764")
    assert result.title == "Aquaman and the Lost Kingdom"
    assert result.imdb_id == "tt9663764"
    assert result.genres == ["Action", "Adventure", "Fantasy"]
    assert result.director == ["James Wan"]
    # writer annotations like "(screenplay by)" are left in the raw list --
    # media_note.py's build_movie_note strips them, not the client itself
    assert result.writer == ["David Leslie Johnson-McGoldrick (screenplay by)", "James Wan", "Jason Momoa"]
    assert result.actors == ["Jason Momoa", "Patrick Wilson", "Yahya Abdul-Mateen II"]
    assert result.runtime == "124 min"
    assert result.imdb_rating == 5.9
    assert result.poster_url == "https://example.com/poster.jpg"
    assert result.released == "22 Dec 2023"


def test_get_full_details_requests_full_plot(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    captured = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return _fake_response({"Response": "True", "Title": "X", "imdbID": "tt1"})

    monkeypatch.setattr("app.core.omdb_client.requests.get", fake_get)
    client.get_full_details("tt1")
    assert captured["params"]["plot"] == "full"


def test_get_full_details_missing_fields_become_na_placeholder(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    monkeypatch.setattr(
        "app.core.omdb_client.requests.get",
        lambda *a, **kw: _fake_response({"Response": "True", "Title": "X", "imdbID": "tt1", "Genre": "N/A"}),
    )

    result = client.get_full_details("tt1")
    assert result.genres == ["N/A"]
    assert result.director == ["N/A"]
    assert result.runtime == "N/A"
    assert result.imdb_rating is None


def test_get_full_details_returns_none_when_no_record(monkeypatch):
    client = OMDbClient(api_key="fake-key")
    monkeypatch.setattr(
        "app.core.omdb_client.requests.get",
        lambda *a, **kw: _fake_response({"Response": "False", "Error": "Movie not found!"}),
    )
    assert client.get_full_details("tt0000000") is None


def test_get_full_details_returns_none_on_request_failure(monkeypatch):
    client = OMDbClient(api_key="fake-key")

    def exploding_get(*a, **kw):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr("app.core.omdb_client.requests.get", exploding_get)
    assert client.get_full_details("tt0111161") is None
