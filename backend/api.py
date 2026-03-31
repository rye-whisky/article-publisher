#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChainThink Article Publisher — FastAPI Application.

Slim assembler: CORS, route mounting, SPA serving.
All business logic lives in services/, pipelines/, routes/.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routes import status_router, articles_router, pipeline_router, logs_router, scheduler_router
from services.pipeline_service import PipelineService
from utils.logging_config import setup_logging, get_broadcaster

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
setup_logging(BASE_DIR)
log = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Article Publisher",
    version="1.0.0",
    description="ChainThink article fetching, cleaning, and publishing pipeline",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Wire pipeline service into app.state
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    import asyncio

    svc = PipelineService.create(BASE_DIR)
    app.state.pipeline_service = svc

    broadcaster = get_broadcaster()
    if broadcaster:
        broadcaster.set_loop(asyncio.get_running_loop())
        app.state.log_broadcaster = broadcaster

    log.info("PipelineService initialized with sources: %s", list(svc.scrapers.keys()))

# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------

app.include_router(status_router)
app.include_router(articles_router)
app.include_router(pipeline_router)
app.include_router(logs_router)
app.include_router(scheduler_router)

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
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
