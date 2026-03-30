# -*- coding: utf-8 -*-
"""Logging configuration for the pipeline."""

import logging
import sys
from pathlib import Path


def setup_logging(base_dir: Path = None, log_file: str = "logs/pipeline.log"):
    """Configure logging with file + stdout handlers."""
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

    log.setLevel(logging.DEBUG)
    log.addHandler(fh)
    log.addHandler(sh)
