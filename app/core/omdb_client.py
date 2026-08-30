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
