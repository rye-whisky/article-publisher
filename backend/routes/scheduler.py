# -*- coding: utf-8 -*-
"""Per-source scheduler API routes."""

from fastapi import APIRouter, Request

from models.schemas import SourceScheduleRequest

router = APIRouter(prefix="/api", tags=["scheduler"])


@router.get("/schedules")
def get_schedules(request: Request):
    """Get all sources' schedule status."""
    svc = request.app.state.pipeline_service
    return {"schedules": svc.get_source_schedules()}


@router.put("/schedules/{source_key}")
def update_schedule(request: Request, source_key: str, req: SourceScheduleRequest):
    """Update a source's schedule config."""
    svc = request.app.state.pipeline_service
    svc.set_source_schedule(source_key, req.enabled, req.interval_minutes)
    return {"schedules": svc.get_source_schedules()}
