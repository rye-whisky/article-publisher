# -*- coding: utf-8 -*-
"""Log rotation utilities for low-memory servers."""

import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_log_rotation(
    log_path: Path,
    max_bytes: int = 2 * 1024 * 1024,  # 2MB per file
    backup_count: int = 3,               # Keep 3 backup files (~8MB total)
):
    """Setup rotating file handler with size limits.

    For 2GB RAM server:
    - Max 2MB per log file
    - Max 3 backup files
    - Total: ~8MB for logs
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    return handler


def cleanup_old_logs(
    log_dir: Path,
    max_total_size_mb: int = 10,
    keep_latest: int = 5,
):
    """Clean up old log files if total size exceeds limit.

    Args:
        log_dir: Directory containing log files
        max_total_size_mb: Maximum total size in MB
        keep_latest: Always keep at least N latest files
    """
    if not log_dir.exists():
        return

    log_files = sorted(
        [f for f in log_dir.glob("*.log*") if f.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    # Keep latest N files
    to_keep = set(log_files[:keep_latest])

    # Check total size
    total_size = sum(f.stat().st_size for f in log_files)
    max_size = max_total_size_mb * 1024 * 1024

    if total_size <= max_size:
        return

    # Delete oldest files beyond size limit
    for f in log_files[keep_latest:]:
        if f not in to_keep:
            try:
                f.unlink()
                logging.info("Deleted old log file: %s", f.name)
            except Exception as e:
                logging.warning("Failed to delete %s: %s", f.name, e)
