# -*- coding: utf-8 -*-
"""SQLite database service for article storage and workflow metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("pipeline")


def _sanitize_for_gbk(text: str) -> str:
    """Remove or replace characters that can't be encoded in GBK."""
    if not isinstance(text, str):
        return text
    problematic = "\u200b\u200c\u200d\u200e\u200f\ufeff"
    for char in problematic:
        text = text.replace(char, "")
    return text


class ArticleDatabase:
    """Thread-safe SQLite database used by both blockchain and AI pipelines."""

    SCHEDULE_PREFIX = "schedule_"
    PUBLIC_STAGES = ("published", "broadcasted")

    def __init__(self, db_path: str | Path = "data/articles.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    # ------------------------------------------------------------------
    # Connection and schema
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0,
                isolation_level=None,
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """Create tables if missing and apply light migrations."""
        conn = self._get_conn()

        conn.execute(
            """
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
                score INTEGER,
                tags TEXT,
                category TEXT,
                language TEXT DEFAULT 'zh',
                one_sentence_summary TEXT,
                filter_status TEXT DEFAULT 'passed',
                filter_reason TEXT,
                duplicate_key TEXT,
                score_status TEXT DEFAULT 'pending',
                score_reason TEXT,
                review_status TEXT DEFAULT 'manual_review',
                auto_publish_enabled INTEGER DEFAULT 0,
                publish_stage TEXT DEFAULT 'local',
                published_strategy TEXT,
                scored_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                published_at TEXT,
                cms_id TEXT,
                broadcasted_at TEXT,
                broadcast_strategy TEXT
            )
        """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocklist_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                match_type TEXT NOT NULL DEFAULT 'keyword',
                field TEXT NOT NULL DEFAULT 'title',
                action TEXT NOT NULL DEFAULT 'block',
                source_key TEXT,
                penalty_score INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                sort_order INTEGER NOT NULL DEFAULT 100,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL,
                source_key TEXT,
                score INTEGER,
                cms_id TEXT,
                pushed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                window_start TEXT,
                strategy TEXT DEFAULT 'auto'
            )
        """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_push_window ON push_history(window_start)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_push_article_strategy ON push_history(article_id, strategy)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL,
                source_key TEXT,
                cms_id TEXT,
                score INTEGER,
                push_title TEXT,
                pushed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT DEFAULT 'manual',
                result TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_article ON broadcast_history(article_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_strategy ON broadcast_history(strategy)")

        for col_sql in [
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'",
            "ALTER TABLE articles ADD COLUMN score INTEGER",
            "ALTER TABLE articles ADD COLUMN tags TEXT",
            "ALTER TABLE articles ADD COLUMN category TEXT",
            "ALTER TABLE articles ADD COLUMN language TEXT DEFAULT 'zh'",
            "ALTER TABLE articles ADD COLUMN one_sentence_summary TEXT",
            "ALTER TABLE articles ADD COLUMN filter_status TEXT DEFAULT 'passed'",
            "ALTER TABLE articles ADD COLUMN filter_reason TEXT",
            "ALTER TABLE articles ADD COLUMN duplicate_key TEXT",
            "ALTER TABLE articles ADD COLUMN score_status TEXT DEFAULT 'pending'",
            "ALTER TABLE articles ADD COLUMN score_reason TEXT",
            "ALTER TABLE articles ADD COLUMN review_status TEXT DEFAULT 'manual_review'",
            "ALTER TABLE articles ADD COLUMN auto_publish_enabled INTEGER DEFAULT 0",
            "ALTER TABLE articles ADD COLUMN publish_stage TEXT DEFAULT 'local'",
            "ALTER TABLE articles ADD COLUMN published_strategy TEXT",
            "ALTER TABLE articles ADD COLUMN scored_at TEXT",
            "ALTER TABLE articles ADD COLUMN broadcasted_at TEXT",
            "ALTER TABLE articles ADD COLUMN broadcast_strategy TEXT",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass

        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_article_id ON articles(article_id)",
            "CREATE INDEX IF NOT EXISTS idx_source_key ON articles(source_key)",
            "CREATE INDEX IF NOT EXISTS idx_publish_time ON articles(publish_time DESC)",
            "CREATE INDEX IF NOT EXISTS idx_score ON articles(score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_duplicate_key ON articles(duplicate_key)",
            "CREATE INDEX IF NOT EXISTS idx_review_status ON articles(review_status)",
            "CREATE INDEX IF NOT EXISTS idx_filter_status ON articles(filter_status)",
        ]:
            conn.execute(sql)

        self._migrate_article_ids_to_namespaced(conn)
        self._migrate_publish_stages(conn)
        conn.commit()

    def _migrate_article_ids_to_namespaced(self, conn: sqlite3.Connection):
        """Upgrade legacy rows with raw article ids to `source:raw_id` format."""
        try:
            conn.execute(
                """
                UPDATE articles
                SET raw_id = CASE
                    WHEN raw_id IS NULL OR raw_id = '' THEN article_id
                    ELSE raw_id
                END
                WHERE raw_id IS NULL OR raw_id = ''
                """
            )

            rows = conn.execute(
                """
                SELECT id, article_id, raw_id, source_key
                FROM articles
                WHERE instr(article_id, ':') = 0
                """
            ).fetchall()
            for row in rows:
                raw_id = row["raw_id"] or row["article_id"]
                source_key = row["source_key"] or ""
                if not raw_id or not source_key:
                    continue
                new_id = f"{source_key}:{raw_id}"
                conflict = conn.execute(
                    "SELECT id FROM articles WHERE article_id = ? AND id != ?",
                    (new_id, row["id"]),
                ).fetchone()
                if conflict:
                    log.warning(
                        "Database migration: duplicate namespaced article_id %s, dropping legacy row id=%s",
                        new_id,
                        row["id"],
                    )
                    conn.execute("DELETE FROM articles WHERE id = ?", (row["id"],))
                    continue
                conn.execute(
                    "UPDATE articles SET article_id = ?, raw_id = ?, updated_at = ? WHERE id = ?",
                    (new_id, raw_id, datetime.now().isoformat(), row["id"]),
                )
        except Exception as exc:
            log.warning("Database migration for article ids failed: %s", exc)

    def _migrate_publish_stages(self, conn: sqlite3.Connection):
        """Backfill publish stages for older rows.

        Historical rows with `cms_id` were previously all CMS drafts, because the old
        publisher only submitted with `is_public = false`. Preserve that behavior by
        marking those rows as draft rather than published, and clear the incorrectly
        populated public publish timestamp.
        """
        try:
            conn.execute(
                """
                UPDATE articles
                SET publish_stage = CASE
                        WHEN cms_id IS NOT NULL AND cms_id != '' THEN 'draft'
                        ELSE 'local'
                    END,
                    published_at = CASE
                        WHEN cms_id IS NOT NULL AND cms_id != '' THEN NULL
                        ELSE published_at
                    END,
                    review_status = CASE
                        WHEN cms_id IS NOT NULL AND cms_id != '' AND review_status = 'published' AND published_strategy = 'auto' THEN 'auto_candidate'
                        WHEN cms_id IS NOT NULL AND cms_id != '' AND review_status = 'published' THEN 'manual_review'
                        ELSE review_status
                    END
                WHERE publish_stage IS NULL OR publish_stage = ''
                """
            )
        except Exception as exc:
            log.warning("Database migration for publish stages failed: %s", exc)

    # ------------------------------------------------------------------
    # Article helpers and CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_article_id(source_key: str, article_id: str = "", raw_id: str = "") -> str:
        if article_id and ":" in article_id:
            return article_id
        source_key = (source_key or "").strip()
        raw = (raw_id or article_id or "").strip()
        if source_key and raw:
            return f"{source_key}:{raw}"
        return raw

    @staticmethod
    def _article_order_by(sort_by: str = "time") -> str:
        """Build a safe ORDER BY clause for article list queries."""
        if sort_by == "score":
            return (
                "CASE WHEN score IS NULL THEN 1 ELSE 0 END ASC, "
                "score DESC, "
                "COALESCE(NULLIF(publish_time, ''), created_at) DESC, "
                "created_at DESC"
            )
        return "COALESCE(NULLIF(publish_time, ''), created_at) DESC, created_at DESC"

    @staticmethod
    def _normalize_article_payload(article: dict) -> dict:
        normalized = dict(article or {})
        source_key = normalized.get("source_key", "")
        article_id = normalized.get("article_id", "")
        raw_id = normalized.get("raw_id", "")

        if not source_key and article_id and ":" in article_id:
            source_key = article_id.split(":", 1)[0]
            normalized["source_key"] = source_key
        if not raw_id:
            raw_id = article_id.split(":", 1)[-1] if article_id else ""
            normalized["raw_id"] = raw_id

        normalized["article_id"] = ArticleDatabase._canonical_article_id(source_key, article_id, raw_id)
        return normalized

    def insert_or_update(self, article: dict) -> int:
        """Insert article or update if it already exists. Returns row id."""
        conn = self._get_conn()
        article = self._normalize_article_payload(article)
        article_id = article.get("article_id", "")
        raw_id = article.get("raw_id", article_id.split(":")[-1] if ":" in article_id else article_id)

        blocks = article.get("blocks", [])
        sanitized_blocks = []
        for block in blocks:
            sanitized_block = dict(block)
            if "text" in sanitized_block:
                sanitized_block["text"] = _sanitize_for_gbk(sanitized_block["text"])
            if "src" in sanitized_block:
                sanitized_block["src"] = _sanitize_for_gbk(sanitized_block["src"])
            if "alt" in sanitized_block:
                sanitized_block["alt"] = _sanitize_for_gbk(sanitized_block["alt"])
            sanitized_blocks.append(sanitized_block)
        blocks_json = json.dumps(sanitized_blocks, ensure_ascii=True)

        abstract = article.get("abstract") or self._compute_abstract(article)
        tags = article.get("tags", [])
        sanitized_tags = [_sanitize_for_gbk(str(tag)) for tag in tags] if tags else []
        tags_json = json.dumps(sanitized_tags, ensure_ascii=True) if sanitized_tags else None
        now = datetime.now().isoformat()

        payload = {
            "article_id": article_id,
            "source_key": article.get("source_key", ""),
            "raw_id": raw_id,
            "title": _sanitize_for_gbk(article.get("title", "")),
            "source": _sanitize_for_gbk(article.get("source", "")),
            "author": _sanitize_for_gbk(article.get("author", "")),
            "publish_time": _sanitize_for_gbk(article.get("publish_time", "")),
            "original_url": _sanitize_for_gbk(article.get("original_url", "")),
            "cover_src": _sanitize_for_gbk(article.get("cover_src", "")),
            "blocks": blocks_json,
            "abstract": _sanitize_for_gbk(abstract),
            "score": article.get("score"),
            "tags": tags_json,
            "category": _sanitize_for_gbk(article.get("category", "")),
            "language": _sanitize_for_gbk(article.get("language", "zh")),
            "one_sentence_summary": _sanitize_for_gbk(article.get("one_sentence_summary", "")),
            "filter_status": article.get("filter_status", "passed"),
            "filter_reason": _sanitize_for_gbk(article.get("filter_reason", "")),
            "duplicate_key": article.get("duplicate_key"),
            "score_status": article.get("score_status", "pending"),
            "score_reason": _sanitize_for_gbk(article.get("score_reason", "")),
            "review_status": article.get("review_status", "manual_review"),
            "auto_publish_enabled": 1 if article.get("auto_publish_enabled") else 0,
            "publish_stage": article.get("publish_stage"),
            "published_strategy": article.get("published_strategy"),
            "scored_at": article.get("scored_at"),
            "created_at": article.get("created_at", now),
            "updated_at": now,
            "published_at": article.get("published_at"),
            "cms_id": article.get("cms_id"),
        }

        conn.execute(
            """
            INSERT INTO articles (
                article_id, source_key, raw_id, title, source, author,
                publish_time, original_url, cover_src, blocks, abstract,
                score, tags, category, language, one_sentence_summary,
                filter_status, filter_reason, duplicate_key,
                score_status, score_reason, review_status,
                auto_publish_enabled, publish_stage, published_strategy, scored_at,
                created_at, updated_at, published_at, cms_id
            ) VALUES (
                :article_id, :source_key, :raw_id, :title, :source, :author,
                :publish_time, :original_url, :cover_src, :blocks, :abstract,
                :score, :tags, :category, :language, :one_sentence_summary,
                :filter_status, :filter_reason, :duplicate_key,
                :score_status, :score_reason, :review_status,
                :auto_publish_enabled, COALESCE(:publish_stage, 'local'), :published_strategy, :scored_at,
                :created_at, :updated_at, :published_at, :cms_id
            )
            ON CONFLICT(article_id) DO UPDATE SET
                source_key = excluded.source_key,
                raw_id = excluded.raw_id,
                title = excluded.title,
                source = excluded.source,
                author = excluded.author,
                publish_time = excluded.publish_time,
                original_url = excluded.original_url,
                cover_src = excluded.cover_src,
                blocks = excluded.blocks,
                abstract = excluded.abstract,
                score = COALESCE(excluded.score, articles.score),
                tags = COALESCE(excluded.tags, articles.tags),
                category = COALESCE(excluded.category, articles.category),
                language = COALESCE(excluded.language, articles.language),
                one_sentence_summary = COALESCE(excluded.one_sentence_summary, articles.one_sentence_summary),
                filter_status = COALESCE(excluded.filter_status, articles.filter_status),
                filter_reason = COALESCE(excluded.filter_reason, articles.filter_reason),
                duplicate_key = COALESCE(excluded.duplicate_key, articles.duplicate_key),
                score_status = COALESCE(excluded.score_status, articles.score_status),
                score_reason = COALESCE(excluded.score_reason, articles.score_reason),
                review_status = COALESCE(excluded.review_status, articles.review_status),
                auto_publish_enabled = COALESCE(excluded.auto_publish_enabled, articles.auto_publish_enabled),
                publish_stage = CASE
                    WHEN :publish_stage IS NOT NULL THEN :publish_stage
                    ELSE articles.publish_stage
                END,
                published_strategy = COALESCE(excluded.published_strategy, articles.published_strategy),
                scored_at = COALESCE(excluded.scored_at, articles.scored_at),
                updated_at = excluded.updated_at,
                published_at = COALESCE(excluded.published_at, articles.published_at),
                cms_id = COALESCE(excluded.cms_id, articles.cms_id)
            """,
            payload,
        )
        conn.commit()
        row = conn.execute("SELECT id FROM articles WHERE article_id = ?", (article_id,)).fetchone()
        return row["id"] if row else 0

    def get_by_article_id(self, article_id: str) -> Optional[dict]:
        """Get a single article by canonical article_id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM articles WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        if row:
            return self._row_to_dict(row)

        if article_id and ":" not in article_id:
            matches = conn.execute(
                "SELECT * FROM articles WHERE raw_id = ? ORDER BY updated_at DESC",
                (article_id,),
            ).fetchall()
            if len(matches) == 1:
                return self._row_to_dict(matches[0])
        return None

    def list_articles(
        self,
        source_key: str = "all",
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "time",
        unpublished_only: bool = False,
        review_status: str | None = None,
        filter_status: str | None = None,
        include_source_keys: list[str] | None = None,
        exclude_source_keys: list[str] | None = None,
    ) -> List[dict]:
        """List articles with optional filters."""
        conn = self._get_conn()
        where = ["1=1"]
        params: list[Any] = []

        if source_key != "all":
            where.append("source_key = ?")
            params.append(source_key)

        if include_source_keys:
            placeholders = ", ".join("?" for _ in include_source_keys)
            where.append(f"source_key IN ({placeholders})")
            params.extend(include_source_keys)

        if exclude_source_keys:
            placeholders = ", ".join("?" for _ in exclude_source_keys)
            where.append(f"source_key NOT IN ({placeholders})")
            params.extend(exclude_source_keys)

        if unpublished_only:
            where.append("COALESCE(publish_stage, 'local') NOT IN ('published', 'broadcasted')")

        if review_status:
            where.append("review_status = ?")
            params.append(review_status)

        if filter_status:
            where.append("filter_status = ?")
            params.append(filter_status)

        order_by = self._article_order_by(sort_by)
        query = f"""
            SELECT * FROM articles
            WHERE {' AND '.join(where)}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def count_articles(
        self,
        source_key: str = "all",
        unpublished_only: bool = False,
        review_status: str | None = None,
        filter_status: str | None = None,
        include_source_keys: list[str] | None = None,
        exclude_source_keys: list[str] | None = None,
    ) -> int:
        """Count articles with optional filters."""
        conn = self._get_conn()
        where = ["1=1"]
        params: list[Any] = []

        if source_key != "all":
            where.append("source_key = ?")
            params.append(source_key)

        if include_source_keys:
            placeholders = ", ".join("?" for _ in include_source_keys)
            where.append(f"source_key IN ({placeholders})")
            params.extend(include_source_keys)

        if exclude_source_keys:
            placeholders = ", ".join("?" for _ in exclude_source_keys)
            where.append(f"source_key NOT IN ({placeholders})")
            params.extend(exclude_source_keys)

        if unpublished_only:
            where.append("COALESCE(publish_stage, 'local') NOT IN ('published', 'broadcasted')")

        if review_status:
            where.append("review_status = ?")
            params.append(review_status)

        if filter_status:
            where.append("filter_status = ?")
            params.append(filter_status)

        query = f"SELECT COUNT(*) FROM articles WHERE {' AND '.join(where)}"
        return conn.execute(query, params).fetchone()[0]

    def find_by_duplicate_key(
        self,
        duplicate_key: str,
        source_keys: list[str] | None = None,
        exclude_article_id: str | None = None,
    ) -> Optional[dict]:
        """Find an already stored article with the same normalized title key."""
        if not duplicate_key:
            return None
        conn = self._get_conn()
        where = ["duplicate_key = ?"]
        params: list[Any] = [duplicate_key]

        if source_keys:
            placeholders = ", ".join("?" for _ in source_keys)
            where.append(f"source_key IN ({placeholders})")
            params.extend(source_keys)

        if exclude_article_id:
            where.append("article_id != ?")
            params.append(exclude_article_id)

        query = f"""
            SELECT * FROM articles
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT 1
        """
        row = conn.execute(query, params).fetchone()
        return self._row_to_dict(row) if row else None

    def update_abstract(self, article_id: str, abstract: str) -> bool:
        """Update article abstract."""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE articles SET abstract = ?, updated_at = ? WHERE article_id = ?",
            (_sanitize_for_gbk(abstract), datetime.now().isoformat(), article_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_filter_result(
        self,
        article_id: str,
        filter_status: str,
        filter_reason: str = "",
        duplicate_key: str = "",
    ) -> bool:
        """Update article filtering status."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            UPDATE articles
            SET filter_status = ?, filter_reason = ?, duplicate_key = ?, updated_at = ?
            WHERE article_id = ?
            """,
            (
                filter_status,
                _sanitize_for_gbk(filter_reason),
                duplicate_key or None,
                datetime.now().isoformat(),
                article_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_scoring(
        self,
        article_id: str,
        score: int | None,
        score_reason: str = "",
        tags: list[str] | None = None,
        review_status: str | None = None,
        auto_publish_enabled: bool | None = None,
        score_status: str = "done",
    ) -> bool:
        """Persist scoring result and downstream review status."""
        conn = self._get_conn()
        tags_json = None
        if tags is not None:
            sanitized_tags = [_sanitize_for_gbk(str(tag)) for tag in tags]
            tags_json = json.dumps(sanitized_tags, ensure_ascii=True)

        current = self.get_by_article_id(article_id) or {}
        cursor = conn.execute(
            """
            UPDATE articles
            SET score = ?,
                tags = ?,
                score_reason = ?,
                score_status = ?,
                review_status = ?,
                auto_publish_enabled = ?,
                scored_at = ?,
                updated_at = ?
            WHERE article_id = ?
            """,
            (
                score,
                tags_json if tags is not None else current.get("tags_json"),
                _sanitize_for_gbk(score_reason),
                score_status,
                review_status or current.get("review_status", "manual_review"),
                int(auto_publish_enabled) if auto_publish_enabled is not None else int(bool(current.get("auto_publish_enabled"))),
                datetime.now().isoformat() if score_status == "done" else current.get("scored_at"),
                datetime.now().isoformat(),
                article_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_review_status(
        self,
        article_id: str,
        review_status: str,
        auto_publish_enabled: bool | None = None,
    ) -> bool:
        """Update review status without touching score."""
        conn = self._get_conn()
        current = self.get_by_article_id(article_id) or {}
        cursor = conn.execute(
            """
            UPDATE articles
            SET review_status = ?, auto_publish_enabled = ?, updated_at = ?
            WHERE article_id = ?
            """,
            (
                review_status,
                int(auto_publish_enabled) if auto_publish_enabled is not None else int(bool(current.get("auto_publish_enabled"))),
                datetime.now().isoformat(),
                article_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0

    def mark_cms_draft(self, article_id: str, cms_id: str, strategy: str = "manual") -> bool:
        """Mark article as saved to the CMS draft box."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        cursor = conn.execute(
            """
            UPDATE articles
            SET cms_id = ?, publish_stage = 'draft', published_strategy = ?, updated_at = ?
            WHERE article_id = ?
            """,
            (cms_id, strategy, now, article_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def mark_published(self, article_id: str, cms_id: str, strategy: str = "manual") -> bool:
        """Mark article as publicly published."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        cursor = conn.execute(
            """
            UPDATE articles
            SET cms_id = ?, publish_stage = 'published', published_at = ?, published_strategy = ?, updated_at = ?
            WHERE article_id = ?
            """,
            (cms_id, now, strategy, now, article_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def mark_broadcasted(self, article_id: str, strategy: str = "manual") -> bool:
        """Mark article as broadcasted (app desktop push)."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        cursor = conn.execute(
            """
            UPDATE articles
            SET publish_stage = 'broadcasted', broadcasted_at = ?, broadcast_strategy = ?, updated_at = ?
            WHERE article_id = ?
            """,
            (now, strategy, now, article_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete(self, article_id: str) -> bool:
        """Delete article by canonical article_id."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM articles WHERE article_id = ?", (article_id,))
        conn.commit()
        return cursor.rowcount > 0

    def cleanup_old(self, days: int = 7) -> int:
        """Delete articles older than N days. Returns deleted row count."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM articles WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount

    def get_published_ids(self) -> List[str]:
        """Return canonical ids already publicly published."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT article_id FROM articles WHERE COALESCE(publish_stage, 'local') IN ('published', 'broadcasted')"
        ).fetchall()
        return [row["article_id"] for row in rows]

    def get_stats(self) -> dict:
        """Return general database stats."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        published = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE COALESCE(publish_stage, 'local') IN ('published', 'broadcasted')"
        ).fetchone()[0]

        sources = {}
        for row in conn.execute("SELECT source_key, COUNT(*) AS cnt FROM articles GROUP BY source_key"):
            sources[row["source_key"]] = row["cnt"]

        return {
            "total_articles": total,
            "published_articles": published,
            "unpublished_articles": total - published,
            "by_source": sources,
        }

    def get_source_metrics(self, source_keys: list[str]) -> dict:
        """Get workflow-oriented metrics for a subset of sources."""
        conn = self._get_conn()
        if not source_keys:
            return {
                "total_articles": 0,
                "published_articles": 0,
                "auto_candidates": 0,
                "manual_review": 0,
                "low_priority": 0,
                "passed_articles": 0,
            }

        placeholders = ", ".join("?" for _ in source_keys)
        where = f"source_key IN ({placeholders})"
        params = list(source_keys)

        def _count(extra_sql: str = "", extra_params: list[Any] | None = None) -> int:
            sql = f"SELECT COUNT(*) FROM articles WHERE {where}"
            if extra_sql:
                sql += f" AND {extra_sql}"
            return conn.execute(sql, params + (extra_params or [])).fetchone()[0]

        return {
            "total_articles": _count(),
            "published_articles": _count("COALESCE(publish_stage, 'local') IN ('published', 'broadcasted')"),
            "auto_candidates": _count(
                "review_status = 'auto_candidate' "
                "AND COALESCE(publish_stage, 'local') = 'local' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM push_history ph "
                "WHERE ph.article_id = articles.article_id AND ph.strategy = 'auto'"
                ")"
            ),
            "manual_review": _count("review_status = 'manual_review' AND COALESCE(publish_stage, 'local') NOT IN ('published', 'broadcasted')"),
            "low_priority": _count("review_status = 'low_priority' AND COALESCE(publish_stage, 'local') NOT IN ('published', 'broadcasted')"),
            "passed_articles": _count("filter_status = 'passed'"),
        }

    # ------------------------------------------------------------------
    # Push workflow helpers
    # ------------------------------------------------------------------

    def get_auto_publish_candidates(
        self,
        source_keys: list[str],
        threshold: int,
        window_start: datetime,
        window_end: datetime,
        limit: int = 10,
    ) -> list[dict]:
        """Get scored candidates that belong to the current window."""
        if not source_keys:
            return []
        conn = self._get_conn()
        source_key_list = list(source_keys)
        placeholders = ", ".join("?" for _ in source_key_list)
        rows = conn.execute(
            f"""
            SELECT *
            FROM articles
            WHERE source_key IN ({placeholders})
              AND COALESCE(publish_stage, 'local') = 'local'
              AND filter_status = 'passed'
              AND review_status = 'auto_candidate'
              AND auto_publish_enabled = 1
              AND score IS NOT NULL
              AND score >= ?
              AND scored_at IS NOT NULL
              AND scored_at >= ?
              AND scored_at < ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM push_history ph
                  WHERE ph.article_id = articles.article_id
                    AND ph.strategy = 'auto'
              )
            ORDER BY score DESC, publish_time DESC, created_at DESC
            LIMIT ?
            """,
            source_key_list + [threshold, window_start.isoformat(), window_end.isoformat(), limit],
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def has_push_history(self, article_id: str, strategy: str = "auto") -> bool:
        """Return True if the article has already been pushed with the given strategy."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM push_history WHERE article_id = ? AND strategy = ? LIMIT 1",
            (article_id, strategy),
        ).fetchone()
        return row is not None

    def get_push_history_article_ids(self, strategy: str = "auto") -> list[str]:
        """Return article ids that already have a push history row for the given strategy."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT article_id FROM push_history WHERE strategy = ?",
            (strategy,),
        ).fetchall()
        return [row["article_id"] for row in rows]

    def record_push_history(
        self,
        article_id: str,
        source_key: str,
        score: int | None,
        cms_id: str,
        window_start: datetime,
        strategy: str = "auto",
    ) -> int:
        """Insert a push history record."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO push_history (article_id, source_key, score, cms_id, pushed_at, window_start, strategy)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                source_key,
                score,
                cms_id,
                datetime.now().isoformat(),
                window_start.isoformat(),
                strategy,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return row[0] if row else 0

    def count_pushes_in_window(self, window_start: datetime, strategy: str = "auto") -> int:
        """Count how many real pushes (with cms_id) were already made in the given window."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM push_history WHERE window_start = ? AND strategy = ? AND cms_id IS NOT NULL AND cms_id != ''",
            (window_start.isoformat(), strategy),
        ).fetchone()
        return row[0] if row else 0

    def list_push_history(self, limit: int = 20, source_keys: list[str] | None = None) -> list[dict]:
        """List recent push history entries."""
        conn = self._get_conn()
        if source_keys:
            source_key_list = list(source_keys)
            placeholders = ", ".join("?" for _ in source_key_list)
            rows = conn.execute(
                f"""
                SELECT * FROM push_history
                WHERE source_key IN ({placeholders})
                ORDER BY pushed_at DESC
                LIMIT ?
                """,
                source_key_list + [limit],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM push_history ORDER BY pushed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    def has_broadcast_history(self, article_id: str) -> bool:
        """Return True if the article has already been broadcasted."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM broadcast_history WHERE article_id = ? LIMIT 1",
            (article_id,),
        ).fetchone()
        return row is not None

    def get_recent_broadcasted_titles(self, limit: int = 6) -> list[str]:
        """Return titles of the most recently broadcasted articles."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT a.title FROM broadcast_history bh
            JOIN articles a ON a.article_id = bh.article_id
            ORDER BY bh.pushed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row["title"] for row in rows if row["title"]]

    def record_broadcast_history(
        self,
        article_id: str,
        source_key: str,
        cms_id: str,
        push_title: str,
        score: int | None = None,
        strategy: str = "manual",
        result: str = "",
    ) -> int:
        """Insert a broadcast history record."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO broadcast_history (article_id, source_key, cms_id, score, push_title, pushed_at, strategy, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (article_id, source_key, cms_id, score, push_title, datetime.now().isoformat(), strategy, result),
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return row[0] if row else 0

    def list_broadcast_history(self, limit: int = 20) -> list[dict]:
        """List recent broadcast history entries."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM broadcast_history ORDER BY pushed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_auto_broadcast_candidates(
        self,
        grace_minutes: int = 15,
        limit: int = 5,
    ) -> list[dict]:
        """Get published articles ready for auto broadcast (past grace period, not yet broadcasted)."""
        conn = self._get_conn()
        cutoff = (datetime.now() - timedelta(minutes=grace_minutes)).isoformat()
        rows = conn.execute(
            """
            SELECT *
            FROM articles
            WHERE publish_stage = 'published'
              AND cms_id IS NOT NULL AND cms_id != ''
              AND published_at IS NOT NULL AND published_at <= ?
              AND NOT EXISTS (
                  SELECT 1 FROM broadcast_history bh
                  WHERE bh.article_id = articles.article_id
              )
            ORDER BY score DESC, published_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    # Sources excluded from auto-publish (scraper removed but DB may have stale data)
    AUTO_PUBLISH_EXCLUDED_SOURCES = {"bestblogs"}

    def get_auto_publish_broadcast_candidates(
        self,
        min_score: int = 75,
        limit: int = 1,
    ) -> list[dict]:
        """Get unpublished articles ready for auto publish + broadcast.

        Only selects articles that haven't been published or broadcast yet.
        Excludes sources in AUTO_PUBLISH_EXCLUDED_SOURCES.
        """
        excluded = self.AUTO_PUBLISH_EXCLUDED_SOURCES
        placeholders = ",".join("?" * len(excluded))
        conn = self._get_conn()
        rows = conn.execute(
            f"""
            SELECT *
            FROM articles
            WHERE COALESCE(publish_stage, 'local') = 'local'
              AND filter_status = 'passed'
              AND score IS NOT NULL
              AND score >= ?
              AND source_key NOT IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM push_history ph
                  WHERE ph.article_id = articles.article_id AND ph.strategy = 'auto'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM broadcast_history bh
                  WHERE bh.article_id = articles.article_id
              )
            ORDER BY score DESC, created_at DESC
            LIMIT ?
            """,
            (min_score, *excluded, limit),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Blocklist rules
    # ------------------------------------------------------------------

    def list_blocklist_rules(self, active_only: bool = False) -> list[dict]:
        """Return blocklist rules ordered by priority."""
        conn = self._get_conn()
        sql = "SELECT * FROM blocklist_rules"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order ASC, id ASC"
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def create_blocklist_rule(self, data: dict) -> int:
        """Create a blocklist rule."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO blocklist_rules (
                pattern, match_type, field, action, source_key,
                penalty_score, notes, sort_order, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _sanitize_for_gbk(data.get("pattern", "")),
                data.get("match_type", "keyword"),
                data.get("field", "title"),
                data.get("action", "block"),
                data.get("source_key") or None,
                int(data.get("penalty_score", 0) or 0),
                _sanitize_for_gbk(data.get("notes", "")),
                int(data.get("sort_order", 100) or 100),
                1 if data.get("is_active", True) else 0,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return row[0] if row else 0

    def update_blocklist_rule(self, rule_id: int, data: dict) -> bool:
        """Update a blocklist rule."""
        conn = self._get_conn()
        existing = conn.execute("SELECT * FROM blocklist_rules WHERE id = ?", (rule_id,)).fetchone()
        if not existing:
            return False

        merged = dict(existing)
        merged.update(data or {})
        cursor = conn.execute(
            """
            UPDATE blocklist_rules
            SET pattern = ?, match_type = ?, field = ?, action = ?, source_key = ?,
                penalty_score = ?, notes = ?, sort_order = ?, is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                _sanitize_for_gbk(merged.get("pattern", "")),
                merged.get("match_type", "keyword"),
                merged.get("field", "title"),
                merged.get("action", "block"),
                merged.get("source_key") or None,
                int(merged.get("penalty_score", 0) or 0),
                _sanitize_for_gbk(merged.get("notes", "")),
                int(merged.get("sort_order", 100) or 100),
                1 if merged.get("is_active", True) else 0,
                datetime.now().isoformat(),
                rule_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_blocklist_rule(self, rule_id: int) -> bool:
        """Delete a blocklist rule."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM blocklist_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Row parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert an SQLite row into a plain article dict."""
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
            "score": row["score"] if "score" in row.keys() else None,
            "category": row["category"] if "category" in row.keys() else None,
            "language": row["language"] if "language" in row.keys() else "zh",
            "one_sentence_summary": row["one_sentence_summary"] if "one_sentence_summary" in row.keys() else None,
            "filter_status": row["filter_status"] if "filter_status" in row.keys() else "passed",
            "filter_reason": row["filter_reason"] if "filter_reason" in row.keys() else None,
            "duplicate_key": row["duplicate_key"] if "duplicate_key" in row.keys() else None,
            "score_status": row["score_status"] if "score_status" in row.keys() else "pending",
            "score_reason": row["score_reason"] if "score_reason" in row.keys() else None,
            "review_status": row["review_status"] if "review_status" in row.keys() else "manual_review",
            "auto_publish_enabled": bool(row["auto_publish_enabled"]) if "auto_publish_enabled" in row.keys() else False,
            "publish_stage": row["publish_stage"] if "publish_stage" in row.keys() and row["publish_stage"] else ("draft" if row["cms_id"] else "local"),
            "published_strategy": row["published_strategy"] if "published_strategy" in row.keys() else None,
            "scored_at": row["scored_at"] if "scored_at" in row.keys() else None,
            "broadcasted_at": row["broadcasted_at"] if "broadcasted_at" in row.keys() else None,
            "broadcast_strategy": row["broadcast_strategy"] if "broadcast_strategy" in row.keys() else None,
        }

        tags_raw = row["tags"] if "tags" in row.keys() else None
        article["tags"] = json.loads(tags_raw) if tags_raw else []
        article["tags_json"] = tags_raw

        blocks_raw = row["blocks"] if "blocks" in row.keys() else None
        if blocks_raw:
            try:
                article["blocks"] = json.loads(blocks_raw)
            except json.JSONDecodeError:
                article["blocks"] = []
        else:
            article["blocks"] = []

        return article

    @staticmethod
    def _compute_abstract(article: dict) -> str:
        """Generate a simple fallback abstract from article blocks."""
        texts = [
            b.get("text", "").strip()
            for b in article.get("blocks", [])
            if b.get("type") != "img" and b.get("text")
        ]
        return " ".join(" ".join(texts).split())[:180]

    # ------------------------------------------------------------------
    # User operations
    # ------------------------------------------------------------------

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
        return {
            "id": row["id"],
            "username": row["username"],
            "password_hash": row["password_hash"],
            "role": row["role"] if "role" in row.keys() else "admin",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "password_hash": row["password_hash"],
            "role": row["role"] if "role" in row.keys() else "admin",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

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

    # ------------------------------------------------------------------
    # Settings and schedules
    # ------------------------------------------------------------------

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
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        conn.commit()

    def set_settings_batch(self, items: Dict[str, str]):
        conn = self._get_conn()
        now = datetime.now().isoformat()
        for key, value in items.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        conn.commit()

    def save_schedule(self, source_key: str, enabled: bool, interval_minutes: int):
        """Save schedule config for a source."""
        key = f"{self.SCHEDULE_PREFIX}{source_key}"
        value = json.dumps({"enabled": enabled, "interval_minutes": interval_minutes})
        self.set_setting(key, value)

    def get_schedule(self, source_key: str) -> dict | None:
        """Get schedule config for a source."""
        key = f"{self.SCHEDULE_PREFIX}{source_key}"
        value = self.get_setting(key)
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def get_all_schedules(self) -> dict[str, dict]:
        """Get all stored schedule configs."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (f"{self.SCHEDULE_PREFIX}%",),
        ).fetchall()
        result = {}
        for row in rows:
            source_key = row["key"][len(self.SCHEDULE_PREFIX):]
            try:
                result[source_key] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
        return result

    def delete_schedule(self, source_key: str):
        """Delete a stored schedule."""
        key = f"{self.SCHEDULE_PREFIX}{source_key}"
        conn = self._get_conn()
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()

    def close(self):
        """Close the current thread's SQLite connection."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            delattr(self._local, "conn")


# Singleton instance
_db_instance: Optional[ArticleDatabase] = None
_db_lock = threading.Lock()


def get_database(db_path: str | Path = None) -> ArticleDatabase:
    """Get or create the database singleton."""
    global _db_instance
    with _db_lock:
        if _db_instance is None:
            _db_instance = ArticleDatabase(db_path or "data/articles.db")
        return _db_instance


def close_database():
    """Close the database singleton connection."""
    global _db_instance
    with _db_lock:
        if _db_instance is not None:
            _db_instance.close()
            _db_instance = None
