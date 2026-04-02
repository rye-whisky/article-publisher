# -*- coding: utf-8 -*-
"""SQLite database service for article storage.

Lightweight, serverless database for local development and small deployments.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

log = logging.getLogger("pipeline")


class ArticleDatabase:
    """SQLite database for storing articles.

    Thread-safe local database using SQLite.
    Automatically creates tables and handles migrations.
    """

    def __init__(self, db_path: str | Path = "data/articles.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """Create tables if not exist."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL UNIQUE,
                source_key TEXT NOT NULL,
                raw_id TEXT,
                title TEXT NOT NULL,
                source TEXT,
                author TEXT,
                publish_time TEXT,
                original_url TEXT,
                cover_src TEXT,
                blocks TEXT,
                abstract TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                published_at TEXT,
                cms_id TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_article_id ON articles(article_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_key ON articles(source_key)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_time ON articles(publish_time DESC)
        """)
        conn.commit()

    # -- CRUD operations --

    def insert_or_update(self, article: dict) -> int:
        """Insert article or update if exists. Returns row id."""
        conn = self._get_conn()
        article_id = article.get("article_id", "")
        raw_id = article.get("raw_id", article_id.split(":")[-1] if ":" in article_id else article_id)
        blocks_json = json.dumps(article.get("blocks", []), ensure_ascii=False)
        abstract = self._compute_abstract(article)

        now = datetime.now().isoformat()

        conn.execute("""
            INSERT INTO articles (
                article_id, source_key, raw_id, title, source, author,
                publish_time, original_url, cover_src, blocks, abstract, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                title = excluded.title,
                author = excluded.author,
                publish_time = excluded.publish_time,
                original_url = excluded.original_url,
                cover_src = excluded.cover_src,
                blocks = excluded.blocks,
                abstract = excluded.abstract,
                updated_at = excluded.updated_at
        """, (
            article_id,
            article.get("source_key", ""),
            raw_id,
            article.get("title", ""),
            article.get("source", ""),
            article.get("author", ""),
            article.get("publish_time", ""),
            article.get("original_url", ""),
            article.get("cover_src", ""),
            blocks_json,
            abstract,
            now,
            now
        ))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_by_article_id(self, article_id: str) -> Optional[dict]:
        """Get article by article_id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM articles WHERE article_id = ?",
            (article_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_articles(
        self,
        source_key: str = "all",
        limit: int = 100,
        offset: int = 0,
        unpublished_only: bool = False
    ) -> List[dict]:
        """List articles with filters."""
        conn = self._get_conn()
        query = "SELECT * FROM articles WHERE 1=1"
        params = []

        if source_key != "all":
            query += " AND source_key = ?"
            params.append(source_key)

        if unpublished_only:
            query += " AND cms_id IS NULL"

        query += " ORDER BY publish_time DESC, created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def count_articles(self, source_key: str = "all", unpublished_only: bool = False) -> int:
        """Count articles with filters."""
        conn = self._get_conn()
        query = "SELECT COUNT(*) FROM articles WHERE 1=1"
        params = []

        if source_key != "all":
            query += " AND source_key = ?"
            params.append(source_key)

        if unpublished_only:
            query += " AND cms_id IS NULL"

        return conn.execute(query, params).fetchone()[0]

    def mark_published(self, article_id: str, cms_id: str) -> bool:
        """Mark article as published with CMS ID."""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE articles SET cms_id = ?, published_at = ?
            WHERE article_id = ?
        """, (cms_id, datetime.now().isoformat(), article_id))
        conn.commit()
        return cursor.rowcount > 0

    def delete(self, article_id: str) -> bool:
        """Delete article by article_id."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM articles WHERE article_id = ?", (article_id,))
        conn.commit()
        return cursor.rowcount > 0

    def get_published_ids(self) -> List[str]:
        """Get all published article IDs."""
        conn = self._get_conn()
        rows = conn.execute("SELECT article_id FROM articles WHERE cms_id IS NOT NULL").fetchall()
        return [row["article_id"] for row in rows]

    # -- Stats and utilities --

    def get_stats(self) -> dict:
        """Get database statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        published = conn.execute("SELECT COUNT(*) FROM articles WHERE cms_id IS NOT NULL").fetchone()[0]

        # Count by source
        sources = {}
        for row in conn.execute("SELECT source_key, COUNT(*) as cnt FROM articles GROUP BY source_key"):
            sources[row["source_key"]] = row["cnt"]

        return {
            "total_articles": total,
            "published_articles": published,
            "unpublished_articles": total - published,
            "by_source": sources,
        }

    # -- Helpers --

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert database row to article dict."""
        article = {
            "id": row["id"],
            "article_id": row["article_id"],
            "source_key": row["source_key"],
            "raw_id": row["raw_id"],
            "title": row["title"],
            "source": row["source"],
            "author": row["author"],
            "publish_time": row["publish_time"],
            "original_url": row["original_url"],
            "cover_src": row["cover_src"],
            "abstract": row["abstract"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
            "cms_id": row["cms_id"],
        }
        # Parse blocks JSON
        if row["blocks"]:
            try:
                article["blocks"] = json.loads(row["blocks"])
            except json.JSONDecodeError:
                article["blocks"] = []
        else:
            article["blocks"] = []
        return article

    @staticmethod
    def _compute_abstract(article: dict) -> str:
        """Generate abstract from blocks."""
        import re
        texts = [
            b.get("text", "").strip()
            for b in article.get("blocks", [])
            if b.get("type") != "img" and b.get("text")
        ]
        return re.sub(r"\s+", " ", " ".join(texts))[:180]

    def close(self):
        """Close database connection."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            delattr(self._local, "conn")


# Singleton instance
_db_instance: Optional[ArticleDatabase] = None
_db_lock = threading.Lock()


def get_database(db_path: str | Path = None) -> ArticleDatabase:
    """Get or create database singleton instance."""
    global _db_instance
    with _db_lock:
        if _db_instance is None:
            _db_instance = ArticleDatabase(db_path or "data/articles.db")
        return _db_instance


def close_database():
    """Close database connection."""
    global _db_instance
    with _db_lock:
        if _db_instance is not None:
            _db_instance.close()
            _db_instance = None
