"""Unified TMDB client: uses the official API when a key is configured,
falls back to TMDBScraper otherwise, and falls back again if the API call
fails at runtime. Both paths return the same MediaResult shape.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import requests

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


def _iter_api_results(results) -> list:
    """tmdbv3api 1.9.0's AsObj.__iter__ has a bug: when the wrapped list is
    genuinely empty, `self._obj_list` (itself an AsObj) is falsy, so it
    falls back to `iter(self._dict())` -- which yields the *response's own
    top-level JSON key names* ("page", "results", "total_pages", ...) as
    plain strings instead of an empty sequence. A real result item is
    always an AsObj with an `id` attribute; a plain str never is, so this
    filters the bug's stray keys out rather than crashing on `.id`/`["id"]`
    a few lines later. Confirmed against tmdbv3api's as_obj.py at the
    pinned version -- revisit if that dependency is ever upgraded.
    """
    return [r for r in results if hasattr(r, "id")]


def _latest_season_episode_count(t, number_of_seasons: int | None) -> int | None:
    """Episode count for the show's newest season, read off the TV Details
    response's `seasons` list -- best-effort since tmdbv3api wraps nested
    JSON in its own AsObj type rather than plain dicts."""
    if not number_of_seasons:
        return None
    seasons = getattr(t, "seasons", None)
    if not seasons:
        return None
    try:
        for s in seasons:
            if getattr(s, "season_number", None) == number_of_seasons:
                return getattr(s, "episode_count", None)
    except TypeError:
        return None
    return None


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
                    for m in _iter_api_results(results)
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
                    for t in _iter_api_results(results)
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
                number_of_seasons = getattr(t, "number_of_seasons", None)
                result.raw["number_of_seasons"] = number_of_seasons
                result.raw["status"] = getattr(t, "status", None)
                result.raw["latest_season_episode_count"] = _latest_season_episode_count(t, number_of_seasons)
                return result
            except Exception as exc:
                logger.warning("TMDB API get_tv_details failed, falling back to scraper: %s", exc)

        return _scraped_to_result(self.scraper.get_tv_details(tmdb_id), "tv")

    def find_by_imdb_id(self, imdb_id: str, media_type: str) -> MediaResult | None:
        """Manual-match path for a title the automatic search/backfill
        couldn't identify (or matched wrong): looks up TMDB's canonical
        record directly by IMDb id instead of a fuzzy title search."""
        return self._cached(("find_imdb", imdb_id, media_type), lambda: self._find_by_imdb_id(imdb_id, media_type))

    def _find_by_imdb_id(self, imdb_id: str, media_type: str) -> MediaResult | None:
        if self._api:
            try:
                resp = requests.get(
                    f"https://api.themoviedb.org/3/find/{imdb_id}",
                    params={"api_key": self.api_key, "external_source": "imdb_id", "language": self.language},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("movie_results" if media_type == "movie" else "tv_results", [])
                if results:
                    r = results[0]
                    return MediaResult(
                        tmdb_id=r["id"],
                        title=r.get("title") or r.get("name") or "",
                        media_type=media_type,
                        year=self._year_from_date(r.get("release_date") or r.get("first_air_date")),
                        overview=r.get("overview", ""),
                        poster_path=r.get("poster_path"),
                        source="api",
                        raw=r,
                    )
                return None
            except Exception as exc:
                logger.warning("TMDB API find_by_imdb_id failed, falling back to scraper: %s", exc)

        return _scraped_to_result(self.scraper.find_by_imdb_id(imdb_id, media_type), media_type)

    @staticmethod
    def _year_from_date(date_str: str | None) -> int | None:
        return int(date_str[:4]) if date_str else None

    def get_external_imdb_id(self, tmdb_id: int, media_type: str) -> str | None:
        """Resolves a TMDB id to its IMDb id -- needed for OMDb ratings
        lookups, since TMDB search results don't carry one directly."""
        return self._cached(("imdb_id", tmdb_id, media_type), lambda: self._get_external_imdb_id(tmdb_id, media_type))

    def _get_external_imdb_id(self, tmdb_id: int, media_type: str) -> str | None:
        if self._api:
            try:
                resp = requests.get(
                    f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids",
                    params={"api_key": self.api_key},
                    timeout=10,
                )
                resp.raise_for_status()
                return resp.json().get("imdb_id") or None
            except Exception as exc:
                logger.warning("TMDB API external_ids failed, falling back to scraper: %s", exc)

        return self.scraper.get_imdb_id(tmdb_id, media_type)

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
                    for m in _iter_api_results(c.parts)
                ]
            except Exception as exc:
                logger.warning("TMDB API get_collection_movies failed, falling back to scraper: %s", exc)

        scraped = self.scraper.get_collection_movies(collection_id)
        return [r for r in (_scraped_to_result(s, "movie") for s in scraped) if r]
