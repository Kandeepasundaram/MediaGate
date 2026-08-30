from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config_loader import AppConfig
from app.core.scanner import scan_directory, scan_targets
from app.database import Database
from app.dependencies import get_config, get_database
from app.models import ScanDirectoryRequest, ScannedFileOut, ScanResponse

router = APIRouter(prefix="/api/scan", tags=["scan"])


def _to_out(scanned) -> list[ScannedFileOut]:
    return [
        ScannedFileOut(
            path=str(f.path),
            size_bytes=f.size_bytes,
            modified_at=f.modified_at,
            parsed_title=f.parsed.title,
            media_type=f.parsed.media_type,
            year=f.parsed.year,
            season=f.parsed.season,
            episode=f.parsed.episode,
            subtitle_count=len(f.subtitles),
        )
        for f in scanned
    ]


@router.get("", response_model=ScanResponse)
def scan_library(
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> ScanResponse:
    """Scans both incoming directories plus both archive roots as one library.

    Movies and TV each get their own incoming + archive path (never a single
    shared bucket) — but incoming and archive commonly point at the *same*
    folder per type for a library organized in-place. Either way, anything
    already recorded in the database as a source or an archived copy is
    excluded, so re-running a scan only surfaces genuinely new files.
    """
    roots = [
        config.paths.incoming_movies,
        config.paths.incoming_tv,
        config.paths.archive_movies,
        config.paths.archive_tv,
    ]
    scanned = scan_targets(roots, known_paths=db.list_known_paths())
    unique_dirs = list(dict.fromkeys(str(r) for r in roots))
    return ScanResponse(directories=unique_dirs, files=_to_out(scanned))


@router.post("/directory", response_model=ScanResponse)
def scan_specific_directory(payload: ScanDirectoryRequest) -> ScanResponse:
    directory = Path(payload.directory)
    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {payload.directory}")
    scanned = scan_directory(directory)
    return ScanResponse(directories=[str(directory)], files=_to_out(scanned))
