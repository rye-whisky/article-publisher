# -*- coding: utf-8 -*-
"""Logs API route."""

from fastapi import APIRouter, Query

from services.pipeline_service import read_logs

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
def get_logs(lines: int = Query(100, ge=1, le=1000)):
    """Read recent log lines."""
    return {"lines": read_logs(lines)}
