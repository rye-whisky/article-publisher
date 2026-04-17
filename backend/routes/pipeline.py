# -*- coding: utf-8 -*-
"""Pipeline run / refetch / cancel API routes."""

import threading

from fastapi import APIRouter, HTTPException, Request, Depends
from routes.auth import require_admin

from models.schemas import RunRequest, RefetchRequest

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.post("/run")
def trigger_run(request: Request, req: RunRequest, _admin=Depends(require_admin)):
    """Trigger a pipeline run (background thread)."""
    svc = request.app.state.pipeline_service
    if not svc.run_state.start():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            result = svc.run(
                source=req.source,
                dry_run=req.dry_run,
                skip_fetch=req.skip_fetch,
                since_today_0700=req.since_today_0700,
            )
            svc.run_state.finish(result)
        except Exception as e:
            svc.run_state.finish({"ok": False, "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Pipeline run started"}


@router.post("/refetch")
def refetch(request: Request, req: RefetchRequest, _admin=Depends(require_admin)):
    """Refetch specific articles by URL or ID."""
    svc = request.app.state.pipeline_service
    if not svc.run_state.start():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            result = svc.run(
                source=req.source,
                refetch_stcn_urls=req.stcn_urls or None,
                refetch_techflow_ids=req.techflow_ids or None,
                refetch_blockbeats_urls=req.blockbeats_urls or None,
                refetch_chaincatcher_urls=req.chaincatcher_urls or None,
                refetch_odaily_urls=req.odaily_urls or None,
                republish_refetched=req.republish,
            )
            svc.run_state.finish(result)
        except Exception as e:
            svc.run_state.finish({"ok": False, "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Refetch started"}


@router.post("/cancel")
def cancel_run(request: Request, _admin=Depends(require_admin)):
    """Cancel a running pipeline. Force-reset if it doesn't respond to cancellation."""
    svc = request.app.state.pipeline_service
    if not svc.run_state.running:
        raise HTTPException(409, "No pipeline is running")

    svc.run_state.cancel()
    return {"ok": True, "message": "Cancellation requested"}


@router.post("/force-reset")
def force_reset(request: Request, _admin=Depends(require_admin)):
    """Force-reset a stuck run state (emergency use)."""
    svc = request.app.state.pipeline_service
    was_running = svc.run_state.running
    svc.run_state.finish({"ok": False, "error": "force-reset by admin"})
    return {"ok": True, "message": f"Run state reset (was_running={was_running})"}
