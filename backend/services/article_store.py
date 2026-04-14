# -*- coding: utf-8 -*-
"""Article store: file I/O + CRUD for articles across all sources.

Memory-efficient implementation with caching and lazy loading.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("pipeline")


class ArticleStore:
    """Manages article file I/O and CRUD operations with memory optimization."""

    def __init__(self, scrapers: dict):
        self.scrapers = scrapers  # {source_key: BaseScraper}
        self._cache: dict[str, tuple[dict, Path]] = {}  # article_id -> (article, path)
        self._cache_enabled = True
        self._max_cache_size = 100  # Limit cache for 2GB server

    def clear_cache(self):
        """Clear the article cache."""
        self._cache.clear()

    # -- Read operations (with caching) --

    def list_articles(self, source: str = "all", limit: int = 200, use_cache: bool = True) -> list[dict]:
        """List articles with optional limit. Distributes evenly across sources."""
        if source != "all":
            scraper = self.scrapers.get(source)
            if not scraper:
                return []
            return list(scraper._iter_articles(limit=limit, use_cache=use_cache))

        # "all": collect from each source fairly
        per_source = max(limit // max(len(self.scrapers), 1), 5)
        articles = []
        for scraper in self.scrapers.values():
            articles.extend(scraper._iter_articles(limit=per_source, use_cache=use_cache))
        return articles

    def list_articles_paged(self, source: str = "all", page: int = 1, page_size: int = 20) -> tuple[int, list[dict]]:
        """List articles with pagination. Returns (total_count, page_of_articles)."""
        if source != "all":
            scraper = self.scrapers.get(source)
            if not scraper:
                return 0, []
            all_articles = list(scraper._iter_articles(limit=0))
        else:
            all_articles = []
            for scraper in self.scrapers.values():
                all_articles.extend(scraper._iter_articles(limit=0))
        all_articles.sort(key=lambda a: a.get("publish_time", ""), reverse=True)

        total = len(all_articles)
        start = (page - 1) * page_size
        return total, all_articles[start:start + page_size]

    def get_article(self, article_id: str) -> Optional[dict]:
        """Get article by ID with cache lookup."""
        if self._cache_enabled and article_id in self._cache:
            return self._cache[article_id][0]

        for key, scraper in self.scrapers.items():
            path = scraper._find_article_path(article_id)
            if path and path.exists():
                article = scraper.parse_article_file(path)
                article["path"] = str(path)
                article["source_key"] = key
                if self._cache_enabled:
                    self._cache_article(article_id, article, path)
                return article

        return None

    def find_article_file(self, article_id: str):
        """Return (path, source_key) or (None, None) with cache."""
        if self._cache_enabled and article_id in self._cache:
            _, path = self._cache[article_id]
            return str(path), self._cache[article_id][0].get("source_key")

        for key, scraper in self.scrapers.items():
            path = scraper._find_article_path(article_id)
            if path and path.exists():
                return str(path), key
        return None, None

    def _cache_article(self, article_id: str, article: dict, path: Path):
        """Cache an article with FIFO eviction."""
        if len(self._cache) >= self._max_cache_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[article_id] = (article, path)

    # -- Write operations --

    def create_article(self, article: dict) -> str:
        """Save a new article. Returns the file path."""
        source_key = article.get("source_key", "stcn")
        scraper = self.scrapers.get(source_key)
        if not scraper:
            raise ValueError(f"Unknown source_key: {source_key}")
        path = scraper.save(article)
        return str(path)

    def update_article(self, article_id: str, updates: dict) -> Optional[dict]:
        """Update an existing article's fields."""
        file_path, source_key = self.find_article_file(article_id)
        if not file_path:
            return None
        path = Path(file_path)
        if not path.exists():
            return None

        scraper = self.scrapers.get(source_key)
        if not scraper:
            return None
        article = scraper.parse_article_file(path)

        for key in ("title", "blocks", "cover_src", "abstract", "author"):
            if key in updates and updates[key] is not None:
                article[key] = updates[key]

        self._save_as_json(article, path)
        return article

    def delete_article(self, article_id: str) -> bool:
        """Delete article file. Returns True if found and deleted."""
        file_path, _ = self.find_article_file(article_id)
        if not file_path:
            return False
        path = Path(file_path)
        if path.exists():
            path.unlink()
            # Invalidate cache
            self._cache.pop(article_id, None)
            return True
        return False

    # -- Helpers --

    @staticmethod
    def _save_as_json(article: dict, path: Path):
        data = {
            "article_id": article.get("raw_id", article.get("article_id", "")),
            "title": article.get("title", ""),
            "source": article.get("source", ""),
            "author": article.get("author", ""),
            "publish_time": article.get("publish_time", ""),
            "original_url": article.get("original_url", ""),
            "cover_src": article.get("cover_src", ""),
            "blocks": article.get("blocks", []),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def enrich_article(article: dict) -> dict:
        """Add computed fields (abstract, cover_image) if not present."""
        if "abstract" not in article or not article["abstract"]:
            article["abstract"] = _compute_abstract(article)
        if "cover_image" not in article or not article["cover_image"]:
            article["cover_image"] = _compute_cover(article)
        return article


def _compute_abstract(article: dict) -> str:
    texts = [b["text"].strip() for b in article.get("blocks", []) if b.get("type") != "img" and b.get("text")]
    return re.sub(r"\s+", " ", " ".join(texts))[:180]


def _compute_cover(article: dict) -> str:
    if article.get("cover_src"):
        return article["cover_src"]
    for b in article.get("blocks", []):
        if b.get("type") == "img" and b.get("src"):
            return b["src"]
    return ""
