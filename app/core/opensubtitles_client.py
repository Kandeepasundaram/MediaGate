"""OpenSubtitles REST API v1 client: searches and downloads a subtitle by
TMDB id. Optional -- like OMDbClient, "no API key" just means the feature
is off (see SubtitlesConfig.opensubtitles_api_key / .auto_fetch_missing_subtitles).

Two-step API, both requiring the same Api-Key header: /subtitles finds
candidate file_ids for a title+language, /download exchanges one of those
for a one-time signed URL that actually serves the file content.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.opensubtitles.com/api/v1"
_TIMEOUT_SECONDS = 15

# Best-effort: OpenSubtitles wants an ISO 639-1 code; this app's own
# keep_languages config accepts looser tags ("eng", "english") for the
# purge filter, so normalize the common ones here rather than assuming
# every configured tag is already a 2-letter code.
_LANGUAGE_ALIASES = {"eng": "en", "english": "en"}


def _normalize_language(tag: str) -> str:
    tag = tag.strip().lower()
    return _LANGUAGE_ALIASES.get(tag, tag[:2])


@dataclass
class SubtitleMatch:
    file_id: int
    language: str
    release: str = ""


class OpenSubtitlesClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"Api-Key": self.api_key, "User-Agent": "MediaManager/1.0"}

    def find_subtitle(
        self, tmdb_id: int, language: str, media_type: str, season: int | None = None, episode: int | None = None
    ) -> SubtitleMatch | None:
        """Best (highest-rated) result for a title+language. TV lookups pass
        season/episode as well -- OpenSubtitles indexes episodes separately
        from their parent show."""
        if not self.enabled:
            return None
        params: dict = {"tmdb_id": tmdb_id, "languages": _normalize_language(language)}
        if media_type == "tv":
            params["type"] = "episode"
            if season is not None:
                params["season_number"] = season
            if episode is not None:
                params["episode_number"] = episode
        else:
            params["type"] = "movie"

        try:
            resp = requests.get(f"{_BASE_URL}/subtitles", params=params, headers=self._headers(), timeout=_TIMEOUT_SECONDS)
            resp.raise_for_status()
            results = resp.json().get("data", [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("OpenSubtitles search failed for tmdb_id=%s: %s", tmdb_id, exc)
            return None

        if not results:
            return None
        best = results[0]
        files = (best.get("attributes") or {}).get("files") or []
        if not files:
            return None
        return SubtitleMatch(
            file_id=files[0]["file_id"],
            language=(best.get("attributes") or {}).get("language", language),
            release=(best.get("attributes") or {}).get("release", ""),
        )

    def download_subtitle(self, file_id: int) -> bytes | None:
        """Two requests: exchange file_id for a signed link, then fetch the
        actual subtitle text from that link."""
        if not self.enabled:
            return None
        try:
            resp = requests.post(
                f"{_BASE_URL}/download", json={"file_id": file_id}, headers=self._headers(), timeout=_TIMEOUT_SECONDS
            )
            resp.raise_for_status()
            link = resp.json().get("link")
            if not link:
                return None
            content_resp = requests.get(link, timeout=_TIMEOUT_SECONDS)
            content_resp.raise_for_status()
            return content_resp.content
        except (requests.RequestException, ValueError) as exc:
            logger.warning("OpenSubtitles download failed for file_id=%s: %s", file_id, exc)
            return None
