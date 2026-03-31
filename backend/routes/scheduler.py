# -*- coding: utf-8 -*-
"""Scheduler API routes."""

from fastapi import APIRouter, Request

from models.schemas import SchedulerRequest

router = APIRouter(prefix="/api", tags=["scheduler"])


@router.get("/scheduler")
def get_scheduler(request: Request):
    """Get scheduler status."""
    svc = request.app.state.pipeline_service
    return svc.scheduler_state.status()


@router.post("/scheduler")
def update_scheduler(request: Request, req: SchedulerRequest):
    """Update scheduler config."""
    svc = request.app.state.pipeline_service
    svc.enable_scheduler(req.enabled, req.interval_minutes)
    return svc.scheduler_state.status()
