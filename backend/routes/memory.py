# -*- coding: utf-8 -*-
"""Memory monitoring and cleanup route for low-memory servers."""

import logging
import psutil
import os
from fastapi import APIRouter

log = logging.getLogger("pipeline")

memory_router = APIRouter(prefix="/api/memory", tags=["memory"])


def get_memory_info() -> dict:
    """Get current memory usage info."""
    process = psutil.Process(os.getpid())
    mem = process.memory_info()
    sys_mem = psutil.virtual_memory()

    return {
        "process": {
            "rss_mb": round(mem.rss / 1024 / 1024, 2),
            "vms_mb": round(mem.vms / 1024 / 1024, 2),
        },
        "system": {
            "total_mb": round(sys_mem.total / 1024 / 1024, 2),
            "available_mb": round(sys_mem.available / 1024 / 1024, 2),
            "percent": sys_mem.percent,
        },
    }


@memory_router.get("/info")
async def get_memory_status():
    """Get current memory usage."""
    return get_memory_info()


@memory_router.post("/clear")
async def clear_memory_cache(request):
    """Clear all in-memory caches to free RAM."""
    svc = request.app.state.pipeline_service
    svc.clear_caches()

    import gc
    gc.collect()

    return {"ok": True, "memory": get_memory_info()}
