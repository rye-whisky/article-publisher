# -*- coding: utf-8 -*-
"""Base scraper abstract class."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path

log = logging.getLogger("pipeline")


class BaseScraper(ABC):
    """Abstract base for source-specific scrapers.

    Each scraper handles one article source (STCN, TechFlow, BlockBeats).
    """

    source_key: str = ""

    def __init__(self, cfg: dict, session, output_dir: Path):
        self.cfg = cfg
        self.session = session
        self.output_dir = output_dir

    @abstractmethod
    def parse_list(self) -> list[dict]:
        """Scrape the listing page and return article metadata items."""

    @abstractmethod
    def fetch_detail(self, item: dict) -> dict:
        """Fetch full article content for a single list item."""

    @abstractmethod
    def save(self, article: dict) -> Path:
        """Persist article to disk. Returns the file path."""

    @abstractmethod
    def parse_article_file(self, path: Path) -> dict:
        """Parse a saved article file into a standard article dict."""

    @abstractmethod
    def load_articles(self) -> list[dict]:
        """Load all saved articles for this source."""

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        """Build a list-item dict from a URL (for refetch). Default: raise."""
        raise NotImplementedError(f"{self.source_key} does not support URL-based refetch")

    def fetch_html(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
