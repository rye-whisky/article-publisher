# -*- coding: utf-8 -*-
"""Pipeline scraper registry and factory."""

from pathlib import Path
from typing import Optional

import requests


def create_scrapers(cfg: dict, session: requests.Session, base_dir: Path) -> dict[str, "BaseScraper"]:
    """Create all configured scrapers. Returns {source_key: scraper}."""
    from .stcn import StcnScraper
    from .techflow import TechFlowScraper
    from .blockbeats import BlockBeatsScraper

    scrapers = {}
    sources_cfg = cfg.get("sources", {})
    paths_cfg = cfg.get("paths", {})

    # STCN
    if "stcn" in sources_cfg:
        stcn_dir = base_dir / paths_cfg.get("stcn_output", "output/stcn_articles")
        scrapers["stcn"] = StcnScraper(sources_cfg["stcn"], session, stcn_dir)

    # TechFlow
    if "techflow" in sources_cfg:
        tf_dir = base_dir / paths_cfg.get("techflow_output", "output/techflow_articles")
        scrapers["techflow"] = TechFlowScraper(sources_cfg["techflow"], session, tf_dir)

    # BlockBeats
    if "blockbeats" in sources_cfg:
        bb_dir = base_dir / paths_cfg.get("blockbeats_output", "output/blockbeats_articles")
        scrapers["blockbeats"] = BlockBeatsScraper(sources_cfg["blockbeats"], session, bb_dir)

    return scrapers
