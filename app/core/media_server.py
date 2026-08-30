"""Best-effort "rescan your library" ping to Plex/Jellyfin after archiving
or organizing files -- neither app watches this container's archive folders
itself, so without this a newly-archived title wouldn't show up in Plex or
Jellyfin until their own next scheduled scan.
"""
from __future__ import annotations

import logging

import requests

from app.config_loader import AppConfig

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
