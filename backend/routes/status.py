# -*- coding: utf-8 -*-
"""Status and state API routes."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def get_status(request: Request):
    """Pipeline status and config info."""
    svc = request.app.state.pipeline_service
    state = svc.load_state()
    sources = {}
    for key, scraper in svc.scrapers.items():
        src_cfg = svc.cfg.get("sources", {}).get(key, {})
        info = {"enabled": src_cfg.get("enabled", True)}
        if key == "stcn":
            info["authors"] = list(scraper.allowed_authors)
        sources[key] = info
    return {
        **svc.run_state.status(),
        "total_published": len(state.get("published_ids", [])),
        "last_updated": state.get("updated_at"),
        "schedules": svc.get_source_schedules(),
        "sources": sources,
    }


@router.get("/state")
def get_state(request: Request):
    """Full dedup state."""
    return request.app.state.pipeline_service.load_state()
