"""OMDb API client: aggregates IMDb rating and Rotten Tomatoes score by
IMDb id. Optional -- ratings are simply unavailable without an OMDb API
key (free tier at omdbapi.com), the same "no key means no feature"
pattern already used for TMDB's own optional API key.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

OMDB_URL = "https://www.omdbapi.com/"


@dataclass
class RatingsResult:
    imdb_rating: float | None = None
    imdb_votes: str | None = None
    rotten_tomatoes: str | None = None
    metacritic: str | None = None


@dataclass
class OMDbFullResult:
    """Everything media_note.py needs to build a movie markdown note --
    OMDb's full `i=<imdb_id>` response, not just the ratings subset
    get_ratings() parses. "N/A" (OMDb's own placeholder for a field it
    doesn't have) is passed through as-is rather than turned into None,
    matching the Obsidian Media DB plugin's own convention of writing it
    out literally (see e.g. this app's example note's `studio: [N/A]`)."""
    title: str
    year: str
    imdb_id: str
    plot: str
    genres: list[str]
    director: list[str]
    writer: list[str]
    actors: list[str]
    runtime: str
    imdb_rating: float | None
    poster_url: str
    released: str  # OMDb's raw "22 Dec 2023" format, or "N/A"


class OMDbClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def get_ratings(self, imdb_id: str) -> RatingsResult | None:
        if not self.enabled or not imdb_id:
            return None
        try:
            resp = requests.get(OMDB_URL, params={"i": imdb_id, "apikey": self.api_key}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("OMDb lookup failed for %s: %s", imdb_id, exc)
            return None

        if data.get("Response") == "False":
            logger.info("OMDb has no record for %s: %s", imdb_id, data.get("Error"))
            return None

        rotten_tomatoes = next(
            (r["Value"] for r in data.get("Ratings", []) if r.get("Source") == "Rotten Tomatoes"), None
        )
        imdb_rating = data.get("imdbRating")
        return RatingsResult(
            imdb_rating=float(imdb_rating) if imdb_rating and imdb_rating != "N/A" else None,
            imdb_votes=data.get("imdbVotes") if data.get("imdbVotes") != "N/A" else None,
            rotten_tomatoes=rotten_tomatoes,
            metacritic=data.get("Metascore") if data.get("Metascore") != "N/A" else None,
        )

    def get_full_details(self, imdb_id: str) -> OMDbFullResult | None:
        """Full OMDb record for the movie-note generator (media_note.py) --
        director/writer/actors/plot/runtime/poster that get_ratings()
        doesn't parse. plot=full asks OMDb for the untruncated plot text."""
        if not self.enabled or not imdb_id:
            return None
        try:
            resp = requests.get(
                OMDB_URL, params={"i": imdb_id, "plot": "full", "apikey": self.api_key}, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("OMDb full-details lookup failed for %s: %s", imdb_id, exc)
            return None

        if data.get("Response") == "False":
            logger.info("OMDb has no record for %s: %s", imdb_id, data.get("Error"))
            return None

        def _split(field: str) -> list[str]:
            value = data.get(field)
            if not value or value == "N/A":
                return ["N/A"]
            return [part.strip() for part in value.split(",") if part.strip()]

        imdb_rating = data.get("imdbRating")
        return OMDbFullResult(
            title=data.get("Title", ""),
            year=data.get("Year", ""),
            imdb_id=data.get("imdbID", imdb_id),
            plot=data.get("Plot") or "",
            genres=_split("Genre"),
            director=_split("Director"),
            writer=_split("Writer"),
            actors=_split("Actors"),
            runtime=data.get("Runtime") or "N/A",
            imdb_rating=float(imdb_rating) if imdb_rating and imdb_rating != "N/A" else None,
            poster_url=data.get("Poster") or "",
            released=data.get("Released") or "N/A",
        )
