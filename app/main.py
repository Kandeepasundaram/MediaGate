from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import archive, library, scan, settings, status, tracker
from app.core import metadata_backfill, scheduler
from app.dependencies import get_config, get_database

logger = logging.getLogger("media_manager")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_database().init_db()
    scheduler_task = scheduler.start()
    maintenance_task = scheduler.start_maintenance()
    backfill_task = metadata_backfill.start()
    logger.info("Media Manager started (daily tracker check + weekly DB maintenance + metadata backfill scheduled in-process)")
    yield
    await scheduler.stop(scheduler_task)
    await scheduler.stop_maintenance(maintenance_task)
    await metadata_backfill.stop(backfill_task)


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

    app.include_router(scan.router)
    app.include_router(archive.router)
    app.include_router(tracker.router)
    app.include_router(status.router)
    app.include_router(settings.router)
    app.include_router(library.router)

    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

    return app


app = create_app()
