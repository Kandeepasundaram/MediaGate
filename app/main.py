from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    archive,
    library,
    library_browse,
    library_maintenance,
    library_notes,
    reports,
    scan,
    settings,
    status,
    tracker,
    universes,
    watchlist,
    webhooks,
)
from app.core import fs_watcher, metadata_backfill, scheduler
from app.dependencies import get_config, get_database, get_new_file_tracker, require_api_token

logger = logging.getLogger("media_manager")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_database().init_db()
    scheduler_task = scheduler.start()
    maintenance_task = scheduler.start_maintenance()
    backup_task = scheduler.start_backup()
    reports_task = scheduler.start_reports()
    backfill_task = metadata_backfill.start()

    watcher_observer = None
    config = get_config()
    if config.watcher.enabled:
        watcher_observer = fs_watcher.start_watching(
            [config.paths.incoming_movies, config.paths.incoming_tv,
             config.paths.archive_movies, config.paths.archive_tv],
            get_new_file_tracker(),
        )
        logger.info("Filesystem watcher enabled for incoming/archive folders")

    logger.info(
        "Media Manager started (daily tracker check + weekly DB maintenance + daily backup + "
        "metadata backfill scheduled in-process)"
    )
    yield
    await scheduler.stop(scheduler_task)
    await scheduler.stop_maintenance(maintenance_task)
    await scheduler.stop_backup(backup_task)
    await scheduler.stop_reports(reports_task)
    await metadata_backfill.stop(backfill_task)
    if watcher_observer is not None:
        fs_watcher.stop_watching(watcher_observer)


def create_app() -> FastAPI:
    config = get_config()
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        filename=str(config.logging.file),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(title="Media Manager", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    token_gate = [Depends(require_api_token)]
    app.include_router(scan.router, dependencies=token_gate)
    app.include_router(archive.router, dependencies=token_gate)
    app.include_router(tracker.router, dependencies=token_gate)
    app.include_router(universes.router, dependencies=token_gate)
    app.include_router(status.router, dependencies=token_gate)
    app.include_router(settings.router, dependencies=token_gate)
    app.include_router(library.router, dependencies=token_gate)
    app.include_router(library_notes.router, dependencies=token_gate)
    app.include_router(library_maintenance.router, dependencies=token_gate)
    app.include_router(library_browse.router, dependencies=token_gate)
    app.include_router(watchlist.router, dependencies=token_gate)
    app.include_router(reports.router, dependencies=token_gate)
    app.include_router(webhooks.router, dependencies=token_gate)

    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

    return app


app = create_app()
