# -*- coding: utf-8 -*-
"""Base scraper abstract class with memory-efficient operations."""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

try:
    import portalocker
except ImportError:
    portalocker = None

log = logging.getLogger("pipeline")


def _file_write_with_lock(path: Path, content: str) -> None:
    """Write file with portalocker lock if available, otherwise atomic write."""
    if portalocker is not None:
        # Use portalocker for cross-platform file locking
        with portalocker.Lock(str(path), timeout=5, mode='w'):
            path.write_text(content, encoding="utf-8")
    else:
        # Fallback: atomic write via temporary file
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)  # Atomic rename


class BaseScraper(ABC):
    """Abstract base for source-specific scrapers.

    Each scraper handles one article source (STCN, TechFlow, BlockBeats).

    Memory optimization:
    - Uses iterators instead of loading all articles at once
    - Lazy loading of article files
    """

    source_key: str = ""

    def __init__(self, cfg: dict, session, output_dir: Path, db=None):
        self.cfg = cfg
        self.session = session
        self.output_dir = output_dir
        self.db = db  # Optional database instance for LLM features
        self._article_cache: dict[Path, dict] = {}  # File cache

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
    def _article_id_from_path(self, path: Path) -> str | None:
        """Extract article_id from file path. Override in subclass."""

    # -- File writing with lock (protected method for subclasses) --

    def _write_file_with_lock(self, path: Path, content: str) -> None:
        """Write file with portalocker lock (cross-platform) or atomic fallback.

        Subclasses should use this in their save() method instead of path.write_text().

        Args:
            path: Target file path
            content: Content to write (typically JSON string)
        """
        try:
            import portalocker
            # Use portalocker for cross-platform file locking
            # LOCK_EX = exclusive lock, timeout = 5 seconds
            with portalocker.Lock(str(path), timeout=5, mode='w'):
                path.write_text(content, encoding="utf-8")
        except ImportError:
            # Fallback: atomic write via temporary file
            # This prevents data corruption but doesn't prevent concurrent writes
            tmp_path = path.with_suffix('.tmp')
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)  # Atomic rename

    # -- Optimized article loading with iterator --

    def load_articles(self, limit: int = 0) -> list[dict]:
        """Load saved articles. Backward compat - wraps iterator."""
        return list(self._iter_articles(limit=limit))

    def _iter_articles(self, limit: int = 0, use_cache: bool = True) -> Iterator[dict]:
        """Iterate over articles without loading all into memory.

        Args:
            limit: Max articles to yield (0 = no limit)
            use_cache: Use in-memory cache for parsed articles
        """
        if not self.output_dir.exists():
            return

        count = 0
        for json_file in sorted(self.output_dir.glob("*.json"), reverse=True):
            if limit and count >= limit:
                break

            try:
                if use_cache and json_file in self._article_cache:
                    article = self._article_cache[json_file]
                else:
                    article = self.parse_article_file(json_file)
                    article["path"] = str(json_file)
                    article["source_key"] = self.source_key
                    if use_cache:
                        self._article_cache[json_file] = article

                yield article
                count += 1
            except Exception as e:
                log.warning("Failed to load %s: %s", json_file.name, e)

    def _find_article_path(self, article_id: str) -> Path | None:
        """Find article file by ID without loading all files."""
        # Try pattern match first (faster than loading)
        for pattern in self._get_id_patterns(article_id):
            matches = list(self.output_dir.glob(pattern))
            if matches:
                return matches[0]
        return None

    def _get_id_patterns(self, article_id: str) -> list[str]:
        """Get possible filename patterns for an article_id. Override as needed."""
        # Default: try direct match and common prefixes
        base_id = article_id.split(":")[-1]  # Remove source prefix if present
        return [f"{base_id}.json", f"{self.source_key}_{base_id}.json", f"article_{base_id}.json"]

    def clear_cache(self):
        """Clear article file cache."""
        self._article_cache.clear()

    # -- Legacy methods for compat --

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        """Build a list-item dict from a URL (for refetch). Default: raise."""
        raise NotImplementedError(f"{self.source_key} does not support URL-based refetch")

    def fetch_html(self, url: str, timeout: int = 30) -> str:
        """Fetch HTML with timeout."""
        r = self.session.get(url, timeout=timeout)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
