"""Rename Lab: dry-run simulation of renaming every tracked episode of one TV
show under one or more naming patterns. Read-only -- nothing here touches the
filesystem or the database. Applying a chosen pattern goes through the
existing POST /api/library/organize (each row carries a ready-made
ArchivePreviewItem), so move/NFO/undo/logging behavior stays in one place.
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.library_common import _metadata_dict
from app.config_loader import AppConfig, RenamingConfig, load_config, update_settings
from app.core.library_adopt import adopt_new_files
from app.core.renamer import (
    TV_PATTERN_PRESETS,
    TV_TEMPLATE_TOKENS,
    plan_tv_rename,
    sanitize_filename,
    validate_tv_templates,
)
from app.core.tmdb_client import MediaResult, TMDBClient, compute_absolute_episode, resolve_season_episodes
from app.core.tvmaze_client import TVmazeClient
from app.database import Database
from app.dependencies import get_config, get_database, get_tmdb_client, get_tvmaze_client, reset_singletons
from app.models import (
    ArchivePreviewItem,
    RenameLabCheckRequest,
    RenameLabCheckResponse,
    RenameLabPattern,
    RenameLabPatternResult,
    RenameLabPresetsResponse,
    RenameLabRow,
    RenameLabSavePatternRequest,
    RenameLabShowFolder,
    RenameLabSimulateRequest,
    RenameLabSimulateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library/rename-lab", tags=["library"])


MAX_SAVED_PATTERNS = 20
_RESERVED_IDS = {"current", "custom"} | {p["id"] for p in TV_PATTERN_PRESETS}


def _saved_patterns(config: AppConfig) -> list[dict]:
    """Read from disk, not the injected config object: saving goes through
    update_settings (which rewrites config.yaml), and reading the file back
    keeps this correct regardless of which AppConfig instance a caller holds."""
    try:
        return list(load_config(config.config_path, create_dirs=False).renaming.saved_tv_patterns or [])
    except Exception:
        return list(config.renaming.saved_tv_patterns or [])


def _save_patterns(config: AppConfig, patterns: list[dict]) -> None:
    update_settings(config.config_path, {"renaming": {"saved_tv_patterns": patterns}})
    reset_singletons()


@router.get("/presets", response_model=RenameLabPresetsResponse)
def rename_lab_presets(config: AppConfig = Depends(get_config)) -> RenameLabPresetsResponse:
    presets = [RenameLabPattern(**p) for p in TV_PATTERN_PRESETS]
    # The user's currently configured global pattern is always offered too.
    presets.insert(0, RenameLabPattern(
        id="current", label="Current setting",
        tv_show_folder=config.renaming.tv_show_folder,
        tv_season_folder=config.renaming.tv_season_folder, tv_file=config.renaming.tv_file,
    ))
    presets.extend(RenameLabPattern(**{**p, "saved": True}) for p in _saved_patterns(config))
    return RenameLabPresetsResponse(presets=presets, tokens=TV_TEMPLATE_TOKENS)


@router.post("/patterns", response_model=RenameLabPattern)
def save_pattern(payload: RenameLabSavePatternRequest, config: AppConfig = Depends(get_config)) -> RenameLabPattern:
    error = validate_tv_templates(payload.tv_season_folder, payload.tv_file, payload.tv_show_folder)
    if error:
        raise HTTPException(status_code=400, detail=error)
    saved = _saved_patterns(config)
    existing = next((p for p in saved if p.get("id") == payload.id), None) if payload.id else None
    if existing is None and len(saved) >= MAX_SAVED_PATTERNS:
        raise HTTPException(status_code=400, detail=f"At most {MAX_SAVED_PATTERNS} saved patterns")
    if existing is not None:
        existing.update(
            label=payload.label, tv_show_folder=payload.tv_show_folder,
            tv_season_folder=payload.tv_season_folder, tv_file=payload.tv_file,
        )
        entry = existing
    else:
        base = "saved-" + (re.sub(r"[^a-z0-9]+", "-", payload.label.lower()).strip("-") or "pattern")
        taken = {p.get("id") for p in saved} | _RESERVED_IDS
        new_id, n = base, 2
        while new_id in taken:
            new_id, n = f"{base}-{n}", n + 1
        entry = {
            "id": new_id, "label": payload.label, "tv_show_folder": payload.tv_show_folder,
            "tv_season_folder": payload.tv_season_folder, "tv_file": payload.tv_file,
        }
        saved.append(entry)
    _save_patterns(config, saved)
    return RenameLabPattern(**{**entry, "saved": True})


@router.delete("/patterns/{pattern_id}")
def delete_pattern(pattern_id: str, config: AppConfig = Depends(get_config)) -> dict:
    saved = _saved_patterns(config)
    remaining = [p for p in saved if p.get("id") != pattern_id]
    if len(remaining) == len(saved):
        raise HTTPException(status_code=404, detail="Saved pattern not found")
    _save_patterns(config, remaining)
    return {"deleted": pattern_id}


@router.post("/check", response_model=RenameLabCheckResponse)
def check_destinations(payload: RenameLabCheckRequest, config: AppConfig = Depends(get_config)) -> RenameLabCheckResponse:
    """Read-only: which of the proposed destination paths already exist on
    disk. Used after a hand-edited name or an auto-suffix so the page can
    re-flag conflicts without a full re-simulation. Only paths inside the
    TV archive root are examined."""
    root = config.paths.archive_tv.resolve()
    exists, outside = [], []
    for raw in payload.paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError:
            outside.append(raw)
            continue
        if resolved != root and root not in resolved.parents:
            outside.append(raw)
        elif path.exists():
            exists.append(raw)
    return RenameLabCheckResponse(exists=exists, outside_archive=outside)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a == b


def _episode_lookup(
    tmdb: TMDBClient, tvmaze: TVmazeClient, tmdb_id: int | None, seasons: set[int]
) -> dict[tuple[int, int], dict]:
    """(season, episode) -> remote episode dict, one request per season (not
    per episode). Empty without a TMDB id / API access."""
    lookup: dict[tuple[int, int], dict] = {}
    if tmdb_id is None:
        return lookup
    for season in sorted(seasons):
        try:
            episodes = resolve_season_episodes(tmdb, tvmaze, tmdb_id, season)
        except Exception as exc:  # network/API failure shouldn't kill the simulation
            logger.warning("Rename lab: season %s lookup failed: %s", season, exc)
            continue
        for e in episodes:
            number = e.get("episode_number")
            if number is not None:
                lookup[(season, number)] = e
    return lookup


@router.post("/simulate", response_model=RenameLabSimulateResponse)
def simulate_show_rename(
    payload: RenameLabSimulateRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    tvmaze: TVmazeClient = Depends(get_tvmaze_client),
) -> RenameLabSimulateResponse:
    if payload.tmdb_id is None and not payload.title:
        raise HTTPException(status_code=400, detail="tmdb_id or title is required")
    rescan_added = adopt_new_files(db, config, "tv") if payload.rescan else None
    if payload.refresh and payload.tmdb_id is not None:
        tmdb.clear_show_cache(payload.tmdb_id)
    rows = [
        r for r in db.list_tv_episodes_for_show(payload.tmdb_id, payload.title)
        if r["final_path"] and r["season_number"] is not None and r["episode_number"] is not None
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="No tracked episodes found for this show")

    media: MediaResult | None = None
    if payload.tmdb_id is not None:
        try:
            media = tmdb.get_tv_details(payload.tmdb_id)
        except Exception as exc:
            logger.warning("Rename lab: show details lookup failed: %s", exc)
    if media is None:
        media = MediaResult(tmdb_id=payload.tmdb_id, title=rows[0]["title"], media_type="tv", year=rows[0]["year"])

    remote = _episode_lookup(tmdb, tvmaze, payload.tmdb_id, {r["season_number"] for r in rows})

    # Per-episode data is pattern-independent: compute it once.
    episodes = []
    for row in rows:
        season, episode = row["season_number"], row["episode_number"]
        meta = _metadata_dict(row)
        rem = remote.get((season, episode), {})
        source = Path(row["final_path"])
        try:
            size = source.stat().st_size
        except OSError:
            size = None
        episodes.append({
            "row": row, "source": source, "season": season, "episode": episode,
            "title": rem.get("name") or meta.get("episode_title") or None,
            "air_date": rem.get("air_date") or meta.get("air_date") or None,
            "overview": rem.get("overview") or meta.get("overview") or "",
            "absolute": compute_absolute_episode(media, season, episode),
            "size": size,
            "duration": meta.get("duration_seconds"),
            "height": meta.get("height"),
            "poster_path": meta.get("poster_path") or media.poster_path,
            "vote_average": meta.get("vote_average"),
            "genres": meta.get("genres") or [],
        })

    results: list[RenameLabPatternResult] = []
    for pattern in payload.patterns:
        result = RenameLabPatternResult(
            id=pattern.id, label=pattern.label or pattern.id,
            tv_show_folder=pattern.tv_show_folder,
            tv_season_folder=pattern.tv_season_folder, tv_file=pattern.tv_file,
        )
        error = validate_tv_templates(pattern.tv_season_folder, pattern.tv_file, pattern.tv_show_folder)
        if error:
            result.error = error
            results.append(result)
            continue

        # "overwrite" keeps plan_tv_rename's path pure: the real policy is
        # applied at organize time, and here collisions are flagged instead
        # of being silently turned into " (2)" names.
        renaming = RenamingConfig(
            tv_show_folder=pattern.tv_show_folder,
            tv_season_folder=pattern.tv_season_folder, tv_file=pattern.tv_file, collision_policy="overwrite",
        )
        uses_title = "{episode_title" in pattern.tv_file
        planned = []
        for ep in episodes:
            plan = plan_tv_rename(
                ep["source"], config.paths.archive_tv, media, ep["season"], ep["episode"],
                episode_title=ep["title"], air_date=ep["air_date"], renaming=renaming,
                absolute_episode=ep["absolute"],
            )
            planned.append((ep, plan))

        result.show_folder = _show_folder(episodes, planned[0][1].dest_path, config)
        dest_counts = Counter(str(plan.dest_path).lower() for _, plan in planned)
        for ep, plan in planned:
            flags: list[str] = []
            unchanged = plan.dest_path == ep["source"]
            if unchanged:
                flags.append("unchanged")
            if not ep["source"].exists():
                flags.append("source_missing")
            if not unchanged and plan.dest_path.exists() and not _same_file(plan.dest_path, ep["source"]):
                flags.append("collision_on_disk")
            if dest_counts[str(plan.dest_path).lower()] > 1:
                flags.append("batch_duplicate")
            if uses_title and not ep["title"]:
                flags.append("missing_title")

            if unchanged:
                result.unchanged += 1
            elif any(f in flags for f in ("collision_on_disk", "batch_duplicate", "source_missing")):
                result.conflicts += 1
            else:
                result.changes += 1

            result.rows.append(RenameLabRow(
                media_id=ep["row"]["id"],
                current_path=str(ep["source"]),
                dest_path=str(plan.dest_path),
                season=ep["season"], episode=ep["episode"],
                code=f"S{ep['season']:02d}E{ep['episode']:02d}",
                episode_title=ep["title"], air_date=ep["air_date"],
                absolute_episode=ep["absolute"], overview=ep["overview"],
                size_bytes=ep["size"], duration_seconds=ep["duration"], height=ep["height"],
                flags=flags,
                item=ArchivePreviewItem(
                    source_path=str(ep["source"]), dest_path=str(plan.dest_path), media_type="tv",
                    title=media.title, year=media.year, season=ep["season"], episode=ep["episode"],
                    tmdb_id=payload.tmdb_id, poster_path=ep["poster_path"], overview=ep["overview"],
                    vote_average=ep["vote_average"], genres=ep["genres"],
                    episode_title=ep["title"], air_date=ep["air_date"],
                ),
            ))
        results.append(result)

    return RenameLabSimulateResponse(
        show_title=media.title, show_year=media.year, tmdb_id=payload.tmdb_id,
        metadata_available=bool(remote), rescan_added=rescan_added, metadata_note=_metadata_note(remote, payload.tmdb_id, tmdb, tvmaze),
        show_folder=next((r.show_folder for r in results if r.show_folder), None),
        patterns=results,
    )


def _metadata_note(remote: dict, tmdb_id: int | None, tmdb: TMDBClient, tvmaze: TVmazeClient) -> str | None:
    """Why remote episode titles are missing (None when they were fetched)."""
    if remote:
        return None
    if tmdb_id is None:
        return "This show has no TMDB match yet, so episode titles can't be fetched. Wait for the metadata backfill or fix the match."
    if not getattr(tmdb, "_api", None) and not tvmaze.enabled:
        return "Episode titles need a TMDB API key (Settings) or TVmaze enabled (Settings > Integrations). Neither is set."
    return "TMDB/TVmaze returned no episode list for this show's seasons. Check the logs for lookup errors."



def _show_folder(episodes: list[dict], planned_dest: Path, config: AppConfig) -> RenameLabShowFolder | None:
    """Show-level folder (archive_tv/<Show>) the episodes currently live in
    vs the one a pattern's show-folder template gives (the first segment of
    any planned destination under the root). Only meaningful for the
    standard Show/Season/file layout (files nested 2 deep under the root)."""
    root = config.paths.archive_tv
    dirs = []
    for ep in episodes:
        try:
            rel = ep["source"].relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) == 3:
            dirs.append(root / rel.parts[0])
    if not dirs:
        return None
    current = Counter(dirs).most_common(1)[0][0]
    try:
        proposed = root / planned_dest.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return None
    differs = current != proposed
    return RenameLabShowFolder(
        current=str(current), proposed=str(proposed),
        will_rename=differs and not proposed.exists(),
        conflict=differs and proposed.exists(),
    )
