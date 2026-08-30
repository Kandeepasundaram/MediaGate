"""Registers files already sitting in the archive folders into media_items,
without copying or moving them -- "stage 1" of the manual library browser:
make an existing, already-organized library (e.g. one Radarr/Sonarr already
manages) visible in the Movies/TV galleries immediately. TMDB metadata for
newly-adopted rows is filled in afterward by the background backfill in
app/core/metadata_backfill.py, not here -- this does no network calls, so
it's cheap enough to run on every gallery load.
"""
from __future__ import annotations

from app.config_loader import AppConfig
from app.core.scanner import scan_directory
from app.database import Database


def adopt_new_files(db: Database, config: AppConfig, media_type: str) -> int:
    root = config.paths.archive_movies if media_type == "movie" else config.paths.archive_tv
    adopted = 0
    for f in scan_directory(root):
        path_str = str(f.path)
        if db.get_media_item_by_final_path(path_str) is not None:
            continue
        db.create_media_item(
            original_path=path_str,
            final_path=path_str,
            title=f.parsed.title,
            year=f.parsed.year,
            media_type=media_type,
            season_number=f.parsed.season,
            episode_number=f.parsed.episode,
            metadata={},
        )
        adopted += 1
    return adopted
