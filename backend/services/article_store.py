# -*- coding: utf-8 -*-
"""Article store: file I/O + CRUD for articles across all sources."""

import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("pipeline")


class ArticleStore:
    """Manages article file I/O and CRUD operations."""

    def __init__(self, scrapers: dict):
        self.scrapers = scrapers  # {source_key: BaseScraper}

    # -- Read operations --

    def list_articles(self, source: str = "all", limit: int = 50) -> list[dict]:
        articles = []
        for key, scraper in self.scrapers.items():
            if source in (key, "all"):
                articles.extend(scraper.load_articles())
        return articles[:limit]

    def get_article(self, article_id: str) -> Optional[dict]:
        for key, scraper in self.scrapers.items():
            for a in scraper.load_articles():
                if a["article_id"] == article_id:
                    return a
        return None

    def find_article_file(self, article_id: str):
        """Return (path, source_key) or (None, None)."""
        for key, scraper in self.scrapers.items():
            for a in scraper.load_articles():
                if a["article_id"] == article_id:
                    return a.get("path"), a.get("source_key")
        return None, None

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

        # Merge updates
        for key in ("title", "blocks", "cover_src", "abstract", "author"):
            if key in updates and updates[key] is not None:
                article[key] = updates[key]

        # Save back as JSON
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
        article["abstract"] = _compute_abstract(article)
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
