from __future__ import annotations

from unittest.mock import MagicMock

import tmdbv3api
from tmdbv3api.as_obj import AsObj

from app.core.tmdb_client import (
    MediaResult,
    TMDBClient,
    compute_absolute_episode,
    genres_for,
    parse_filename,
    season_episode_counts,
    vote_average_for,
)
from app.core.tmdb_scraper import ScrapedResult


def _empty_search_response() -> AsObj:
    """A real tmdbv3api AsObj built from a genuine zero-results TMDB search
    payload -- reproduces the library's actual (buggy) __iter__ behavior
    rather than a mock standing in for it."""
    return AsObj({"page": 1, "results": [], "total_pages": 1, "total_results": 0}, key="results")


def _search_response(movies: list[dict]) -> AsObj:
    return AsObj({"page": 1, "results": movies, "total_pages": 1, "total_results": len(movies)}, key="results")


def test_genres_for_uses_named_genres_from_details_response():
    media = MediaResult(tmdb_id=1, title="X", media_type="movie", raw={"genres": [{"id": 18, "name": "Drama"}]})
    assert genres_for(media) == ["Drama"]


def test_genres_for_maps_genre_ids_from_search_response():
    media = MediaResult(tmdb_id=1, title="X", media_type="movie", raw={"genre_ids": [28, 12]})
    assert genres_for(media) == ["Action", "Adventure"]


def test_genres_for_uses_tv_genre_map_for_tv_media_type():
    media = MediaResult(tmdb_id=1, title="X", media_type="tv", raw={"genre_ids": [10759]})
    assert genres_for(media) == ["Action & Adventure"]


def test_genres_for_empty_when_no_raw_data():
    media = MediaResult(tmdb_id=1, title="X", media_type="movie", raw={})
    assert genres_for(media) == []


def test_genres_for_ignores_unknown_genre_ids():
    media = MediaResult(tmdb_id=1, title="X", media_type="movie", raw={"genre_ids": [999999]})
    assert genres_for(media) == []


def test_vote_average_for_returns_value():
    media = MediaResult(tmdb_id=1, title="X", media_type="movie", raw={"vote_average": 8.4})
    assert vote_average_for(media) == 8.4


def test_vote_average_for_none_when_zero_or_missing():
    assert vote_average_for(MediaResult(tmdb_id=1, title="X", media_type="movie", raw={"vote_average": 0})) is None
    assert vote_average_for(MediaResult(tmdb_id=1, title="X", media_type="movie", raw={})) is None


def test_season_episode_counts_excludes_nothing_itself():
    media = MediaResult(
        tmdb_id=1, title="Show", media_type="tv",
        raw={"seasons": [
            {"season_number": 0, "episode_count": 3},
            {"season_number": 1, "episode_count": 12},
            {"season_number": 2, "episode_count": 10},
        ]},
    )
    assert season_episode_counts(media) == {0: 3, 1: 12, 2: 10}


def test_season_episode_counts_empty_when_no_seasons_data():
    media = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={})
    assert season_episode_counts(media) == {}


def test_compute_absolute_episode_sums_prior_seasons():
    media = MediaResult(
        tmdb_id=1, title="Show", media_type="tv",
        raw={"seasons": [
            {"season_number": 0, "episode_count": 3},
            {"season_number": 1, "episode_count": 12},
            {"season_number": 2, "episode_count": 10},
        ]},
    )
    assert compute_absolute_episode(media, season=1, episode=1) == 1
    assert compute_absolute_episode(media, season=2, episode=3) == 15  # 12 (S1) + 3
    # season 0 (specials) never counted toward the total
    assert compute_absolute_episode(media, season=2, episode=1) == 13


def test_compute_absolute_episode_none_when_season_unknown():
    media = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={"seasons": [{"season_number": 1, "episode_count": 12}]})
    assert compute_absolute_episode(media, season=5, episode=1) is None


def test_compute_absolute_episode_none_when_no_season_data():
    media = MediaResult(tmdb_id=1, title="Show", media_type="tv", raw={})
    assert compute_absolute_episode(media, season=1, episode=1) is None


def test_parse_filename_movie_detects_cd_part_marker():
    parsed = parse_filename("Some.Movie.2019.CD1.mkv")
    assert parsed.media_type == "movie"
    assert parsed.title == "Some Movie"
    assert parsed.year == 2019
    assert parsed.part == "Cd1"


def test_parse_filename_movie_detects_part_marker_variants():
    assert parse_filename("Movie.2020.Part2.mkv").part == "Part2"
    assert parse_filename("Movie.2020.Disc1.mkv").part == "Disc1"
    assert parse_filename("Movie.2020.disk2.mkv").part == "Disk2"


def test_parse_filename_movie_no_part_marker_by_default():
    assert parse_filename("Some Movie (2019) 1080p BluRay.mkv").part is None


def test_parse_filename_tv_detects_part_marker():
    parsed = parse_filename("The.Show.S02E05.CD1.mkv")
    assert parsed.media_type == "tv"
    assert parsed.title == "The Show"
    assert parsed.season == 2
    assert parsed.episode == 5
    assert parsed.part == "Cd1"


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


def test_refresh_movie_details_bypasses_and_updates_cache():
    fake_scraper = MagicMock()
    fake_scraper.get_movie_details.side_effect = [
        ScrapedResult(tmdb_id=1, title="Old Title", year=2019),
        ScrapedResult(tmdb_id=1, title="New Title", year=2019),
    ]
    client = TMDBClient(api_key="", scraper=fake_scraper)

    first = client.get_movie_details(1)
    assert first.title == "Old Title"

    refreshed = client.refresh_movie_details(1)
    assert refreshed.title == "New Title"
    assert fake_scraper.get_movie_details.call_count == 2

    # cache now holds the refreshed result, not the original
    again = client.get_movie_details(1)
    assert again.title == "New Title"
    assert fake_scraper.get_movie_details.call_count == 2


def test_refresh_tv_details_bypasses_and_updates_cache():
    fake_scraper = MagicMock()
    fake_scraper.get_tv_details.side_effect = [
        ScrapedResult(tmdb_id=9, title="Old Show", year=2019),
        ScrapedResult(tmdb_id=9, title="New Show", year=2019),
    ]
    client = TMDBClient(api_key="", scraper=fake_scraper)

    first = client.get_tv_details(9)
    assert first.title == "Old Show"

    refreshed = client.refresh_tv_details(9)
    assert refreshed.title == "New Show"
    assert fake_scraper.get_tv_details.call_count == 2


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


def test_get_trailer_key_returns_none_without_api_key():
    fake_scraper = MagicMock()
    client = TMDBClient(api_key="", scraper=fake_scraper)
    assert client.get_trailer_key(278, "movie") is None


def test_get_trailer_key_prefers_official_youtube_trailer(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "results": [
                    {"site": "YouTube", "type": "Trailer", "official": False, "key": "unofficial-key"},
                    {"site": "YouTube", "type": "Teaser", "official": True, "key": "teaser-key"},
                    {"site": "YouTube", "type": "Trailer", "official": True, "key": "official-key"},
                    {"site": "Vimeo", "type": "Trailer", "official": True, "key": "vimeo-key"},
                ]
            }

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    assert client.get_trailer_key(278, "movie") == "official-key"


def test_get_trailer_key_falls_back_to_any_trailer_when_none_official(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"site": "YouTube", "type": "Trailer", "official": False, "key": "only-key"}]}

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    assert client.get_trailer_key(278, "movie") == "only-key"


def test_get_trailer_key_returns_none_when_no_trailers(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    assert client.get_trailer_key(278, "movie") is None


def test_get_trailer_key_returns_none_on_api_error(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())
    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get_trailer_key(278, "movie") is None


def test_get_cast_returns_empty_without_api_key():
    client = TMDBClient(api_key="", scraper=MagicMock())
    assert client.get_cast(278, "movie") == []


def test_get_cast_sorts_by_billing_order_and_respects_limit(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "cast": [
                    {"name": "Second Billed", "character": "B", "profile_path": "/b.jpg", "order": 1},
                    {"name": "Top Billed", "character": "A", "profile_path": "/a.jpg", "order": 0},
                    {"name": "Third Billed", "character": "C", "profile_path": None, "order": 2},
                ]
            }

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    cast = client.get_cast(278, "movie", limit=2)
    assert [c["name"] for c in cast] == ["Top Billed", "Second Billed"]


def test_get_cast_returns_empty_on_api_error(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())
    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get_cast(278, "movie") == []


def test_get_season_episodes_returns_empty_without_api_key():
    client = TMDBClient(api_key="", scraper=MagicMock())
    assert client.get_season_episodes(1399, 1) == []


def test_get_season_episodes_parses_episode_list(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "episodes": [
                    {"episode_number": 1, "name": "Pilot", "air_date": "2020-01-01", "overview": "First."},
                    {"episode_number": 2, "name": "Second", "air_date": "2020-01-08", "overview": "Next."},
                ]
            }

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    episodes = client.get_season_episodes(1399, 1)
    assert [e["name"] for e in episodes] == ["Pilot", "Second"]
    assert episodes[0]["air_date"] == "2020-01-01"


def test_get_season_episodes_returns_empty_on_api_error(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())
    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get_season_episodes(1399, 1) == []


def test_get_similar_titles_returns_empty_without_api_key():
    client = TMDBClient(api_key="", scraper=MagicMock())
    assert client.get_similar_titles(278, "movie") == []


def test_get_similar_titles_parses_movie_results(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": 42, "title": "Similar Movie", "release_date": "2019-05-01", "poster_path": "/p.jpg"}]}

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    similar = client.get_similar_titles(278, "movie")
    assert len(similar) == 1
    assert similar[0].tmdb_id == 42
    assert similar[0].title == "Similar Movie"
    assert similar[0].year == 2019
    assert similar[0].poster_path == "/p.jpg"


def test_get_similar_titles_parses_tv_results(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": 7, "name": "Similar Show", "first_air_date": "2021-01-01"}]}

    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: FakeResponse())
    similar = client.get_similar_titles(7, "tv")
    assert similar[0].title == "Similar Show"
    assert similar[0].year == 2021


def test_get_similar_titles_returns_empty_on_api_error(monkeypatch):
    client = TMDBClient(api_key="fake-key", scraper=MagicMock())
    monkeypatch.setattr("app.core.tmdb_client.requests.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get_similar_titles(278, "movie") == []


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
