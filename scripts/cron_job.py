#!/usr/bin/env python3
"""Daily tracker check. Runs `check_for_updates` against TMDB and flags any
newly-available seasons/sequels as pending. The dashboard (app/static/app.js)
polls /api/tracker/notifications and raises a browser Notification for
anything not yet seen — there is no separate OS-level notification agent.

Run via cron/systemd-timer, e.g.:
    0 6 * * * /path/to/.venv/bin/python /path/to/scripts/cron_job.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config_loader import load_config
from app.core.tmdb_client import TMDBClient
from app.core.tracker import check_for_updates
from app.database import Database

logger = logging.getLogger("cron_job")


def main() -> int:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        filename=str(config.logging.file),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(config.database_path)
    db.init_db()
    tmdb = TMDBClient(api_key=config.tmdb.api_key, language=config.tmdb.language)

    pending_count = check_for_updates(db, tmdb, config.notifications.webhook_url or None)
    logger.info("Tracker check complete: %d item(s) pending notification", pending_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
