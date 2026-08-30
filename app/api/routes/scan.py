from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.config_loader import AppConfig
from app.core.scanner import scan_directory
from app.dependencies import get_config
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
def scan_active_directory(config: AppConfig = Depends(get_config)) -> ScanResponse:
    scanned = scan_directory(config.paths.active_dir)
    return ScanResponse(directory=str(config.paths.active_dir), files=_to_out(scanned))


@router.post("/directory", response_model=ScanResponse)
def scan_specific_directory(payload: ScanDirectoryRequest) -> ScanResponse:
    from pathlib import Path

    directory = Path(payload.directory)
    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {payload.directory}")
    scanned = scan_directory(directory)
    return ScanResponse(directory=str(directory), files=_to_out(scanned))
