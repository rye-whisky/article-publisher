# -*- coding: utf-8 -*-
"""Configuration loader with environment variable expansion."""

import os
import re
from pathlib import Path
from functools import lru_cache

import yaml


def _expand_env(value):
    """Replace ${VAR} / $$VAR patterns with environment variables."""
    if not isinstance(value, str):
        return value
    return re.sub(
        r'\$\{(\w+)\}|\$\$(\w+)',
        lambda m: os.environ.get(m.group(1) or m.group(2), ''),
        value,
    )


def _expand_recursive(obj):
    """Recursively expand env vars in config values."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_recursive(v) for v in obj]
    return obj


def load_config(config_path: str | Path) -> dict:
    """Load YAML config and expand environment variables.

    Args:
        config_path: Path to config.yaml

    Returns:
        Parsed and expanded config dict.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    return _expand_recursive(raw)
