from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config_loader import AppConfig, keep_languages_for
from app.core.archiver import ArchiveError, archive_file
from app.core.renamer import RenamePlan, fetch_episode_title, plan_movie_rename, plan_tv_rename
from app.core.subtitle_purger import SUBTITLE_EXTENSIONS, fetch_missing_subtitle, missing_keep_language, purge_subtitles
from app.core.media_server import notify_media_servers
from app.core.opensubtitles_client import OpenSubtitlesClient
from app.core.tmdb_client import MediaResult, TMDBClient, compute_absolute_episode, parse_filename
from app.core.tracker import maybe_auto_track
from app.database import Database
from app.dependencies import get_config, get_database, get_opensubtitles_client, get_tmdb_client
from app.models import (
    ArchiveConfirmRequest,
    ArchiveConfirmResponse,
    ArchiveConfirmResult,
    ArchiveHistoryResponse,
    ArchivePreviewItem,
    ArchivePreviewRequest,
    ArchivePreviewResponse,
    MediaType,
    OperationLogOut,
    TMDBSearchResponse,
    TMDBSearchResultOut,
    UndoResponse,
)

router = APIRouter(prefix="/api/archive", tags=["archive"])


def _is_duplicate(db: Database, plan) -> bool:
    """A media_items row already exists for this exact title (movie: same
    title+year; TV: same show+season+episode) -- surfaced as a warning in
    the preview table, not a hard block, since re-archiving the same title
    under a different source file is sometimes intentional (e.g. a better
    quality re-encode)."""
    for existing in db.list_media_items(media_type=plan.media_type):
        if existing["title"].strip().lower() != plan.title.strip().lower():
            continue
        if plan.media_type == "movie":
            if existing["year"] == plan.year:
                return True
        else:
            if existing["season_number"] == plan.season and existing["episode_number"] == plan.episode:
                return True
    return False


@router.post("/preview", response_model=ArchivePreviewResponse)
def preview_archive(
    payload: ArchivePreviewRequest,
    config: AppConfig = Depends(get_config),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    db: Database = Depends(get_database),
) -> ArchivePreviewResponse:
    items: list[ArchivePreviewItem] = []
    errors: list[str] = []

    for raw_path in payload.paths:
        source = Path(raw_path)
        if not source.exists():
            errors.append(f"File not found: {raw_path}")
            continue

        parsed = parse_filename(source.name)
        override_id = payload.tmdb_overrides.get(raw_path)
        try:
            if parsed.media_type == "tv":
                media = _resolve_tv_match(tmdb, parsed, override_id)
                season = parsed.season or 1
                episode = parsed.episode or 1
                plan = plan_tv_rename(
                    source,
                    config.paths.archive_tv,
                    media,
                    season=season,
                    episode=episode,
                    episode_title=_resolve_episode_title(tmdb, config.renaming, media, season, episode),
                    renaming=config.renaming,
                    absolute_episode=_resolve_absolute_episode(tmdb, config.renaming, media, season, episode),
                    part=parsed.part,
                )
            else:
                media = _resolve_movie_match(tmdb, parsed, override_id)
                plan = plan_movie_rename(
                    source, config.paths.archive_movies, media, renaming=config.renaming, part=parsed.part
                )

            items.append(
                ArchivePreviewItem(
                    source_path=str(plan.source_path),
                    dest_path=str(plan.dest_path),
                    media_type=plan.media_type,
                    title=plan.title,
                    year=plan.year,
                    season=plan.season,
                    episode=plan.episode,
                    tmdb_id=plan.tmdb_id,
                    poster_path=media.poster_path,
                    overview=media.overview,
                    vote_average=plan.vote_average,
                    genres=plan.genres,
                    duplicate=_is_duplicate(db, plan),
                )
            )
        except Exception as exc:  # noqa: BLE001 - surface any per-file failure without aborting the batch
            errors.append(f"{raw_path}: {exc}")

    return ArchivePreviewResponse(items=items, errors=errors)


def _resolve_absolute_episode(
    tmdb: TMDBClient, renaming, media: MediaResult, season: int, episode: int
) -> int | None:
    """Only fetches full TV details (an extra TMDB request beyond the
    title search _resolve_tv_match already did) when the configured
    naming templates actually reference {absolute_episode} -- most
    libraries use plain SxxExx and shouldn't pay for a lookup whose result
    would never be used. get_tv_details is cached per tmdb_id, so this
    costs nothing extra for a season with multiple episodes already
    matched to the same show in this request/process."""
    if media.tmdb_id is None:
        return None
    # "{absolute_episode" (no closing brace) so a format spec like
    # "{absolute_episode:03d}" still matches -- a plain "in" check for the
    # full "{absolute_episode}" token would miss that, the exact form the
    # anime-style naming example in the Settings hint text recommends.
    if "{absolute_episode" not in renaming.tv_file and "{absolute_episode" not in renaming.tv_season_folder:
        return None
    full_media = tmdb.get_tv_details(media.tmdb_id)
    if full_media is None:
        return None
    return compute_absolute_episode(full_media, season, episode)


def _resolve_episode_title(
    tmdb: TMDBClient, renaming, media: MediaResult, season: int, episode: int
) -> str | None:
    """Only looked up (API mode only -- see fetch_episode_title) when the
    configured tv_file template actually references {episode_title} or
    {episode_title_suffix}, same cost-avoidance reasoning as
    _resolve_absolute_episode."""
    if media.tmdb_id is None:
        return None
    if "{episode_title" not in renaming.tv_file:
        return None
    return fetch_episode_title(tmdb, media.tmdb_id, season, episode)


def _resolve_tv_match(tmdb: TMDBClient, parsed, override_id: int | None) -> MediaResult:
    if override_id is not None:
        media = tmdb.get_tv_details(override_id)
        if media:
            return media
    matches = tmdb.search_tv(parsed.title)
    return matches[0] if matches else MediaResult(tmdb_id=None, title=parsed.title, media_type="tv")


def _resolve_movie_match(tmdb: TMDBClient, parsed, override_id: int | None) -> MediaResult:
    if override_id is not None:
        media = tmdb.get_movie_details(override_id)
        if media:
            return media
    matches = tmdb.search_movie(parsed.title, parsed.year)
    return matches[0] if matches else MediaResult(tmdb_id=None, title=parsed.title, media_type="movie", year=parsed.year)


@router.get("/search", response_model=TMDBSearchResponse)
def search_tmdb(
    title: str,
    media_type: MediaType,
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> TMDBSearchResponse:
    """Candidate TMDB matches for a title -- backs the "Change Match" picker
    in the archive/organize preview table, for manual disambiguation when
    the automatic top result is wrong."""
    matches = tmdb.search_tv(title) if media_type == "tv" else tmdb.search_movie(title)
    return TMDBSearchResponse(
        results=[
            TMDBSearchResultOut(tmdb_id=m.tmdb_id, title=m.title, year=m.year, overview=m.overview, poster_path=m.poster_path)
            for m in matches
        ]
    )


def _copy_sibling_subtitles(source: Path, dest_folder: Path) -> None:
    for sub in source.parent.glob(f"{source.stem}*"):
        if sub.suffix.lower() in SUBTITLE_EXTENSIONS:
            shutil.copy2(sub, dest_folder / sub.name)


@router.post("/confirm", response_model=ArchiveConfirmResponse)
def confirm_archive(
    payload: ArchiveConfirmRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
    opensubtitles: OpenSubtitlesClient = Depends(get_opensubtitles_client),
) -> ArchiveConfirmResponse:
    results: list[ArchiveConfirmResult] = []

    for item in payload.items:
        source = Path(item.source_path)
        dest = Path(item.dest_path)

        if payload.dry_run:
            results.append(_dry_run_result(source, dest))
            continue

        keep_languages = keep_languages_for(config.subtitles, item.media_type)

        # Fetch before purge -- a subtitle downloaded after the purge step
        # would immediately be at risk of getting purged again on the next
        # run if its language tag doesn't match exactly, and there's no
        # reason to purge first anyway.
        if config.subtitles.auto_fetch_missing_subtitles and opensubtitles.enabled and item.tmdb_id is not None:
            missing = missing_keep_language(source.parent, source.stem, keep_languages)
            if missing:
                fetch_missing_subtitle(
                    opensubtitles, source.parent, source.stem, item.tmdb_id, item.media_type, missing,
                    season=item.season, episode=item.episode,
                )

        if payload.purge_subtitles:
            purge_subtitles(source.parent, keep_languages=keep_languages, dry_run=False)

        plan = RenamePlan(
            source_path=source,
            dest_path=dest,
            media_type=item.media_type,
            tmdb_id=item.tmdb_id,
            title=item.title,
            year=item.year,
            season=item.season,
            episode=item.episode,
            poster_path=item.poster_path,
            overview=item.overview,
            vote_average=item.vote_average,
            genres=item.genres,
        )
        try:
            media_id = archive_file(db, plan, write_nfo_files=config.media_server.write_nfo_files)
            _copy_sibling_subtitles(source, dest.parent)
            maybe_auto_track(
                db, config.tracker.auto_track_new, item.tmdb_id, item.media_type, item.title, item.season
            )
            results.append(
                ArchiveConfirmResult(source_path=str(source), dest_path=str(dest), media_id=media_id, status="success")
            )
        except ArchiveError as exc:
            results.append(ArchiveConfirmResult(source_path=str(source), status="failed", error=str(exc)))

    if not payload.dry_run and any(r.status == "success" for r in results):
        notify_media_servers(config)

    return ArchiveConfirmResponse(results=results)


def _dry_run_result(source: Path, dest: Path) -> ArchiveConfirmResult:
    """What archive/organize would report for `source` without actually
    touching the filesystem, DB, subtitles, tracker, or media server --
    only the source-exists/non-empty checks are predictable in advance;
    a copy/checksum failure or an overwrite conflict can't be."""
    if not source.exists():
        return ArchiveConfirmResult(source_path=str(source), status="failed", error="Source file not found")
    if source.stat().st_size == 0:
        return ArchiveConfirmResult(source_path=str(source), status="failed", error="Source file is 0 bytes")
    return ArchiveConfirmResult(source_path=str(source), dest_path=str(dest), status="success")


@router.get("/history", response_model=ArchiveHistoryResponse)
def archive_history(
    operation_type: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    db: Database = Depends(get_database),
) -> ArchiveHistoryResponse:
    """Defaults to every operation type (the History tab's "All" filter);
    pass operation_type to narrow to one, e.g. for the tracker-check-only
    view. status/since/until back the History tab's own filter row; a
    higher limit (the tab's "Export View (CSV)" button uses one) exports
    everything currently matching the filters, not just what's on screen.
    """
    ops = db.list_operations(operation_type=operation_type, status=status, since=since, until=until, limit=limit)
    for op in ops:
        if op.get("details"):
            op["details"] = json.loads(op["details"])
    return ArchiveHistoryResponse(operations=[OperationLogOut(**op) for op in ops])


@router.post("/history/{operation_id}/undo", response_model=UndoResponse)
def undo_operation(operation_id: int, db: Database = Depends(get_database)) -> UndoResponse:
    """Reverses a successful 'archive' (copy) or 'rename' (organize/move)
    operation: deletes the copied file (archive) or moves the file back to
    where it came from (rename), and updates media_items to match. Anything
    else (a failed op, or one already undone -- the dest file is simply
    gone by then) is rejected rather than silently no-op'd.
    """
    op = db.get_operation(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    if op["status"] != "success":
        raise HTTPException(status_code=400, detail="Only a successful operation can be undone")
    if op["operation_type"] not in ("archive", "rename"):
        raise HTTPException(status_code=400, detail=f"Undo not supported for operation type '{op['operation_type']}'")

    details = json.loads(op["details"]) if op["details"] else {}

    if op["operation_type"] == "archive":
        dest = Path(details.get("dest", ""))
        if not dest.exists():
            raise HTTPException(status_code=400, detail="Archived file no longer exists at its destination")
        dest.unlink()
        if op["media_id"] is not None:
            db.delete_media_item(op["media_id"])
        db.log_operation(operation_type="delete", status="success", media_id=None, details={"undid_operation_id": operation_id, "path": str(dest)})
        return UndoResponse(undone=True, detail=f"Deleted {dest}")

    # rename/organize: move the file back to where it came from.
    source = Path(details.get("to", ""))
    original = Path(details.get("from", ""))
    if not source.exists():
        raise HTTPException(status_code=400, detail="File no longer exists at its organized location")
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(original))
    if op["media_id"] is not None:
        db.update_media_item(op["media_id"], final_path=str(original))
    db.log_operation(operation_type="rename", status="success", media_id=op["media_id"], details={"undid_operation_id": operation_id, "from": str(source), "to": str(original)})
    return UndoResponse(undone=True, detail=f"Moved back to {original}")
