"""TVmaze API client: free, keyless episode-guide data (air dates, episode
titles, show status/network, next-episode-to-air) resolved via a show's
IMDb id. Optional -- like OMDbClient/OpenSubtitlesClient, `enabled=False`
just means every method returns None (see TVmazeConfig.enabled).

This is what epguides.com's own site actually runs on: its per-show CSV
export ("list as .csv") proxies straight to
`../common/exportToCSVmaze.asp?maze={tvmaze_id}`, and its HTML pages credit
episode corrections to TVmaze.com. Hitting TVmaze's own JSON API directly
avoids epguides' inconsistent show-URL slugs and needs no HTML parsing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tvmaze.com"
_TIMEOUT_SECONDS = 15


@dataclass
class TVmazeEpisode:
    season: int
    episode: int
    name: str | None
    air_date: str | None  # ISO "YYYY-MM-DD"


@dataclass
class TVmazeShowInfo:
    tvmaze_id: int
    status: str | None  # "Running", "Ended", "To Be Determined", ...
    network: str | None
    next_episode_air_date: str | None
    next_episode_code: str | None  # "S05E03"


class TVmazeClient:
    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self._cache: dict[tuple, object] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _cached(self, key: tuple, fn):
        if key in self._cache:
            return self._cache[key]
        result = fn()
        self._cache[key] = result
        return result

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            resp = requests.get(f"{_BASE_URL}{path}", params=params, timeout=_TIMEOUT_SECONDS)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("TVmaze request failed for %s: %s", path, exc)
            return None

    def lookup_show_id_by_imdb(self, imdb_id: str) -> int | None:
        if not self.enabled:
            return None
        return self._cached(("lookup", imdb_id), lambda: self._lookup_show_id_by_imdb(imdb_id))

    def _lookup_show_id_by_imdb(self, imdb_id: str) -> int | None:
        data = self._get("/lookup/shows", params={"imdb": imdb_id})
        return data.get("id") if data else None

    def get_show_info(self, tvmaze_id: int) -> TVmazeShowInfo | None:
        if not self.enabled:
            return None
        return self._cached(("show_info", tvmaze_id), lambda: self._get_show_info(tvmaze_id))

    def _get_show_info(self, tvmaze_id: int) -> TVmazeShowInfo | None:
        data = self._get(f"/shows/{tvmaze_id}", params={"embed": "nextepisode"})
        if data is None:
            return None
        network = (data.get("network") or data.get("webChannel") or {}).get("name")
        next_ep = (data.get("_embedded") or {}).get("nextepisode") or {}
        next_air_date = next_ep.get("airdate") or None
        next_season = next_ep.get("season")
        next_number = next_ep.get("number")
        next_code = (
            f"S{next_season:02d}E{next_number:02d}"
            if next_season is not None and next_number is not None
            else None
        )
        return TVmazeShowInfo(
            tvmaze_id=tvmaze_id,
            status=data.get("status"),
            network=network,
            next_episode_air_date=next_air_date,
            next_episode_code=next_code,
        )

    def get_episodes(self, tvmaze_id: int) -> list[TVmazeEpisode]:
        if not self.enabled:
            return []
        return self._cached(("episodes", tvmaze_id), lambda: self._get_episodes(tvmaze_id))

    def _get_episodes(self, tvmaze_id: int) -> list[TVmazeEpisode]:
        data = self._get(f"/shows/{tvmaze_id}/episodes")
        if not data:
            return []
        episodes = []
        for e in data:
            season = e.get("season")
            number = e.get("number")
            if season is None or number is None:
                continue
            episodes.append(
                TVmazeEpisode(season=season, episode=number, name=e.get("name"), air_date=e.get("airdate") or None)
            )
        return episodes

    def get_episode_by_imdb(self, imdb_id: str, season: int, episode: int) -> TVmazeEpisode | None:
        if not self.enabled:
            return None
        tvmaze_id = self.lookup_show_id_by_imdb(imdb_id)
        if tvmaze_id is None:
            return None
        for ep in self.get_episodes(tvmaze_id):
            if ep.season == season and ep.episode == episode:
                return ep
        return None

    def get_show_info_by_imdb(self, imdb_id: str) -> TVmazeShowInfo | None:
        if not self.enabled:
            return None
        tvmaze_id = self.lookup_show_id_by_imdb(imdb_id)
        if tvmaze_id is None:
            return None
        return self.get_show_info(tvmaze_id)
