# -*- coding: utf-8 -*-
"""Pipeline run / refetch API routes."""

import threading
from fastapi import APIRouter, HTTPException

from models.schemas import RunRequest, RefetchRequest
from services.pipeline_service import run_pipeline, run_state

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.post("/run")
def trigger_run(req: RunRequest):
    """Trigger a pipeline run (background thread)."""
    if not run_state.start():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            result = run_pipeline(
                source=req.source,
                dry_run=req.dry_run,
                skip_fetch=req.skip_fetch,
                since_today_0700=req.since_today_0700,
            )
            run_state.finish(result)
        except Exception as e:
            run_state.finish({"ok": False, "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Pipeline run started"}


@router.post("/refetch")
def refetch(req: RefetchRequest):
    """Refetch specific articles by URL or ID."""
    if not run_state.start():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            result = run_pipeline(
                source=req.source,
                refetch_stcn_urls=req.stcn_urls or None,
                refetch_techflow_ids=req.techflow_ids or None,
            )
            run_state.finish(result)
        except Exception as e:
            run_state.finish({"ok": False, "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Refetch started"}
