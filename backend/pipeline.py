#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backward-compatible re-export shim.

The original Pipeline class and setup_logging are now in the layered structure.
This file keeps legacy imports working.
"""

from services.pipeline_service import PipelineService as Pipeline
from utils.logging_config import setup_logging
from services.pipeline_service import PipelineService


def get_pipeline():
    """Legacy helper: return a PipelineService via create()."""
    from pathlib import Path
    return PipelineService.create(Path(__file__).resolve().parent.parent)
