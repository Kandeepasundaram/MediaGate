"""Shared row-mapping helpers for the library.py/library_notes.py/
library_maintenance.py route modules -- split out so all three can build a
LibraryItemOut (or read a media_items row's JSON columns) the same way
without importing from each other."""
from __future__ import annotations

import json
from pathlib import Path

from app.core import media_probe
from app.models import LibraryItemOut


def _metadata_dict(row: dict) -> dict:
    try:
        meta = json.loads(row["metadata"]) if row.get("metadata") else {}
    except json.JSONDecodeError:
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _tags_list(row: dict) -> list[str]:
    try:
        tags = json.loads(row["tags"]) if row.get("tags") else []
    except json.JSONDecodeError:
        tags = []
    return tags if isinstance(tags, list) else []


def _to_out(
    row: dict, viewer_watched_ids: set[int] | None = None, show_status: str | None = None,
    show_personal: tuple[int | None, str | None] | None = None,
) -> LibraryItemOut:
    meta = _metadata_dict(row)
    # TV rows: personal rating/note live on tv_shows (one per show), passed
    # in by the caller (list_tv already looks it up alongside show_status)
    # -- a per-episode media_items.personal_rating would be meaningless.
    # Movies: each row IS the item, so its own column is authoritative.
    personal_rating, personal_note = show_personal if show_personal is not None else (row["personal_rating"], row["personal_note"])

    file_name = None
    size_bytes = None
    if row["final_path"]:
        final_path = Path(row["final_path"])
        file_name = final_path.name
        try:
            size_bytes = final_path.stat().st_size
        except OSError:
            pass  # file moved/deleted since archiving; name still worth showing

    return LibraryItemOut(
        id=row["id"],
        title=row["title"],
        media_type=row["media_type"],
        year=row["year"],
        season_number=row["season_number"],
        episode_number=row["episode_number"],
        tmdb_id=row["tmdb_id"],
        poster_path=meta.get("poster_path"),
        overview=meta.get("overview", ""),
        watched=bool(row["watched"]),
        watched_at=row["watched_at"],
        final_path=row["final_path"],
        archived_at=row["archived_at"],
        file_name=file_name,
        size_bytes=size_bytes,
        episode_title=meta.get("episode_title"),
        air_date=meta.get("air_date"),
        manual_override=bool(row["manual_override"]),
        vote_average=meta.get("vote_average"),
        genres=meta.get("genres") or [],
        resolution=media_probe.resolution_bucket(meta.get("height")),
        hdr=bool(meta.get("hdr", False)),
        audio_channels=meta.get("audio_channels"),
        tags=_tags_list(row),
        viewer_watched=(row["id"] in viewer_watched_ids) if viewer_watched_ids is not None else None,
        show_status=show_status,
        personal_rating=personal_rating,
        personal_note=personal_note,
    )
