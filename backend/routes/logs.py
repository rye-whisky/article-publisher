# -*- coding: utf-8 -*-
"""Logs API route."""

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
def get_logs(request: Request, lines: int = Query(100, ge=1, le=1000)):
    """Read recent log lines."""
    svc = request.app.state.pipeline_service
    return {"lines": svc.read_logs(lines)}


@router.get("/logs/stream")
async def stream_logs(request: Request):
    """SSE endpoint: pushes new log lines in real-time."""
    broadcaster = request.app.state.log_broadcaster
    q = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
