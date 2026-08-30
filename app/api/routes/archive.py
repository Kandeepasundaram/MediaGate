from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends

from app.config_loader import AppConfig
from app.core.archiver import ArchiveError, archive_file
from app.core.renamer import RenamePlan, plan_movie_rename, plan_tv_rename
from app.core.subtitle_purger import SUBTITLE_EXTENSIONS, purge_subtitles
from app.core.tmdb_client import MediaResult, TMDBClient, parse_filename
from app.database import Database
from app.dependencies import get_config, get_database, get_tmdb_client
from app.models import (
    ArchiveConfirmRequest,
    ArchiveConfirmResponse,
    ArchiveConfirmResult,
    ArchiveHistoryResponse,
    ArchivePreviewItem,
    ArchivePreviewRequest,
    ArchivePreviewResponse,
    OperationLogOut,
)

router = APIRouter(prefix="/api/archive", tags=["archive"])


@router.post("/preview", response_model=ArchivePreviewResponse)
def preview_archive(
    payload: ArchivePreviewRequest,
    config: AppConfig = Depends(get_config),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> ArchivePreviewResponse:
    items: list[ArchivePreviewItem] = []
    errors: list[str] = []

    for raw_path in payload.paths:
        source = Path(raw_path)
        if not source.exists():
            errors.append(f"File not found: {raw_path}")
            continue

        parsed = parse_filename(source.name)
        try:
            if parsed.media_type == "tv":
                matches = tmdb.search_tv(parsed.title)
                media = matches[0] if matches else MediaResult(tmdb_id=None, title=parsed.title, media_type="tv")
                plan = plan_tv_rename(
                    source,
                    config.paths.archive_tv,
                    media,
                    season=parsed.season or 1,
                    episode=parsed.episode or 1,
                )
            else:
                matches = tmdb.search_movie(parsed.title, parsed.year)
                media = matches[0] if matches else MediaResult(
                    tmdb_id=None, title=parsed.title, media_type="movie", year=parsed.year
                )
                plan = plan_movie_rename(source, config.paths.archive_movies, media)

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
                )
            )
        except Exception as exc:  # noqa: BLE001 - surface any per-file failure without aborting the batch
            errors.append(f"{raw_path}: {exc}")

    return ArchivePreviewResponse(items=items, errors=errors)


def _copy_sibling_subtitles(source: Path, dest_folder: Path) -> None:
    for sub in source.parent.glob(f"{source.stem}*"):
        if sub.suffix.lower() in SUBTITLE_EXTENSIONS:
            shutil.copy2(sub, dest_folder / sub.name)


@router.post("/confirm", response_model=ArchiveConfirmResponse)
def confirm_archive(
    payload: ArchiveConfirmRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> ArchiveConfirmResponse:
    results: list[ArchiveConfirmResult] = []

    for item in payload.items:
        source = Path(item.source_path)
        dest = Path(item.dest_path)

        if payload.purge_subtitles:
            purge_subtitles(source.parent, dry_run=False)

        plan = RenamePlan(
            source_path=source,
            dest_path=dest,
            media_type=item.media_type,
            tmdb_id=item.tmdb_id,
            title=item.title,
            year=item.year,
            season=item.season,
            episode=item.episode,
        )
        try:
            media_id = archive_file(db, plan)
            _copy_sibling_subtitles(source, dest.parent)
            results.append(
                ArchiveConfirmResult(source_path=str(source), dest_path=str(dest), media_id=media_id, status="success")
            )
        except ArchiveError as exc:
            results.append(ArchiveConfirmResult(source_path=str(source), status="failed", error=str(exc)))

    return ArchiveConfirmResponse(results=results)


@router.get("/history", response_model=ArchiveHistoryResponse)
def archive_history(limit: int = 100, db: Database = Depends(get_database)) -> ArchiveHistoryResponse:
    import json

    ops = db.list_operations(operation_type="archive", limit=limit)
    for op in ops:
        if op.get("details"):
            op["details"] = json.loads(op["details"])
    return ArchiveHistoryResponse(operations=[OperationLogOut(**op) for op in ops])
