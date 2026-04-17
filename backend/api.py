#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChainThink Article Publisher — FastAPI Application.

Slim assembler: CORS, route mounting, SPA serving.
All business logic lives in services/, pipelines/, routes/.
"""

import asyncio
import logging
import os
import yaml
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routes import status_router, articles_router, pipeline_router, logs_router, scheduler_router, memory_router, database_router, ai_articles_router, workflow_router
from routes.auth import router as auth_router, init_auth
from routes.settings import router as settings_router, init_settings_routes
from middleware.auth import AuthMiddleware
from services.pipeline_service import PipelineService
from services.ai_pipeline_service import AiPipelineService
from services.database import get_database
from utils.logging_config import setup_logging, get_broadcaster

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
setup_logging(BASE_DIR)
log = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # Load config and init auth
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Init database
    db_path = config.get("database", {}).get("sqlite_path", "data/articles.db")
    db = get_database(BASE_DIR / db_path)

    # Init auth (seeds default user from config)
    init_auth(config, database=db)

    # Init settings routes
    init_settings_routes(db)

    # Startup
    svc = PipelineService.create(BASE_DIR)
    app.state.pipeline_service = svc

    # Init AI pipeline service
    ai_svc = AiPipelineService.create(BASE_DIR, db)
    app.state.ai_pipeline_service = ai_svc

    # Restore schedules from database (with defaults 10-20 min)
    log.info("Restoring pipeline schedules...")
    svc.restore_schedules()
    ai_svc.restore_schedules()
    if svc.push_scheduler:
        svc.push_scheduler.start()
    if svc.auto_publish_scheduler:
        svc.auto_publish_scheduler.start()

    # Set default schedules for sources without saved config (10-20 min)
    # Blockchain sources: 15 min default
    for src in ["stcn", "techflow", "blockbeats", "chaincatcher", "odaily"]:
        existing = db.get_schedule(src)
        if existing is None:
            interval = 10 + (hash(src) % 11)  # 10-20 minutes pseudo-random
            log.info("Setting default schedule for %s: %d minutes (disabled, use UI to enable)", src, interval)
            db.save_schedule(src, False, interval)

    broadcaster = get_broadcaster()
    if broadcaster:
        broadcaster.set_loop(asyncio.get_running_loop())
        app.state.log_broadcaster = broadcaster

    log.info("PipelineService initialized with sources: %s", list(svc.scrapers.keys()))
    log.info("AiPipelineService initialized with sources: %s", list(ai_svc.scrapers.keys()))

    yield

    # Shutdown
    log.info("Shutting down pipeline service...")
    svc.stop_all_schedules()
    ai_svc.stop_all_schedules()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Article Publisher",
    version="1.0.0",
    description="ChainThink article fetching, cleaning, and publishing pipeline",
    lifespan=lifespan,
)

# Auth middleware (reads serializer from routes.auth, initialized during lifespan)
app.add_middleware(AuthMiddleware)

# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------

app.include_router(status_router)
app.include_router(articles_router)
app.include_router(pipeline_router)
app.include_router(logs_router)
app.include_router(scheduler_router)
app.include_router(memory_router)
app.include_router(auth_router)
app.include_router(database_router)
app.include_router(settings_router)
app.include_router(ai_articles_router)
app.include_router(workflow_router)

# ---------------------------------------------------------------------------
# Serve frontend static files (production)
# ---------------------------------------------------------------------------

frontend_dist = BASE_DIR / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = frontend_dist / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_dist / "index.html"))

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    reload = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=reload, access_log=False)
