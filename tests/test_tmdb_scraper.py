from __future__ import annotations

from unittest.mock import MagicMock

import requests

from app.core.tmdb_scraper import TMDBScraper


def _fake_response(url: str, text: str = "", history: list | None = None):
    resp = MagicMock()
    resp.url = url
    resp.text = text
    resp.history = history if history is not None else []
    resp.raise_for_status = MagicMock()
    return resp


def test_find_by_imdb_id_follows_redirect_to_real_page():
    scraper = TMDBScraper(rate_limit_seconds=0)
    html = '<h2><a>The Shawshank Redemption</a></h2><div class="overview"><p>Two imprisoned men...</p></div>'
    resp = _fake_response(
        "https://www.themoviedb.org/movie/278-the-shawshank-redemption",
        text=html,
        history=[_fake_response("https://www.themoviedb.org/movie/tt0111161")],
    )
    scraper.session.get = MagicMock(return_value=resp)

    result = scraper.find_by_imdb_id("tt0111161", "movie")

    assert result is not None
    assert result.tmdb_id == 278
    assert result.title == "The Shawshank Redemption"


def test_find_by_imdb_id_returns_none_when_no_redirect_happened():
    """No redirect means themoviedb.org didn't recognize the imdb id --
    digits inside "tt0111161" itself must not be mistaken for a tmdb id."""
    scraper = TMDBScraper(rate_limit_seconds=0)
    resp = _fake_response("https://www.themoviedb.org/movie/tt0111161", history=[])
    scraper.session.get = MagicMock(return_value=resp)

    assert scraper.find_by_imdb_id("tt0111161", "movie") is None


def test_find_by_imdb_id_returns_none_on_request_failure():
    scraper = TMDBScraper(rate_limit_seconds=0, max_retries=1)
    scraper.session.get = MagicMock(side_effect=requests.ConnectionError("boom"))

    assert scraper.find_by_imdb_id("tt0111161", "movie") is None
