# -*- coding: utf-8 -*-
"""SQLite database service for article storage.

Lightweight, serverless database for local development and small deployments.
"""

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

log = logging.getLogger("pipeline")


def _sanitize_for_gbk(text: str) -> str:
    """Remove or replace characters that can't be encoded in GBK."""
    if not isinstance(text, str):
        return text
    # Remove zero-width characters and other problematic Unicode
    # \u200b = zero-width space, \u200c = zero-width non-joiner, \u200d = zero-width joiner
    # \u200e = left-to-right mark, \u200f = right-to-left mark
    # \ufeff = zero-width no-break space (BOM)
    problematic = '\u200b\u200c\u200d\u200e\u200f\ufeff'
    for char in problematic:
        text = text.replace(char, '')
    return text


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
                timeout=30.0,
                isolation_level=None
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

        # Users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrate: add role column if missing (existing databases)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        except Exception:
            pass  # Column already exists

        # Migrate: AI article fields
        for col_sql in [
            "ALTER TABLE articles ADD COLUMN score INTEGER",
            "ALTER TABLE articles ADD COLUMN tags TEXT",
            "ALTER TABLE articles ADD COLUMN category TEXT",
            "ALTER TABLE articles ADD COLUMN language TEXT DEFAULT 'zh'",
            "ALTER TABLE articles ADD COLUMN one_sentence_summary TEXT",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON articles(score DESC)")

        # Settings table (key-value)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

    # -- CRUD operations --

    def insert_or_update(self, article: dict) -> int:
        """Insert article or update if exists. Returns row id."""
        conn = self._get_conn()
        article_id = article.get("article_id", "")
        raw_id = article.get("raw_id", article_id.split(":")[-1] if ":" in article_id else article_id)

        # Sanitize blocks content BEFORE creating JSON (fix GBK encoding error)
        blocks = article.get("blocks", [])
        sanitized_blocks = []
        for block in blocks:
            sanitized_block = dict(block)
            if "text" in sanitized_block:
                sanitized_block["text"] = _sanitize_for_gbk(sanitized_block["text"])
            if "src" in sanitized_block:
                sanitized_block["src"] = _sanitize_for_gbk(sanitized_block["src"])
            sanitized_blocks.append(sanitized_block)
        blocks_json = json.dumps(sanitized_blocks, ensure_ascii=True)

        abstract = article.get("abstract") or self._compute_abstract(article)

        # Sanitize tags before JSON encoding
        tags = article.get("tags", [])
        sanitized_tags = [_sanitize_for_gbk(str(tag)) for tag in tags] if tags else []
        tags_json = json.dumps(sanitized_tags, ensure_ascii=True) if sanitized_tags else None

        now = datetime.now().isoformat()

        # Sanitize text fields for GBK compatibility (Windows encoding issue)
        title = _sanitize_for_gbk(article.get("title", ""))
        source = _sanitize_for_gbk(article.get("source", ""))
        author = _sanitize_for_gbk(article.get("author", ""))
        publish_time = _sanitize_for_gbk(article.get("publish_time", ""))
        original_url = _sanitize_for_gbk(article.get("original_url", ""))
        cover_src = _sanitize_for_gbk(article.get("cover_src", ""))
        abstract = _sanitize_for_gbk(abstract)
        one_sentence_summary = _sanitize_for_gbk(article.get("one_sentence_summary", ""))
        category = _sanitize_for_gbk(article.get("category", ""))
        language = _sanitize_for_gbk(article.get("language", "zh"))

        conn.execute("""
            INSERT INTO articles (
                article_id, source_key, raw_id, title, source, author,
                publish_time, original_url, cover_src, blocks, abstract,
                score, tags, category, language, one_sentence_summary,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                title = excluded.title,
                author = excluded.author,
                publish_time = excluded.publish_time,
                original_url = excluded.original_url,
                cover_src = excluded.cover_src,
                blocks = excluded.blocks,
                abstract = excluded.abstract,
                score = excluded.score,
                tags = excluded.tags,
                category = excluded.category,
                language = excluded.language,
                one_sentence_summary = excluded.one_sentence_summary,
                updated_at = excluded.updated_at
        """, (
            article_id,
            article.get("source_key", ""),
            raw_id,
            title,
            source,
            author,
            publish_time,
            original_url,
            cover_src,
            blocks_json,
            abstract,
            article.get("score"),
            tags_json,
            category,
            language,
            one_sentence_summary,
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

    def update_abstract(self, article_id: str, abstract: str) -> bool:
        """Update the abstract field for an article."""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE articles SET abstract = ?, updated_at = ? WHERE article_id = ?",
            (abstract, datetime.now().isoformat(), article_id),
        )
        conn.commit()
        return cursor.rowcount > 0

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

    def cleanup_old(self, days: int = 7) -> int:
        """Delete articles older than N days. Returns number of deleted rows."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM articles WHERE created_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount

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
        # AI article fields (may not exist in older rows)
        try:
            article["score"] = row["score"]
        except (IndexError, KeyError):
            article["score"] = None
        try:
            tags_str = row["tags"]
            article["tags"] = json.loads(tags_str) if tags_str else []
        except (IndexError, KeyError):
            article["tags"] = []
        try:
            article["category"] = row["category"]
        except (IndexError, KeyError):
            article["category"] = None
        try:
            article["language"] = row["language"]
        except (IndexError, KeyError):
            article["language"] = "zh"
        try:
            article["one_sentence_summary"] = row["one_sentence_summary"]
        except (IndexError, KeyError):
            article["one_sentence_summary"] = None
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

    # -- User operations --

    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def seed_user(self, username: str, password: str, role: str = "admin"):
        """Insert default user if no users exist."""
        conn = self._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (username, self._hash_password(password), role, now, now),
            )
            conn.commit()

    def seed_guest_user(self, username: str = "guest", password: str = "guest"):
        """Insert guest user if not exists."""
        conn = self._get_conn()
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (username, self._hash_password(password), "guest", now, now),
            )
            conn.commit()

    def get_user_by_username(self, username: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "username": row["username"], "password_hash": row["password_hash"],
                "role": row["role"] if "role" in row.keys() else "admin",
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "username": row["username"], "password_hash": row["password_hash"],
                "role": row["role"] if "role" in row.keys() else "admin",
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def verify_user_password(self, username: str, password: str) -> bool:
        user = self.get_user_by_username(username)
        if not user:
            return False
        return user["password_hash"] == self._hash_password(password)

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        if not self.verify_user_password(username, old_password):
            return False
        conn = self._get_conn()
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (self._hash_password(new_password), datetime.now().isoformat(), username),
        )
        conn.commit()
        return True

    def update_username(self, old_username: str, new_username: str) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE users SET username = ?, updated_at = ? WHERE username = ?",
                (new_username, datetime.now().isoformat(), old_username),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            return False

    # -- Settings operations --

    def get_setting(self, key: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_all_settings(self) -> Dict[str, str]:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set_setting(self, key: str, value: str):
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )
        conn.commit()

    def set_settings_batch(self, items: Dict[str, str]):
        conn = self._get_conn()
        now = datetime.now().isoformat()
        for key, value in items.items():
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, now),
            )
        conn.commit()

    def close(self):
        """Close database connection."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            delattr(self._local, "conn")

    # -- Schedule persistence (for per-source auto-scheduling) --

    SCHEDULE_PREFIX = "schedule_"

    def save_schedule(self, source_key: str, enabled: bool, interval_minutes: int):
        """Save schedule config for a source."""
        key = f"{self.SCHEDULE_PREFIX}{source_key}"
        value = json.dumps({"enabled": enabled, "interval_minutes": interval_minutes})
        self.set_setting(key, value)

    def get_schedule(self, source_key: str) -> dict | None:
        """Get schedule config for a source. Returns None if not set."""
        key = f"{self.SCHEDULE_PREFIX}{source_key}"
        value = self.get_setting(key)
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def get_all_schedules(self) -> dict[str, dict]:
        """Get all schedule configs. Returns {source_key: {enabled, interval_minutes}}."""
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE ?", (f"{self.SCHEDULE_PREFIX}%",)).fetchall()
        result = {}
        for row in rows:
            source_key = row["key"][len(self.SCHEDULE_PREFIX):]
            try:
                result[source_key] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
        return result

    def delete_schedule(self, source_key: str):
        """Delete schedule config for a source."""
        key = f"{self.SCHEDULE_PREFIX}{source_key}"
        conn = self._get_conn()
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


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
