from __future__ import annotations

from unittest.mock import MagicMock

import tmdbv3api

from app.core.tmdb_client import TMDBClient, parse_filename
from app.core.tmdb_scraper import ScrapedResult


def test_parse_filename_tv_show():
    parsed = parse_filename("The.Show.S02E05.1080p.WEB-DL.x264.mkv")
    assert parsed.media_type == "tv"
    assert parsed.title == "The Show"
    assert parsed.season == 2
    assert parsed.episode == 5


def test_parse_filename_movie_with_year():
    parsed = parse_filename("Some Movie (2019) 1080p BluRay.mkv")
    assert parsed.media_type == "movie"
    assert parsed.title == "Some Movie"
    assert parsed.year == 2019


def test_parse_filename_movie_no_year_falls_back_to_title_only():
    parsed = parse_filename("random_video_file.mp4")
    assert parsed.media_type == "movie"
    assert parsed.year is None
    assert "random" in parsed.title.lower()


def test_client_uses_scraper_when_no_api_key():
    fake_scraper = MagicMock()
    fake_scraper.search_movie.return_value = [ScrapedResult(tmdb_id=42, title="Movie", year=2020)]

    client = TMDBClient(api_key="", scraper=fake_scraper)
    assert client.mode == "scraper"

    results = client.search_movie("Movie", 2020)
    assert len(results) == 1
    assert results[0].tmdb_id == 42
    assert results[0].source == "scraper"
    fake_scraper.search_movie.assert_called_once_with("Movie", 2020)


def test_client_caches_results():
    fake_scraper = MagicMock()
    fake_scraper.search_movie.return_value = [ScrapedResult(tmdb_id=1, title="X")]

    client = TMDBClient(api_key="", scraper=fake_scraper)
    client.search_movie("X", None)
    client.search_movie("X", None)

    fake_scraper.search_movie.assert_called_once()


def test_find_by_imdb_id_uses_scraper_when_no_api_key():
    fake_scraper = MagicMock()
    fake_scraper.find_by_imdb_id.return_value = ScrapedResult(tmdb_id=278, title="The Shawshank Redemption", year=1994)

    client = TMDBClient(api_key="", scraper=fake_scraper)
    result = client.find_by_imdb_id("tt0111161", "movie")

    assert result.tmdb_id == 278
    assert result.source == "scraper"
    fake_scraper.find_by_imdb_id.assert_called_once_with("tt0111161", "movie")


def test_find_by_imdb_id_returns_none_when_scraper_finds_nothing():
    fake_scraper = MagicMock()
    fake_scraper.find_by_imdb_id.return_value = None

    client = TMDBClient(api_key="", scraper=fake_scraper)
    assert client.find_by_imdb_id("tt9999999", "movie") is None


def test_find_by_imdb_id_uses_api_when_key_set(monkeypatch):
    fake_scraper = MagicMock()
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"movie_results": [{"id": 278, "title": "The Shawshank Redemption", "release_date": "1994-09-23", "overview": "...", "poster_path": "/p.jpg"}], "tv_results": []}

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())

    result = client.find_by_imdb_id("tt0111161", "movie")
    assert result.tmdb_id == 278
    assert result.year == 1994
    assert result.source == "api"
    fake_scraper.find_by_imdb_id.assert_not_called()


def test_find_by_imdb_id_api_falls_back_to_scraper_on_error(monkeypatch):
    fake_scraper = MagicMock()
    fake_scraper.find_by_imdb_id.return_value = ScrapedResult(tmdb_id=42, title="Fallback")
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    def exploding_get(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.core.tmdb_client.requests.get", exploding_get)

    result = client.find_by_imdb_id("tt0111161", "movie")
    assert result.tmdb_id == 42
    assert result.source == "scraper"


def test_client_falls_back_to_scraper_on_api_failure(monkeypatch):
    fake_scraper = MagicMock()
    fake_scraper.search_movie.return_value = [ScrapedResult(tmdb_id=7, title="Y")]

    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)
    assert client.mode == "api"

    class ExplodingMovie:
        def search(self, title):
            raise RuntimeError("simulated TMDB API failure")

    monkeypatch.setattr(tmdbv3api, "Movie", ExplodingMovie)

    results = client.search_movie("Y")
    assert results[0].tmdb_id == 7
    assert results[0].source == "scraper"
