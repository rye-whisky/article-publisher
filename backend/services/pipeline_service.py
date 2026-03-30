# -*- coding: utf-8 -*-
"""Pipeline service — thin async wrapper around the core Pipeline class.

Provides a thread-safe singleton for the pipeline instance and exposes
high-level operations used by the API routes.
"""

import logging
import threading
import time
from typing import Optional

from pipeline import Pipeline

log = logging.getLogger("pipeline")

# Module-level singleton
_pipeline: Optional[Pipeline] = None
_pipeline_lock = threading.Lock()


def get_pipeline() -> Pipeline:
    """Return the singleton Pipeline instance (lazy init)."""
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                _pipeline = Pipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# Background runner state
# ---------------------------------------------------------------------------

class RunState:
    """Thread-safe tracker for background pipeline runs."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.started_at: Optional[str] = None
        self.result: Optional[dict] = None

    def start(self) -> bool:
        """Try to mark as running. Returns False if already running."""
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
            "started_at": self.started_at,
            "last_result": self.result,
        }


run_state = RunState()


# ---------------------------------------------------------------------------
# Service operations
# ---------------------------------------------------------------------------

def run_pipeline(
    source: str = "all",
    dry_run: bool = False,
    skip_fetch: bool = False,
    since_today_0700: bool = False,
    refetch_stcn_urls: Optional[list[str]] = None,
    refetch_techflow_ids: Optional[list[str]] = None,
    republish_ids: Optional[list[str]] = None,
) -> dict:
    """Run the pipeline synchronously (call from thread)."""
    p = get_pipeline()
    return p.run(
        source=source,
        dry_run=dry_run,
        skip_fetch=skip_fetch,
        since_today_0700=since_today_0700,
        refetch_stcn_urls=refetch_stcn_urls,
        refetch_techflow_ids=refetch_techflow_ids,
        republish_ids=republish_ids,
    )


def list_articles(source: str = "all", limit: int = 50) -> list[dict]:
    """Load articles from local storage."""
    p = get_pipeline()
    state = p.load_state()
    published_ids = set(state.get("published_ids", []))
    articles = p.load_articles(source)[:limit]
    for a in articles:
        a["published"] = a["article_id"] in published_ids
    return articles


def get_article(article_id: str) -> Optional[dict]:
    """Find a single article by ID."""
    p = get_pipeline()
    state = p.load_state()
    published_ids = set(state.get("published_ids", []))
    for source in ("stcn", "techflow"):
        for a in p.load_articles(source):
            if a["article_id"] == article_id:
                a["published"] = a["article_id"] in published_ids
                return a
    return None


def remove_published_id(article_id: str) -> bool:
    """Remove an article ID from the dedup state."""
    p = get_pipeline()
    state = p.load_state()
    ids = state.get("published_ids", [])
    if article_id in ids:
        ids.remove(article_id)
        state["published_ids"] = ids
        p.save_state(state)
        return True
    return False


def get_state() -> dict:
    """Return the full dedup state."""
    return get_pipeline().load_state()


def read_logs(lines: int = 100) -> list[str]:
    """Read recent log lines."""
    p = get_pipeline()
    log_path = p.base_dir / p.cfg["paths"]["log_file"]
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8")
    all_lines = text.strip().split("\n")
    return all_lines[-lines:]
