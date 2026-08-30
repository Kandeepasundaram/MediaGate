from __future__ import annotations

from unittest.mock import MagicMock

import tmdbv3api
from tmdbv3api.as_obj import AsObj

from app.core.tmdb_client import TMDBClient, parse_filename
from app.core.tmdb_scraper import ScrapedResult


def _empty_search_response() -> AsObj:
    """A real tmdbv3api AsObj built from a genuine zero-results TMDB search
    payload -- reproduces the library's actual (buggy) __iter__ behavior
    rather than a mock standing in for it."""
    return AsObj({"page": 1, "results": [], "total_pages": 1, "total_results": 0}, key="results")


def _search_response(movies: list[dict]) -> AsObj:
    return AsObj({"page": 1, "results": movies, "total_pages": 1, "total_results": len(movies)}, key="results")


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


def test_get_external_imdb_id_uses_scraper_when_no_api_key():
    fake_scraper = MagicMock()
    fake_scraper.get_imdb_id.return_value = "tt0111161"

    client = TMDBClient(api_key="", scraper=fake_scraper)
    assert client.get_external_imdb_id(278, "movie") == "tt0111161"
    fake_scraper.get_imdb_id.assert_called_once_with(278, "movie")


def test_get_external_imdb_id_uses_api_when_key_set(monkeypatch):
    fake_scraper = MagicMock()
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"imdb_id": "tt0111161"}

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())

    assert client.get_external_imdb_id(278, "movie") == "tt0111161"
    fake_scraper.get_imdb_id.assert_not_called()


def test_get_external_imdb_id_api_falls_back_to_scraper_on_error(monkeypatch):
    fake_scraper = MagicMock()
    fake_scraper.get_imdb_id.return_value = "tt0111161"
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))

    assert client.get_external_imdb_id(278, "movie") == "tt0111161"


def test_search_movie_handles_zero_results_from_real_api_without_crashing(monkeypatch):
    """Regression test for tmdbv3api 1.9.0's AsObj.__iter__ bug: a genuine
    zero-results search response, when iterated, yields the response's own
    JSON key names ("page", "results", ...) as plain strings instead of
    nothing -- which used to blow up with 'str' object has no attribute
    'id' instead of just returning an empty list."""
    fake_scraper = MagicMock()
    fake_scraper.search_movie.return_value = []
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeMovie:
        def search(self, title):
            return _empty_search_response()

    monkeypatch.setattr(tmdbv3api, "Movie", FakeMovie)

    assert client.search_movie("Some Obscure Title Nobody Made") == []


def test_search_tv_handles_zero_results_from_real_api_without_crashing(monkeypatch):
    fake_scraper = MagicMock()
    fake_scraper.search_tv.return_value = []
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeTV:
        def search(self, title):
            return _empty_search_response()

    monkeypatch.setattr(tmdbv3api, "TV", FakeTV)

    assert client.search_tv("Some Obscure Show Nobody Made") == []


def test_search_movie_still_parses_real_nonempty_api_results(monkeypatch):
    """Same real-AsObj construction as the empty-results test, but with an
    actual match -- confirms the empty-results fix didn't break the normal
    (non-buggy) iteration path."""
    fake_scraper = MagicMock()
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeMovie:
        def search(self, title):
            return _search_response(
                [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "overview": "...", "poster_path": "/p.jpg"}]
            )

    monkeypatch.setattr(tmdbv3api, "Movie", FakeMovie)

    results = client.search_movie("The Matrix")
    assert len(results) == 1
    assert results[0].tmdb_id == 603
    assert results[0].title == "The Matrix"
    assert results[0].year == 1999
    fake_scraper.search_movie.assert_not_called()


def test_get_tv_details_extracts_latest_season_episode_count(monkeypatch):
    """Backs the detail pane's "new season available" banner: the newest
    season's episode_count, read off TV Details' real (AsObj-wrapped)
    `seasons` list rather than a mock standing in for it."""
    fake_scraper = MagicMock()
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeTV:
        def details(self, tmdb_id):
            return AsObj({
                "id": 1399, "name": "Test Show", "first_air_date": "2011-04-17",
                "overview": "A show.", "poster_path": "/p.jpg",
                "number_of_seasons": 2, "number_of_episodes": 18, "status": "Ended",
                "seasons": [
                    {"season_number": 1, "episode_count": 10},
                    {"season_number": 2, "episode_count": 8},
                ],
            })

    monkeypatch.setattr(tmdbv3api, "TV", FakeTV)

    result = client.get_tv_details(1399)
    assert result.tmdb_id == 1399
    assert result.raw["number_of_seasons"] == 2
    assert result.raw["number_of_episodes"] == 18
    assert result.raw["status"] == "Ended"
    assert result.raw["latest_season_episode_count"] == 8


def test_get_tv_details_handles_missing_seasons_list(monkeypatch):
    fake_scraper = MagicMock()
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeTV:
        def details(self, tmdb_id):
            return AsObj({
                "id": 1399, "name": "Test Show", "first_air_date": "2011-04-17",
                "overview": "", "poster_path": None,
                "number_of_seasons": 1, "status": "Returning Series",
            })

    monkeypatch.setattr(tmdbv3api, "TV", FakeTV)

    result = client.get_tv_details(1399)
    assert result.raw["latest_season_episode_count"] is None


def test_get_tv_details_scraper_mode_has_empty_raw():
    fake_scraper = MagicMock()
    fake_scraper.get_tv_details.return_value = ScrapedResult(tmdb_id=1399, title="Test Show", year=2011)
    client = TMDBClient(api_key="", scraper=fake_scraper)

    result = client.get_tv_details(1399)
    assert result.source == "scraper"
    assert result.raw == {}


def test_get_collection_movies_handles_empty_parts_without_crashing(monkeypatch):
    fake_scraper = MagicMock()
    fake_scraper.get_collection_movies.return_value = []
    client = TMDBClient(api_key="fake-key", scraper=fake_scraper)

    class FakeCollection:
        def details(self, collection_id):
            return AsObj({"id": 1, "name": "Empty Collection", "parts": []}, key="parts")

    monkeypatch.setattr(tmdbv3api, "Collection", FakeCollection)

    assert client.get_collection_movies(1) == []


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
