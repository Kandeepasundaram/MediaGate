"""Best-effort "rescan your library" ping to Plex/Jellyfin after archiving
or organizing files -- neither app watches this container's archive folders
itself, so without this a newly-archived title wouldn't show up in Plex or
Jellyfin until their own next scheduled scan.
"""
from __future__ import annotations

import logging

import requests

from app.config_loader import AppConfig
from app.database import Database

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


def _notify_plex(url: str, token: str) -> None:
    try:
        requests.get(f"{url.rstrip('/')}/library/sections/all/refresh", params={"X-Plex-Token": token}, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Plex library refresh failed: %s", exc)


def _notify_jellyfin(url: str, api_key: str) -> None:
    try:
        requests.post(
            f"{url.rstrip('/')}/Library/Refresh", headers={"X-Emby-Token": api_key}, timeout=_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        logger.warning("Jellyfin library refresh failed: %s", exc)


def notify_media_servers(config: AppConfig) -> None:
    """Pings whichever of Plex/Jellyfin has both a URL and credential
    configured -- either, both, or neither, all valid setups."""
    if config.media_server.plex_url and config.media_server.plex_token:
        _notify_plex(config.media_server.plex_url, config.media_server.plex_token)
    if config.media_server.jellyfin_url and config.media_server.jellyfin_api_key:
        _notify_jellyfin(config.media_server.jellyfin_url, config.media_server.jellyfin_api_key)


def _plex_watched_imdb_ids(url: str, token: str) -> set[str]:
    """IMDb ids of every movie Plex reports a nonzero viewCount for.
    Plex identifies external ids via a per-item Guid list (e.g.
    'imdb://tt1234567') rather than a flat field, and only returns it (and
    JSON at all) when asked for explicitly."""
    watched: set[str] = set()
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/library/all",
            params={"type": 1, "X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        for item in resp.json().get("MediaContainer", {}).get("Metadata", []):
            if not item.get("viewCount"):
                continue
            for guid in item.get("Guid", []):
                gid = guid.get("id", "")
                if gid.startswith("imdb://"):
                    watched.add(gid[len("imdb://"):])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Plex watched-status fetch failed: %s", exc)
    return watched


def _jellyfin_watched_imdb_ids(url: str, api_key: str) -> set[str]:
    """IMDb ids of every movie Jellyfin's first user has marked played.
    Jellyfin's played state is per-user (unlike Plex's per-token view), and
    the API has no "any user" query -- best-effort picks whichever account
    /Users returns first, which is the only account on most single-user
    homelab installs anyway."""
    watched: set[str] = set()
    headers = {"X-Emby-Token": api_key}
    try:
        users_resp = requests.get(f"{url.rstrip('/')}/Users", headers=headers, timeout=_TIMEOUT_SECONDS)
        users_resp.raise_for_status()
        users = users_resp.json()
        if not users:
            return watched
        items_resp = requests.get(
            f"{url.rstrip('/')}/Items",
            headers=headers,
            params={
                "userId": users[0]["Id"],
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "Fields": "ProviderIds",
                "Filters": "IsPlayed",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        items_resp.raise_for_status()
        for item in items_resp.json().get("Items", []):
            imdb_id = (item.get("ProviderIds") or {}).get("Imdb")
            if imdb_id:
                watched.add(imdb_id)
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        logger.warning("Jellyfin watched-status fetch failed: %s", exc)
    return watched


def sync_watched_from_media_servers(config: AppConfig, db: Database) -> int:
    """Pulls "watched" status back from Plex/Jellyfin into media_items --
    movies only, matched by imdb_id (already stored on the row for
    ratings). TV isn't attempted: matching an *episode* would need a
    per-episode provider id neither server reliably exposes, unlike a
    movie's own top-level Guid/ProviderIds.

    One-directional -- only ever flips watched False -> True, never the
    reverse -- so a manual "mark unwatched" in this app's own UI is never
    silently undone by a stale or incomplete media-server read.
    """
    watched_imdb_ids: set[str] = set()
    if config.media_server.plex_url and config.media_server.plex_token:
        watched_imdb_ids |= _plex_watched_imdb_ids(config.media_server.plex_url, config.media_server.plex_token)
    if config.media_server.jellyfin_url and config.media_server.jellyfin_api_key:
        watched_imdb_ids |= _jellyfin_watched_imdb_ids(config.media_server.jellyfin_url, config.media_server.jellyfin_api_key)

    if not watched_imdb_ids:
        return 0

    updated = 0
    for row in db.list_media_items(media_type="movie"):
        if row["watched"] or not row["imdb_id"]:
            continue
        if row["imdb_id"] in watched_imdb_ids:
            db.update_media_item(row["id"], watched=1)
            updated += 1
    return updated
