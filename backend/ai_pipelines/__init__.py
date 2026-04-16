# -*- coding: utf-8 -*-
"""AI pipeline scraper registry and factory."""

from pathlib import Path
from typing import Optional

import requests


def create_ai_scrapers(cfg: dict, session: requests.Session, base_dir: Path) -> dict:
    """Create AI article scrapers. Returns {source_key: scraper}."""
    from .baoyu import BaoyuScraper
    from .bestblogs import BestBlogsScraper
    from .claude import ClaudeScraper
    from .kr36 import Kr36Scraper
    from .qbitai import QbitaiScraper
    from .aiera import AieraScraper
    from .aibase import AibaseScraper

    scrapers = {}
    ai_cfg = cfg.get("ai_sources", {})
    paths_cfg = cfg.get("paths", {})
    output_dir = base_dir / paths_cfg.get("ai_articles_output", "output/ai_articles")

    # BestBlogs (disabled by default)
    if ai_cfg.get("bestblogs", {}).get("enabled", False):
        scrapers["bestblogs"] = BestBlogsScraper(
            ai_cfg.get("bestblogs", {}), session, output_dir
        )

    # 36氪 AI 资讯
    if ai_cfg.get("kr36", {}).get("enabled", True):
        scrapers["kr36"] = Kr36Scraper(
            ai_cfg.get("kr36", {}), session, output_dir
        )

    # 宝玉的分享
    if ai_cfg.get("baoyu", {}).get("enabled", True):
        scrapers["baoyu"] = BaoyuScraper(
            ai_cfg.get("baoyu", {}), session, output_dir
        )

    # Claude 官方博客
    if ai_cfg.get("claude", {}).get("enabled", True):
        scrapers["claude"] = ClaudeScraper(
            ai_cfg.get("claude", {}), session, output_dir
        )

    # 量子位 AI 资讯
    if ai_cfg.get("qbitai", {}).get("enabled", True):
        scrapers["qbitai"] = QbitaiScraper(
            ai_cfg.get("qbitai", {}), session, output_dir
        )

    # 新智元 AI 资讯
    if ai_cfg.get("aiera", {}).get("enabled", True):
        scrapers["aiera"] = AieraScraper(
            ai_cfg.get("aiera", {}), session, output_dir
        )

    # AIBase AI 资讯
    if ai_cfg.get("aibase", {}).get("enabled", True):
        scrapers["aibase"] = AibaseScraper(
            ai_cfg.get("aibase", {}), session, output_dir
        )

    return scrapers
