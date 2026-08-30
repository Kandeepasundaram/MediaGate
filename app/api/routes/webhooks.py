"""Inbound webhook receiver for Radarr/Sonarr's "On Import"/"On Download"
notifications -- triggers an immediate adopt scan for the matching media
type instead of waiting for the next Movies/TV gallery load (which is what
otherwise picks up a Radarr/Sonarr-imported file, per library_adopt.py).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.config_loader import AppConfig
from app.core.library_adopt import adopt_new_files
from app.database import Database
from app.dependencies import get_config, get_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/arr")
def arr_webhook(payload: dict, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)) -> dict:
    """Accepts whatever Radarr/Sonarr's webhook payload looks like without
    validating its shape closely -- the two apps' payloads differ and have
    changed across versions, but both distinguish themselves by including a
    "movie" or "series" key, which is all that's needed here to know which
    adopt scan to run. An unrecognized payload (including Radarr/Sonarr's
    own "Test" button) just runs both -- adopt_new_files() is a harmless
    no-op when there's nothing new to find, so there's no wrong answer here
    worth rejecting the request over.
    """
    if "series" in payload and "movie" not in payload:
        adopted = {"tv": adopt_new_files(db, config, "tv")}
    elif "movie" in payload and "series" not in payload:
        adopted = {"movie": adopt_new_files(db, config, "movie")}
    else:
        adopted = {"movie": adopt_new_files(db, config, "movie"), "tv": adopt_new_files(db, config, "tv")}

    logger.info("Arr webhook triggered adopt scan: %s", adopted)
    return {"adopted": adopted}
