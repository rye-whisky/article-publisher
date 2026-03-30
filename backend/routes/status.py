# -*- coding: utf-8 -*-
"""Status and state API routes."""

from fastapi import APIRouter

from services.pipeline_service import get_pipeline, run_state

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def get_status():
    """Pipeline status and config info."""
    p = get_pipeline()
    state = p.load_state()
    return {
        "running": run_state.running,
        "last_result": run_state.result,
        "started_at": run_state.started_at,
        "total_published": len(state.get("published_ids", [])),
        "last_updated": state.get("updated_at"),
        "sources": {
            "stcn": {"enabled": p.stcn_cfg.get("enabled", True), "authors": list(p.allowed_authors)},
            "techflow": {"enabled": p.techflow_cfg.get("enabled", True)},
        },
    }


@router.get("/state")
def get_state():
    """Full dedup state."""
    return get_pipeline().load_state()
