# -*- coding: utf-8 -*-
"""Logging configuration for the pipeline."""

import logging
import sys
from pathlib import Path

from utils.log_broadcaster import LogBroadcaster

_broadcaster = None


def setup_logging(base_dir: Path = None, log_file: str = "logs/pipeline.log"):
    """Configure logging with file + stdout handlers."""
    global _broadcaster

    log = logging.getLogger("pipeline")

    # Avoid duplicate handlers on repeated calls
    if log.handlers:
        return

    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    log_path = base_dir / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("{asctime} [{levelname}] {message}", style="{")

    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)

    _broadcaster = LogBroadcaster()
    _broadcaster.setFormatter(fmt)

    log.setLevel(logging.DEBUG)
    log.addHandler(fh)
    log.addHandler(sh)
    log.addHandler(_broadcaster)

    # Suppress noisy uvicorn access logs (e.g. repeated /api/logs polling)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_broadcaster():
    return _broadcaster
