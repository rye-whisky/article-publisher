# -*- coding: utf-8 -*-
"""Workflow routes: blocklist CRUD and auto-publish status."""

from fastapi import APIRouter, Depends, HTTPException, Request

from routes.auth import require_admin

router = APIRouter(prefix="/api", tags=["workflow"])


@router.get("/blocklist")
def list_blocklist(request: Request):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    return {"rules": svc.database.list_blocklist_rules()}


@router.post("/blocklist")
async def create_blocklist_rule(request: Request, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    body = await request.json()
    rule_id = svc.database.create_blocklist_rule(body)
    return {"ok": True, "id": rule_id}


@router.put("/blocklist/{rule_id}")
async def update_blocklist_rule(request: Request, rule_id: int, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    body = await request.json()
    ok = svc.database.update_blocklist_rule(rule_id, body)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


@router.delete("/blocklist/{rule_id}")
def delete_blocklist_rule(request: Request, rule_id: int, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    ok = svc.database.delete_blocklist_rule(rule_id)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


@router.get("/workflow/status")
def get_workflow_status(request: Request):
    svc = request.app.state.pipeline_service
    return svc.get_workflow_status()


@router.post("/workflow/push-check")
def run_push_check(request: Request, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.auto_publish_scheduler:
        raise HTTPException(501, "Auto-publish scheduler not configured")
    return svc.auto_publish_scheduler.run_once()


@router.post("/workflow/broadcast-check")
def run_broadcast_check(request: Request, _admin=Depends(require_admin)):
    """Trigger a single auto-publish + broadcast cycle manually (alias for push-check)."""
    svc = request.app.state.pipeline_service
    if not svc.auto_publish_scheduler:
        raise HTTPException(501, "Auto-publish scheduler not configured")
    return svc.auto_publish_scheduler.run_once()
