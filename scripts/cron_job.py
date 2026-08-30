#!/usr/bin/env python3
"""Daily tracker check. Runs `check_for_updates`, then pushes toast notifications
for any newly-pending items to the configured Windows agent.

Run from Ubuntu via cron/systemd-timer, e.g.:
    0 6 * * * /path/to/.venv/bin/python /path/to/scripts/cron_job.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from app.config_loader import load_config
from app.core.tmdb_client import TMDBClient
from app.core.tracker import check_for_updates
from app.database import Database

logger = logging.getLogger("cron_job")


def notify_windows_agent(url: str, title: str, body: str) -> None:
    if not url:
        logger.info("No windows_agent_url configured; skipping toast for '%s'", title)
        return
    try:
        requests.post(url, json={"title": title, "body": body}, timeout=5)
    except requests.RequestException as exc:
        logger.warning("Failed to reach Windows notification agent: %s", exc)


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

    pending_count = check_for_updates(db, tmdb)
    logger.info("Tracker check complete: %d item(s) pending notification", pending_count)

    for row in db.list_unsent_notifications():
        if row["media_type"] == "tv":
            body = f"Season {row['latest_known_season']} of {row['title']} is out!"
        else:
            body = row["movie_release_status"] or f"New release for {row['title']}"
        notify_windows_agent(config.tracker.windows_agent_url, "New Media Available", body)
        db.mark_notification_sent(row["id"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
