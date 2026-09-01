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
# Multi-part rip markers -- an old-format disc rip split across files gets
# tagged like "Movie.Name.2020.CD1.mkv" / "...CD2.mkv", or occasionally
# "Part1"/"Disc1"/"Disk1". Stripped out before title/year extraction so it
# never leaks into the parsed title, and reported separately (ParsedFilename.part)
# so the renamer can give each part a distinct filename instead of colliding.
_PART_PATTERN = re.compile(r"[.\s_-](cd|part|disc|disk)[.\s_-]?(\d{1,2})\b", re.IGNORECASE)


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


_MOVIE_GENRE_NAMES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Science Fiction", 10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
}
_TV_GENRE_NAMES = {
    10759: "Action & Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 10762: "Kids", 9648: "Mystery",
    10763: "News", 10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Soap",
    10767: "Talk", 10768: "War & Politics", 37: "Western",
}


def genres_for(media: "MediaResult") -> list[str]:
    """Genre names for a match, straight from whatever TMDB API response is
    already sitting in media.raw -- no extra network call. The details
    endpoint (get_movie_details/get_tv_details) returns full {id, name}
    objects; the search endpoint only returns genre_ids, mapped here via
    TMDB's own (effectively static) genre id list. Empty in scraper mode,
    where raw is {}."""
    named = media.raw.get("genres")
    if named:
        return [g["name"] for g in named if isinstance(g, dict) and g.get("name")]
    genre_map = _TV_GENRE_NAMES if media.media_type == "tv" else _MOVIE_GENRE_NAMES
    return [genre_map[gid] for gid in media.raw.get("genre_ids", []) if gid in genre_map]


def vote_average_for(media: "MediaResult") -> float | None:
    """TMDB's 0-10 average user rating, straight from media.raw. None in
    scraper mode or if TMDB hasn't got enough votes to publish one yet."""
    value = media.raw.get("vote_average")
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def season_episode_counts(media: "MediaResult") -> dict[int, int]:
    """season_number -> episode_count from a TV details MediaResult's raw
    payload (needs the full get_tv_details response, not a search result --
    TMDB's search endpoint doesn't include a per-season breakdown). Handles
    both tmdbv3api's AsObj items (API mode) and plain dicts, same dual
    getattr/dict-access reason as _latest_season_episode_count below."""
    counts: dict[int, int] = {}
    for s in media.raw.get("seasons") or []:
        season_number = getattr(s, "season_number", None) if not isinstance(s, dict) else s.get("season_number")
        episode_count = getattr(s, "episode_count", None) if not isinstance(s, dict) else s.get("episode_count")
        if season_number is not None:
            counts[season_number] = episode_count or 0
    return counts


def compute_absolute_episode(media: "MediaResult", season: int, episode: int) -> int | None:
    """Cumulative episode number across all prior seasons plus the
    in-season episode number -- the numbering anime naming conventions
    typically use instead of SxxExx. None (not "just use the in-season
    number") when there isn't enough season data to compute it correctly,
    so a caller can fall back explicitly rather than silently mislabeling
    an episode with a wrong absolute number. Season 0 (TMDB's "Specials"
    bucket) is never counted toward the total, matching every other
    season-0 exclusion in this app (tv-status, the missing-episodes gap
    detector)."""
    counts = season_episode_counts(media)
    if season not in counts:
        return None
    prior = sum(count for s, count in counts.items() if 0 < s < season)
    return prior + episode


@dataclass
class ParsedFilename:
    title: str
    media_type: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    part: str | None = None  # "CD1", "Part2", "Disc1", ... -- see _PART_PATTERN


def parse_filename(filename: str) -> ParsedFilename:
    """Best-effort extraction of title/year (movie) or title/season/episode (TV)."""
    stem = re.sub(r"\.\w{2,4}$", "", filename)
    normalized = stem.replace("_", ".").replace(" ", ".")

    part = None
    part_match = _PART_PATTERN.search(normalized)
    if part_match:
        part = f"{part_match.group(1).capitalize()}{part_match.group(2)}"
        normalized = normalized[: part_match.start()] + normalized[part_match.end():]

    tv_match = _TV_PATTERN.match(normalized)
    if tv_match:
        title = tv_match.group("title").replace(".", " ").strip(" -")
        title = _FILENAME_JUNK.sub("", title).strip()
        return ParsedFilename(
            title=title,
            media_type="tv",
            season=int(tv_match.group("season")),
            episode=int(tv_match.group("episode")),
            part=part,
        )

    movie_match = _MOVIE_YEAR_PATTERN.match(normalized)
    if movie_match:
        title = movie_match.group("title").replace(".", " ").strip(" -")
        title = _FILENAME_JUNK.sub("", title).strip()
        return ParsedFilename(title=title, media_type="movie", year=int(movie_match.group("year")), part=part)

    title = _FILENAME_JUNK.sub("", normalized.replace(".", " ")).strip()
    return ParsedFilename(title=title or stem, media_type="movie", part=part)


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

    def _tmdb_get(self, path: str, params: dict | None = None) -> dict | None:
        """GET https://api.themoviedb.org/3/{path} with api_key already
        applied, a fixed 10s timeout, and the same warn-and-return-None
        error handling every direct API call in this class needs --
        shared so the retry/timeout policy lives in one place instead of
        five (external_ids, videos, credits, similar, find)."""
        try:
            resp = requests.get(
                f"https://api.themoviedb.org/3/{path}",
                params={"api_key": self.api_key, **(params or {})},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("TMDB API %s failed: %s", path, exc)
            return None

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

    def refresh_movie_details(self, tmdb_id: int) -> MediaResult | None:
        """Bypasses the memoized cache and re-populates it -- for the
        library's bulk "Refresh Metadata" action, where the whole point is
        picking up a title/poster/rating change on TMDB's side since this
        process last looked, which get_movie_details' cache would otherwise
        hide for the rest of this process's lifetime."""
        result = self._get_movie_details(tmdb_id)
        self._cache[("movie_details", tmdb_id)] = result
        return result

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

    def refresh_tv_details(self, tmdb_id: int) -> MediaResult | None:
        """TV counterpart of refresh_movie_details -- see there for why this
        bypasses (and then repopulates) the cache instead of just calling
        get_tv_details again."""
        result = self._get_tv_details(tmdb_id)
        self._cache[("tv_details", tmdb_id)] = result
        return result

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
                result.raw["number_of_episodes"] = getattr(t, "number_of_episodes", None)
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
            data = self._tmdb_get(f"find/{imdb_id}", {"external_source": "imdb_id", "language": self.language})
            if data is not None:
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
            data = self._tmdb_get(f"{media_type}/{tmdb_id}/external_ids")
            if data is not None:
                return data.get("imdb_id") or None

        return self.scraper.get_imdb_id(tmdb_id, media_type)

    def get_trailer_key(self, tmdb_id: int, media_type: str) -> str | None:
        """YouTube video key for the best available trailer, for a
        "Watch Trailer" link in the detail pane. API-key-only: no scraper
        fallback exists yet (themoviedb.org's own trailer widget isn't
        scraped), so this is simply unavailable without a TMDB API key --
        same "no key means no feature" pattern as OMDb ratings."""
        return self._cached(("trailer", tmdb_id, media_type), lambda: self._get_trailer_key(tmdb_id, media_type))

    def _get_trailer_key(self, tmdb_id: int, media_type: str) -> str | None:
        if not self._api:
            return None
        data = self._tmdb_get(f"{media_type}/{tmdb_id}/videos")
        if data is None:
            return None
        videos = data.get("results", [])

        trailers = [v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
        if not trailers:
            return None
        official = next((v for v in trailers if v.get("official")), None)
        return (official or trailers[0]).get("key")

    def get_cast(self, tmdb_id: int, media_type: str, limit: int = 8) -> list[dict]:
        """Top-billed cast (name/character/headshot) for the detail pane.
        API-key-only, same as get_trailer_key -- no scraper fallback."""
        return self._cached(("cast", tmdb_id, media_type), lambda: self._get_cast(tmdb_id, media_type))[:limit]

    def _get_cast(self, tmdb_id: int, media_type: str) -> list[dict]:
        if not self._api:
            return []
        data = self._tmdb_get(f"{media_type}/{tmdb_id}/credits")
        if data is None:
            return []
        cast = data.get("cast", [])
        return [
            {"id": c.get("id"), "name": c.get("name"), "character": c.get("character"), "profile_path": c.get("profile_path")}
            for c in sorted(cast, key=lambda c: c.get("order", 999))
        ]

    def get_person_credits(self, person_id: int, limit: int = 16) -> list[dict]:
        """A cast member's combined (movie + TV) filmography, for the detail
        pane's cast-card click-through ("what else has this actor been
        in"). API-key-only, same as get_cast/get_trailer_key -- no scraper
        fallback (there's no per-person page to scrape). Ranked by TMDB
        popularity rather than release date, sorted desc, so the most
        recognizable titles surface first regardless of library size."""
        return self._cached(("person_credits", person_id), lambda: self._get_person_credits(person_id))[:limit]

    def _get_person_credits(self, person_id: int) -> list[dict]:
        if not self._api:
            return []
        data = self._tmdb_get(f"person/{person_id}/combined_credits")
        if data is None:
            return []
        credits = data.get("cast", [])
        out = []
        for c in credits:
            media_type = c.get("media_type")
            if media_type not in ("movie", "tv"):
                continue
            date = c.get("release_date") if media_type == "movie" else c.get("first_air_date")
            out.append(
                {
                    "tmdb_id": c.get("id"),
                    "media_type": media_type,
                    "title": c.get("title") or c.get("name") or "",
                    "year": int(date[:4]) if date else None,
                    "poster_path": c.get("poster_path"),
                    "popularity": c.get("popularity") or 0,
                }
            )
        out.sort(key=lambda c: c["popularity"], reverse=True)
        return out

    def get_similar_titles(self, tmdb_id: int, media_type: str, limit: int = 8) -> list[MediaResult]:
        """Other titles TMDB considers similar, for detail-pane discovery.
        API-key-only, same as get_trailer_key -- no scraper fallback."""
        return self._cached(("similar", tmdb_id, media_type), lambda: self._get_similar_titles(tmdb_id, media_type))[:limit]

    def _get_similar_titles(self, tmdb_id: int, media_type: str) -> list[MediaResult]:
        if not self._api:
            return []
        data = self._tmdb_get(f"{media_type}/{tmdb_id}/similar")
        if data is None:
            return []
        results = data.get("results", [])

        titles = []
        for r in results:
            date = r.get("release_date") if media_type == "movie" else r.get("first_air_date")
            titles.append(
                MediaResult(
                    tmdb_id=r.get("id"),
                    title=r.get("title") or r.get("name") or "",
                    media_type=media_type,
                    year=int(date[:4]) if date else None,
                    poster_path=r.get("poster_path"),
                    source="api",
                )
            )
        return titles

    def get_season_episodes(self, tmdb_id: int, season_number: int) -> list[dict]:
        """Episode name/air_date/overview for one season, via TMDB's
        /tv/{id}/season/{n} endpoint. API-key-only, same as get_cast/
        get_trailer_key -- no scraper fallback (season-detail pages aren't
        scraped). The /tv-season route falls back to TVmazeClient.get_episodes
        when this comes back empty, same fallback role TVmaze plays in
        /tv-status."""
        return self._cached(
            ("season_episodes", tmdb_id, season_number),
            lambda: self._get_season_episodes(tmdb_id, season_number),
        )

    def _get_season_episodes(self, tmdb_id: int, season_number: int) -> list[dict]:
        if not self._api:
            return []
        data = self._tmdb_get(f"tv/{tmdb_id}/season/{season_number}")
        if data is None:
            return []
        return [
            {
                "episode_number": e.get("episode_number"),
                "name": e.get("name"),
                "air_date": e.get("air_date"),
                "overview": e.get("overview"),
            }
            for e in data.get("episodes", [])
            if e.get("episode_number") is not None
        ]

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
