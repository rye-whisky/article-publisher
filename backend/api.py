#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChainThink Article Publisher — FastAPI Application.

Layered architecture:
  api.py              → FastAPI app assembly, CORS, static files
  routes/             → API endpoint definitions
  services/           → Business logic wrappers
  models/             → Pydantic request/response schemas
  config/             → Configuration loader
  utils/              → Logging, helpers
  pipeline.py         → Core pipeline engine
  crc64.py            → CRC64 hash implementation
"""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline import Pipeline, setup_logging

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

setup_logging(BASE_DIR)
log = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# Pipeline singleton
# ---------------------------------------------------------------------------

_pipeline: Optional[Pipeline] = None
_pipeline_lock = threading.Lock()


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                _pipeline = Pipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# Run state tracker
# ---------------------------------------------------------------------------

class RunState:
    """Thread-safe tracker for background pipeline runs."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.started_at: Optional[str] = None
        self.result: Optional[dict] = None

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            return True

    def finish(self, result: dict):
        with self._lock:
            self.running = False
            self.result = result

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_result": self.result,
            "started_at": self.started_at,
        }


run_state = RunState()

# ---------------------------------------------------------------------------
# Scheduler state manager
# ---------------------------------------------------------------------------

class SchedulerState:
    """Thread-safe scheduler state and timer management."""

    def __init__(self):
        self._lock = threading.Lock()
        self.enabled = False
        self.interval_minutes = 60
        self.next_run_time: Optional[str] = None
        self._timer: Optional[threading.Timer] = None
        self._stop_event = threading.Event()

    def set_config(self, enabled: bool, interval_minutes: int):
        with self._lock:
            self.enabled = enabled
            self.interval_minutes = max(1, min(1440, interval_minutes))
            self._restart_timer()

    def _restart_timer(self):
        self._stop_event.set()
        if self._timer:
            self._timer.cancel()
        self._stop_event.clear()

        if not self.enabled:
            self.next_run_time = None
            return

        import datetime as dt
        next_run = dt.datetime.now() + dt.timedelta(minutes=self.interval_minutes)
        self.next_run_time = next_run.strftime("%Y-%m-%dT%H:%M:%S")

        def _callback():
            if self._stop_event.is_set():
                return
            self._run_pipeline()
            if self.enabled and not self._stop_event.is_set():
                self._restart_timer()

        self._timer = threading.Timer(self.interval_minutes * 60, _callback)
        self._timer.daemon = True
        self._timer.start()

    def _run_pipeline(self):
        try:
            if run_state.start():
                p = get_pipeline()
                result = p.run(source="all")
                run_state.finish(result)
        except Exception as e:
            log.error("Scheduler run failed: %s", e)
            run_state.finish({"ok": False, "error": str(e)})

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "interval_minutes": self.interval_minutes,
                "next_run_time": self.next_run_time,
            }

    def stop(self):
        with self._lock:
            self.enabled = False
            self._stop_event.set()
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self.next_run_time = None


scheduler_state = SchedulerState()

# ---------------------------------------------------------------------------
# Helpers for article list view
# ---------------------------------------------------------------------------

def _compute_abstract(article: dict) -> str:
    texts = [b["text"].strip() for b in article.get("blocks", [])
             if b.get("type") != "img" and b.get("text")]
    return re.sub(r"\s+", " ", " ".join(texts))[:180]


def _compute_cover(article: dict) -> str:
    # TechFlow: use cover_src directly
    if article.get("cover_src"):
        return article["cover_src"]
    # STCN or fallback: first img block
    for b in article.get("blocks", []):
        if b.get("type") == "img" and b.get("src"):
            return b["src"]
    return ""


def _enrich_article(article: dict) -> dict:
    article["abstract"] = _compute_abstract(article)
    article["cover_image"] = _compute_cover(article)
    return article


# ---------------------------------------------------------------------------
# Request / Response models (Pydantic)
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    source: str = Field(default="all", pattern="^(stcn|techflow|all)$")
    dry_run: bool = False
    skip_fetch: bool = False


class RefetchRequest(BaseModel):
    source: str = Field(default="stcn", pattern="^(stcn|techflow|all)$")
    stcn_urls: list[str] = []
    techflow_ids: list[str] = []


class SchedulerRequest(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=60, ge=1, le=1440)


class CreateArticleRequest(BaseModel):
    title: str = Field(..., min_length=1)
    source_key: str = Field(pattern="^(stcn|techflow)$")
    blocks: list[dict] = []
    cover_src: str = ""
    abstract: str = ""
    author: str = ""
    source: str = ""
    original_url: str = ""


class UpdateArticleRequest(BaseModel):
    title: str | None = None
    blocks: list[dict] | None = None
    cover_src: str | None = None
    abstract: str | None = None
    author: str | None = None


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
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status():
    """Pipeline status and config info."""
    p = get_pipeline()
    state = p.load_state()
    return {
        **run_state.status(),
        "total_published": len(state.get("published_ids", [])),
        "last_updated": state.get("updated_at"),
        "scheduler": scheduler_state.status(),
        "sources": {
            "stcn": {"enabled": p.stcn_cfg.get("enabled", True), "authors": list(p.allowed_authors)},
            "techflow": {"enabled": p.techflow_cfg.get("enabled", True)},
        },
    }


@app.get("/api/state")
def get_state():
    """Full dedup state."""
    return get_pipeline().load_state()


@app.get("/api/articles")
def list_articles(
    source: str = Query("all", pattern="^(stcn|techflow|all)$"),
    limit: int = Query(50, ge=1, le=200),
):
    """List articles from local storage."""
    p = get_pipeline()
    state = p.load_state()
    published_ids = set(state.get("published_ids", []))
    articles = p.load_articles(source)[:limit]
    for a in articles:
        a["published"] = a["article_id"] in published_ids
        _enrich_article(a)
    return {"total": len(articles), "articles": articles}


@app.get("/api/articles/{article_id}")
def get_article(article_id: str):
    """Get a single article's detail."""
    p = get_pipeline()
    state = p.load_state()
    published_ids = set(state.get("published_ids", []))
    for source in ["stcn", "techflow"]:
        for a in p.load_articles(source):
            if a["article_id"] == article_id:
                a["published"] = a["article_id"] in published_ids
                _enrich_article(a)
                return a
    raise HTTPException(404, f"Article {article_id} not found")


@app.post("/api/run")
def trigger_run(req: RunRequest):
    """Trigger a pipeline run (background thread)."""
    if not run_state.start():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            p = get_pipeline()
            result = p.run(source=req.source, dry_run=req.dry_run, skip_fetch=req.skip_fetch)
            run_state.finish(result)
        except Exception as e:
            run_state.finish({"ok": False, "error": str(e)})
            log.error("Pipeline run failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Pipeline run started"}


@app.post("/api/refetch")
def refetch(req: RefetchRequest):
    """Refetch specific articles."""
    if not run_state.start():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            p = get_pipeline()
            result = p.run(
                source=req.source,
                refetch_stcn_urls=req.stcn_urls or None,
                refetch_techflow_ids=req.techflow_ids or None,
            )
            run_state.finish(result)
        except Exception as e:
            run_state.finish({"ok": False, "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Refetch started"}


@app.get("/api/logs")
def get_logs(lines: int = Query(100, ge=1, le=1000)):
    """Read recent log lines."""
    p = get_pipeline()
    log_path = BASE_DIR / p.cfg["paths"]["log_file"]
    if not log_path.exists():
        return {"lines": []}
    text = log_path.read_text(encoding="utf-8")
    all_lines = text.strip().split("\n")
    return {"lines": all_lines[-lines:]}


@app.delete("/api/state/{article_id}")
def remove_from_state(article_id: str):
    """Remove an article ID from the dedup state (allow republish)."""
    p = get_pipeline()
    state = p.load_state()
    ids = state.get("published_ids", [])
    if article_id in ids:
        ids.remove(article_id)
        state["published_ids"] = ids
        p.save_state(state)
        return {"ok": True, "removed": article_id}
    raise HTTPException(404, f"{article_id} not in state")


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

import json as _json

def _find_article_file(article_id: str):
    """Find the file path for a given article_id like 'stcn:3708814' or 'techflow:30884'."""
    p = get_pipeline()
    for source in ["stcn", "techflow"]:
        for a in p.load_articles(source):
            if a["article_id"] == article_id:
                return a.get("path"), a.get("source_key")
    return None, None


def _save_article_file(article: dict):
    """Save article as JSON to the appropriate directory."""
    p = get_pipeline()
    if article.get("source_key") == "stcn":
        p.stcn_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article["article_id"].replace("stcn:", ""))
        path = p.stcn_dir / f"stcn_{raw_id}.json"
    else:
        p.techflow_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article["article_id"].replace("techflow:", ""))
        path = p.techflow_dir / f"techflow_{raw_id}.json"
    data = {
        "article_id": raw_id,
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "author": article.get("author", ""),
        "publish_time": article.get("publish_time", ""),
        "original_url": article.get("original_url", ""),
        "cover_src": article.get("cover_src", ""),
        "blocks": article.get("blocks", []),
    }
    path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@app.post("/api/articles")
def create_article(req: CreateArticleRequest):
    """Create a new article manually."""
    import time as _time
    raw_id = f"manual_{int(_time.time() * 1000)}"
    article = {
        "source_key": req.source_key,
        "article_id": f"{req.source_key}:{raw_id}",
        "raw_id": raw_id,
        "title": req.title,
        "author": req.author or "",
        "source": req.source or ("券商中国" if req.source_key == "stcn" else "深潮 TechFlow"),
        "publish_time": time.strftime("%Y-%m-%d %H:%M"),
        "original_url": req.original_url or "",
        "cover_src": req.cover_src or "",
        "blocks": req.blocks or [],
    }
    path = _save_article_file(article)
    _enrich_article(article)
    return {"ok": True, "article": article, "path": path}


@app.put("/api/articles/{article_id}")
def update_article(article_id: str, req: UpdateArticleRequest):
    """Update an existing article."""
    file_path, source_key = _find_article_file(article_id)
    if not file_path:
        raise HTTPException(404, f"Article {article_id} not found")
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(404, f"File {file_path} not found")

    # Read current article data
    p = get_pipeline()
    if source_key == "stcn":
        article = p.parse_stcn_article_file(path)
    else:
        article = Pipeline.parse_techflow_article_file(path)

    # Merge updates
    if req.title is not None:
        article["title"] = req.title
    if req.blocks is not None:
        article["blocks"] = req.blocks
    if req.cover_src is not None:
        article["cover_src"] = req.cover_src
    if req.abstract is not None:
        article["abstract"] = req.abstract
    if req.author is not None:
        article["author"] = req.author

    # Save as JSON (STCN articles will now use JSON too)
    _save_article_file(article)
    _enrich_article(article)
    return {"ok": True, "article": article}


@app.delete("/api/articles/{article_id}")
def delete_article(article_id: str):
    """Delete an article file and remove from published state."""
    file_path, source_key = _find_article_file(article_id)
    if not file_path:
        raise HTTPException(404, f"Article {article_id} not found")
    path = Path(file_path)
    if path.exists():
        path.unlink()

    # Remove from published state if present
    p = get_pipeline()
    state = p.load_state()
    ids = state.get("published_ids", [])
    if article_id in ids:
        ids.remove(article_id)
        state["published_ids"] = ids
        p.save_state(state)

    return {"ok": True, "deleted": article_id}


@app.get("/api/scheduler")
def get_scheduler():
    """Get scheduler status."""
    return scheduler_state.status()


@app.post("/api/scheduler")
def update_scheduler(req: SchedulerRequest):
    """Update scheduler config."""
    scheduler_state.set_config(req.enabled, req.interval_minutes)
    return scheduler_state.status()


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
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
