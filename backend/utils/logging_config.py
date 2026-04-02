# -*- coding: utf-8 -*-
"""Logging configuration with rotation for low-memory servers."""

import logging
import sys
from pathlib import Path

from utils.log_broadcaster import LogBroadcaster
from utils.log_rotation import setup_log_rotation, cleanup_old_logs

_broadcaster = None


def setup_logging(
    base_dir: Path = None,
    log_file: str = "logs/pipeline.log",
    enable_rotation: bool = True,
):
    """Configure logging with rotating file handler for memory safety.

    Args:
        base_dir: Project base directory
        log_file: Relative path to log file
        enable_rotation: Use rotating file handler (2MB max, 3 backups)
    """
    global _broadcaster

    log = logging.getLogger("pipeline")

    # Avoid duplicate handlers on repeated calls
    if log.handlers:
        return

    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    log_path = base_dir / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Cleanup old logs if needed
    cleanup_old_logs(log_path.parent, max_total_size_mb=10, keep_latest=5)

    fmt = logging.Formatter("{asctime} [{levelname}] {message}", style="{")

    # Use rotating handler for memory safety
    if enable_rotation:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(
            str(log_path),
            maxBytes=2 * 1024 * 1024,  # 2MB per file
            backupCount=3,               # Keep 3 backups
            encoding="utf-8",
        )
    else:
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

    # Suppress noisy uvicorn access logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_broadcaster():
    return _broadcaster
