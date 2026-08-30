"""Fallback TMDB scraper used when no API key is configured.

Scrapes themoviedb.org's public search/detail pages. Best-effort: page
markup can change, so callers should treat empty results as "unknown"
rather than "doesn't exist."
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.themoviedb.org"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
]


@dataclass
class ScrapedResult:
    tmdb_id: int | None
    title: str
    year: int | None = None
    overview: str = ""
    poster_path: str | None = None
    extra: dict = field(default_factory=dict)


class TMDBScraper:
    def __init__(self, *, rate_limit_seconds: float = 2.0, max_retries: int = 3, session: requests.Session | None = None):
        self.rate_limit_seconds = rate_limit_seconds
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._last_request_ts = 0.0

    def _headers(self) -> dict:
        return {"User-Agent": random.choice(_USER_AGENTS)}

    def _get(self, url: str, params: dict | None = None) -> requests.Response | None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, headers=self._headers(), timeout=10)
                self._last_request_ts = time.monotonic()
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("TMDB scrape request failed (attempt %d/%d): %s", attempt + 1, self.max_retries, exc)
                time.sleep(min(2**attempt, 8))
        logger.error("TMDB scrape request exhausted retries: %s", last_exc)
        return None

    def search_movie(self, title: str, year: int | None = None) -> list[ScrapedResult]:
        resp = self._get(f"{BASE_URL}/search/movie", params={"query": title, "year": year})
        if resp is None:
            return []
        return self._parse_search_results(resp.text, "movie")

    def search_tv(self, title: str) -> list[ScrapedResult]:
        resp = self._get(f"{BASE_URL}/search/tv", params={"query": title})
        if resp is None:
            return []
        return self._parse_search_results(resp.text, "tv")

    def _parse_search_results(self, html: str, media_type: str) -> list[ScrapedResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[ScrapedResult] = []
        for card in soup.select("div.card"):
            link = card.select_one("a.result")
            if not link or not link.get("href"):
                continue
            href = link["href"]
            tmdb_id = self._extract_id(href)
            title_el = card.select_one("h2") or card.select_one(".title")
            year_el = card.select_one(".release_date") or card.select_one("span.release_date")
            results.append(
                ScrapedResult(
                    tmdb_id=tmdb_id,
                    title=title_el.get_text(strip=True) if title_el else "",
                    year=self._extract_year(year_el.get_text(strip=True)) if year_el else None,
                )
            )
        return results

    def get_movie_details(self, tmdb_id: int) -> ScrapedResult | None:
        resp = self._get(f"{BASE_URL}/movie/{tmdb_id}")
        if resp is None:
            return None
        return self._parse_detail(resp.text, tmdb_id)

    def get_tv_details(self, tmdb_id: int) -> ScrapedResult | None:
        resp = self._get(f"{BASE_URL}/tv/{tmdb_id}")
        if resp is None:
            return None
        return self._parse_detail(resp.text, tmdb_id)

    def find_by_imdb_id(self, imdb_id: str, media_type: str) -> ScrapedResult | None:
        """Best-effort manual-match fallback: themoviedb.org redirects
        /movie/{imdb_id} or /tv/{imdb_id} to the real .../{tmdb_id}-{slug}
        page when the IMDb id matches a known title. `resp.history` is only
        populated when a redirect actually happened, which is what
        distinguishes a real match from themoviedb.org just echoing the
        literal (unmatched) imdb_id back -- that string still contains
        digits after the "tt" prefix, so digit-extraction alone would give
        a false positive on a 404/no-match response.
        """
        resp = self._get(f"{BASE_URL}/{media_type}/{imdb_id}")
        if resp is None or not resp.history:
            return None
        tmdb_id = self._extract_id(str(resp.url))
        if tmdb_id is None:
            return None
        return self._parse_detail(resp.text, tmdb_id)

    def get_collection_movies(self, collection_id: int) -> list[ScrapedResult]:
        resp = self._get(f"{BASE_URL}/collection/{collection_id}")
        if resp is None:
            return []
        return self._parse_search_results(resp.text, "movie")

    def _parse_detail(self, html: str, tmdb_id: int) -> ScrapedResult:
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.select_one("h2 a") or soup.select_one("h2")
        overview_el = soup.select_one("div.overview p")
        poster_el = soup.select_one("div.poster img")
        return ScrapedResult(
            tmdb_id=tmdb_id,
            title=title_el.get_text(strip=True) if title_el else "",
            overview=overview_el.get_text(strip=True) if overview_el else "",
            poster_path=poster_el.get("src") if poster_el else None,
        )

    @staticmethod
    def _extract_id(href: str) -> int | None:
        parts = [p for p in href.split("/") if p]
        for part in parts:
            digits = "".join(ch for ch in part if ch.isdigit())
            if digits:
                return int(digits)
        return None

    @staticmethod
    def _extract_year(text: str) -> int | None:
        digits = "".join(ch if ch.isdigit() else " " for ch in text).split()
        for token in digits:
            if len(token) == 4:
                return int(token)
        return None
