# -*- coding: utf-8 -*-
"""Pipeline scraper registry and factory."""

from pathlib import Path
from typing import Optional

import requests


def create_scrapers(cfg: dict, session: requests.Session, base_dir: Path, db=None) -> dict[str, "BaseScraper"]:
    """Create all configured scrapers. Returns {source_key: scraper}."""
    from .stcn import StcnScraper
    from .techflow import TechFlowScraper
    from .blockbeats import BlockBeatsScraper
    from .chaincatcher import ChainCatcherScraper
    from .odaily import OdailyScraper

    scrapers = {}
    sources_cfg = cfg.get("sources") or {}
    paths_cfg = cfg.get("paths") or {}

    # STCN
    if "stcn" in sources_cfg:
        stcn_dir = base_dir / paths_cfg.get("stcn_output", "output/stcn_articles")
        scrapers["stcn"] = StcnScraper(sources_cfg["stcn"], session, stcn_dir, db)

    # TechFlow
    if "techflow" in sources_cfg:
        tf_dir = base_dir / paths_cfg.get("techflow_output", "output/techflow_articles")
        scrapers["techflow"] = TechFlowScraper(sources_cfg["techflow"], session, tf_dir, db)

    # BlockBeats
    if "blockbeats" in sources_cfg:
        bb_dir = base_dir / paths_cfg.get("blockbeats_output", "output/blockbeats_articles")
        scrapers["blockbeats"] = BlockBeatsScraper(sources_cfg["blockbeats"], session, bb_dir, db)

    # ChainCatcher
    if "chaincatcher" in sources_cfg:
        cc_dir = base_dir / paths_cfg.get("chaincatcher_output", "output/chaincatcher_articles")
        scrapers["chaincatcher"] = ChainCatcherScraper(sources_cfg["chaincatcher"], session, cc_dir, db)

    # Odaily
    if "odaily" in sources_cfg:
        odaily_dir = base_dir / paths_cfg.get("odaily_output", "output/odaily_articles")
        scrapers["odaily"] = OdailyScraper(sources_cfg["odaily"], session, odaily_dir, db)

    return scrapers
