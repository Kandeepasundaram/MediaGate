"""Unified TMDB client: uses the official API when a key is configured,
falls back to TMDBScraper otherwise, and falls back again if the API call
fails at runtime. Both paths return the same MediaResult shape.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.tmdb_scraper import ScrapedResult, TMDBScraper

logger = logging.getLogger(__name__)

_FILENAME_JUNK = re.compile(
    r"\b(1080p|720p|2160p|4k|x264|x265|h264|h265|hevc|web[-.]?dl|webrip|bluray|brrip|hdrip|dvdrip|"
    r"amzn|nf|hulu|aac|ac3|dts|remux|proper|repack|extended|unrated)\b",
    re.IGNORECASE,
)
_TV_PATTERN = re.compile(r"^(?P<title>.+?)[.\s_-]+[Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,3})")
_MOVIE_YEAR_PATTERN = re.compile(r"^(?P<title>.+?)[.\s_(\[]+(?P<year>19\d{2}|20\d{2})\b")


@dataclass
class MediaResult:
    tmdb_id: int | None
    title: str
    media_type: str
    year: int | None = None
    overview: str = ""
    poster_path: str | None = None
    source: str = "api"
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedFilename:
    title: str
    media_type: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None


def parse_filename(filename: str) -> ParsedFilename:
    """Best-effort extraction of title/year (movie) or title/season/episode (TV)."""
    stem = re.sub(r"\.\w{2,4}$", "", filename)
    normalized = stem.replace("_", ".").replace(" ", ".")

    tv_match = _TV_PATTERN.match(normalized)
    if tv_match:
        title = tv_match.group("title").replace(".", " ").strip(" -")
        title = _FILENAME_JUNK.sub("", title).strip()
        return ParsedFilename(
            title=title,
            media_type="tv",
            season=int(tv_match.group("season")),
            episode=int(tv_match.group("episode")),
        )

    movie_match = _MOVIE_YEAR_PATTERN.match(normalized)
    if movie_match:
        title = movie_match.group("title").replace(".", " ").strip(" -")
        title = _FILENAME_JUNK.sub("", title).strip()
        return ParsedFilename(title=title, media_type="movie", year=int(movie_match.group("year")))

    title = _FILENAME_JUNK.sub("", stem.replace(".", " ")).strip()
    return ParsedFilename(title=title or stem, media_type="movie")


def _scraped_to_result(r: ScrapedResult | None, media_type: str) -> MediaResult | None:
    if r is None:
        return None
    return MediaResult(
        tmdb_id=r.tmdb_id,
        title=r.title,
        media_type=media_type,
        year=r.year,
        overview=r.overview,
        poster_path=r.poster_path,
        source="scraper",
    )


class TMDBClient:
    """Auto-selects API or scraper backend; falls back to scraper on API errors."""

    def __init__(self, api_key: str = "", language: str = "en-US", *, scraper: TMDBScraper | None = None):
        self.api_key = api_key
        self.language = language
        self.scraper = scraper or TMDBScraper()
        self._cache: dict[tuple, Any] = {}
        self._api = None
        if api_key:
            try:
                from tmdbv3api import TMDb

                self._api = TMDb()
                self._api.api_key = api_key
                self._api.language = language
                logger.info("TMDBClient using API mode")
            except ImportError:
                logger.warning("tmdbv3api not installed; falling back to scraper mode")
                self._api = None
        else:
            logger.info("TMDBClient using scraper mode (no API key configured)")

    @property
    def mode(self) -> str:
        return "api" if self._api else "scraper"

    def _cached(self, key: tuple, fn):
        if key in self._cache:
            return self._cache[key]
        result = fn()
        self._cache[key] = result
        return result

    def search_movie(self, title: str, year: int | None = None) -> list[MediaResult]:
        return self._cached(("search_movie", title, year), lambda: self._search_movie(title, year))

    def _search_movie(self, title: str, year: int | None) -> list[MediaResult]:
        if self._api:
            try:
                from tmdbv3api import Movie

                results = Movie().search(title)
                out = [
                    MediaResult(
                        tmdb_id=m.id,
                        title=m.title,
                        media_type="movie",
                        year=int(m.release_date[:4]) if getattr(m, "release_date", None) else None,
                        overview=getattr(m, "overview", ""),
                        poster_path=getattr(m, "poster_path", None),
                        source="api",
                        raw=dict(m),
                    )
                    for m in results
                ]
                if year:
                    out = [r for r in out if r.year in (None, year)] or out
                return out
            except Exception as exc:
                logger.warning("TMDB API search_movie failed, falling back to scraper: %s", exc)

        scraped = self.scraper.search_movie(title, year)
        return [r for r in (_scraped_to_result(s, "movie") for s in scraped) if r]

    def search_tv(self, title: str) -> list[MediaResult]:
        return self._cached(("search_tv", title), lambda: self._search_tv(title))

    def _search_tv(self, title: str) -> list[MediaResult]:
        if self._api:
            try:
                from tmdbv3api import TV

                results = TV().search(title)
                return [
                    MediaResult(
                        tmdb_id=t.id,
                        title=t.name,
                        media_type="tv",
                        year=int(t.first_air_date[:4]) if getattr(t, "first_air_date", None) else None,
                        overview=getattr(t, "overview", ""),
                        poster_path=getattr(t, "poster_path", None),
                        source="api",
                        raw=dict(t),
                    )
                    for t in results
                ]
            except Exception as exc:
                logger.warning("TMDB API search_tv failed, falling back to scraper: %s", exc)

        scraped = self.scraper.search_tv(title)
        return [r for r in (_scraped_to_result(s, "tv") for s in scraped) if r]

    def get_movie_details(self, tmdb_id: int) -> MediaResult | None:
        return self._cached(("movie_details", tmdb_id), lambda: self._get_movie_details(tmdb_id))

    def _get_movie_details(self, tmdb_id: int) -> MediaResult | None:
        if self._api:
            try:
                from tmdbv3api import Movie

                m = Movie().details(tmdb_id)
                return MediaResult(
                    tmdb_id=m.id,
                    title=m.title,
                    media_type="movie",
                    year=int(m.release_date[:4]) if getattr(m, "release_date", None) else None,
                    overview=getattr(m, "overview", ""),
                    poster_path=getattr(m, "poster_path", None),
                    source="api",
                    raw=dict(m),
                )
            except Exception as exc:
                logger.warning("TMDB API get_movie_details failed, falling back to scraper: %s", exc)

        return _scraped_to_result(self.scraper.get_movie_details(tmdb_id), "movie")

    def get_tv_details(self, tmdb_id: int) -> MediaResult | None:
        return self._cached(("tv_details", tmdb_id), lambda: self._get_tv_details(tmdb_id))

    def _get_tv_details(self, tmdb_id: int) -> MediaResult | None:
        if self._api:
            try:
                from tmdbv3api import TV

                t = TV().details(tmdb_id)
                result = MediaResult(
                    tmdb_id=t.id,
                    title=t.name,
                    media_type="tv",
                    year=int(t.first_air_date[:4]) if getattr(t, "first_air_date", None) else None,
                    overview=getattr(t, "overview", ""),
                    poster_path=getattr(t, "poster_path", None),
                    source="api",
                    raw=dict(t),
                )
                result.raw["number_of_seasons"] = getattr(t, "number_of_seasons", None)
                return result
            except Exception as exc:
                logger.warning("TMDB API get_tv_details failed, falling back to scraper: %s", exc)

        return _scraped_to_result(self.scraper.get_tv_details(tmdb_id), "tv")

    def get_collection_movies(self, collection_id: int) -> list[MediaResult]:
        return self._cached(("collection", collection_id), lambda: self._get_collection_movies(collection_id))

    def _get_collection_movies(self, collection_id: int) -> list[MediaResult]:
        if self._api:
            try:
                from tmdbv3api import Collection

                c = Collection().details(collection_id)
                return [
                    MediaResult(
                        tmdb_id=m["id"],
                        title=m["title"],
                        media_type="movie",
                        year=int(m["release_date"][:4]) if m.get("release_date") else None,
                        poster_path=m.get("poster_path"),
                        source="api",
                        raw=m,
                    )
                    for m in c.parts
                ]
            except Exception as exc:
                logger.warning("TMDB API get_collection_movies failed, falling back to scraper: %s", exc)

        scraped = self.scraper.get_collection_movies(collection_id)
        return [r for r in (_scraped_to_result(s, "movie") for s in scraped) if r]
