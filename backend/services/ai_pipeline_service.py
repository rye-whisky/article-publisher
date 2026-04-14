# -*- coding: utf-8 -*-
"""AI Pipeline service: manages AI article scrapers, ingestion, and queries."""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from config.loader import load_config
from services.database import ArticleDatabase
from services.pipeline_service import RunState, SourceScheduleState

log = logging.getLogger("pipeline")


class AiPipelineService:
    """Service for AI-curated article pipelines."""

    def __init__(self, cfg: dict, base_dir: Path, session, scrapers: dict,
                 database: ArticleDatabase):
        self.cfg = cfg
        self.base_dir = base_dir
        self.session = session
        self.scrapers = scrapers
        self.database = database
        self._lock = threading.Lock()
        self.run_state = RunState()
        self.source_schedules: dict[str, SourceScheduleState] = {}

    @classmethod
    def create(cls, base_dir: Path, database: ArticleDatabase) -> "AiPipelineService":
        """Build AiPipelineService from config.yaml."""
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        config_path = base_dir / "config.yaml"
        cfg = load_config(config_path)

        retry = Retry(total=2, backoff_factor=1)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=4)
        session = __import__("requests").Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        from ai_pipelines import create_ai_scrapers
        scrapers = create_ai_scrapers(cfg, session, base_dir)

        return cls(cfg, base_dir, session, scrapers, database)

    # -- Ingestion --

    def ingest(self, source: str = "all") -> dict:
        """Run AI scrapers, save new articles to files + DB.

        Args:
            source: "all" or a specific source key (e.g. "bestblogs").

        Returns summary: {source: {new: int, total: int}}.
        """
        summary = {}
        for source_key, scraper in self.scrapers.items():
            if source not in ("all", source_key):
                continue
            try:
                items = scraper.parse_list()
                new_count = 0
                for item in items:
                    if self.run_state.cancelled:
                        break
                    article = scraper.fetch_detail(item)
                    # Save to file
                    scraper.save(article)

                    # Build DB record
                    article_id = article.get("article_id", f"{source_key}:{article.get('raw_id', '')}")
                    db_record = {
                        "article_id": article_id,
                        "source_key": source_key,
                        "raw_id": article.get("raw_id", ""),
                        "title": article.get("title", ""),
                        "source": article.get("source", ""),
                        "author": article.get("author", ""),
                        "publish_time": article.get("publish_time", ""),
                        "original_url": article.get("original_url", ""),
                        "cover_src": article.get("cover_src", ""),
                        "blocks": article.get("blocks", []),
                        "abstract": article.get("one_sentence_summary", ""),
                        "score": article.get("score"),
                        "tags": article.get("tags", []),
                        "category": article.get("category", ""),
                        "language": article.get("language", "zh"),
                        "one_sentence_summary": article.get("one_sentence_summary", ""),
                    }
                    # Check if new
                    existing = self.database.get_by_article_id(article_id)
                    if not existing:
                        new_count += 1
                    self.database.insert_or_update(db_record)

                summary[source_key] = {"new": new_count, "total": len(items)}
                log.info("[AI] %s: %d new / %d total", source_key, new_count, len(items))

            except Exception as e:
                # Safely convert exception to string, avoiding GBK encoding errors on Windows
                error_msg = str(e).encode('ascii', errors='replace').decode('ascii')
                log.error("[AI] %s ingest failed: %s", source_key, error_msg)
                summary[source_key] = {"new": 0, "total": 0, "error": error_msg}

        return summary

    # -- Status --

    def _ai_source_filter(self) -> str:
        """Build SQL WHERE fragment for AI source keys."""
        keys = list(self.scrapers.keys())
        if not keys:
            return "1=0"
        placeholders = ", ".join("?" for _ in keys)
        return f"source_key IN ({placeholders})"

    def _ai_source_params(self) -> list[str]:
        return list(self.scrapers.keys())

    def get_status(self) -> dict:
        """Return run status + schedule status + basic stats."""
        conn = self.database._get_conn()
        src_filter = self._ai_source_filter()
        params = self._ai_source_params()
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {src_filter}", params
        ).fetchone()[0]
        published = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {src_filter} AND cms_id IS NOT NULL", params
        ).fetchone()[0]

        run_info = self.run_state.status()
        run_info["total"] = total
        run_info["published"] = published
        run_info["sources"] = list(self.scrapers.keys())
        return run_info

    # -- Per-source scheduler helpers (mirrors PipelineService) --

    def _init_schedules(self):
        """Initialize SourceScheduleState for each known source."""
        for key in self.scrapers:
            if key not in self.source_schedules:
                self.source_schedules[key] = SourceScheduleState(key, enabled=False, interval_minutes=60)

    def _source_scheduler_run(self, source_key: str):
        """Timer callback: run ingest for a single source."""
        try:
            if self.run_state.start():
                result = self.ingest(source=source_key)
                self.run_state.finish({"ok": True, "summary": result})
        except Exception as e:
            log.error("[AI] Scheduler run failed for %s: %s", source_key, e)
            self.run_state.finish({"ok": False, "error": str(e)})

    def set_source_schedule(self, source_key: str, enabled: bool, interval_minutes: int):
        self._init_schedules()
        sched = self.source_schedules.get(source_key)
        if not sched:
            raise ValueError(f"Unknown source: {source_key}")
        sched.set_config(enabled, interval_minutes, run_fn=lambda: self._source_scheduler_run(source_key))
        # Persist to database
        if self.database:
            self.database.save_schedule(f"ai_{source_key}", enabled, interval_minutes)

    def get_source_schedules(self) -> dict:
        self._init_schedules()
        return {key: sched.status() for key, sched in self.source_schedules.items()}

    def restore_schedules(self):
        """Restore schedule configs from database and start enabled timers."""
        if not self.database:
            log.info("[AI] No database, skipping schedule restore")
            return
        self._init_schedules()
        saved = self.database.get_all_schedules()
        for key, config in saved.items():
            # AI schedules are stored with "ai_" prefix
            if not key.startswith("ai_"):
                continue
            source_key = key[3:]  # Remove "ai_" prefix
            if source_key not in self.scrapers:
                continue
            enabled = config.get("enabled", False)
            interval = config.get("interval_minutes", 60)
            if enabled:
                log.info("[AI] Restoring schedule: %s every %d minutes", source_key, interval)
                self.set_source_schedule(source_key, enabled, interval)
            else:
                self._init_schedules()
                self.source_schedules[source_key].enabled = False
                self.source_schedules[source_key].interval_minutes = interval

    def stop_all_schedules(self):
        for sched in self.source_schedules.values():
            sched.stop()

    # -- Queries --

    def list_articles(
        self,
        source: str = "all",
        category: str = None,
        min_score: int = None,
        tag: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """Query AI articles from DB with filters. Returns (total, articles)."""
        conn = self.database._get_conn()
        conditions = [self._ai_source_filter()]  # AI sources only
        params = self._ai_source_params()

        if source != "all":
            conditions.append("source_key = ?")
            params.append(source)

        if category:
            conditions.append("category = ?")
            params.append(category)

        if min_score is not None:
            conditions.append("score >= ?")
            params.append(min_score)

        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')

        where = " AND ".join(conditions)
        count_sql = f"SELECT COUNT(*) FROM articles WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()[0]

        offset = (page - 1) * page_size
        query_sql = f"SELECT * FROM articles WHERE {where} ORDER BY created_at DESC, publish_time DESC LIMIT ? OFFSET ?"
        rows = conn.execute(query_sql, params + [page_size, offset]).fetchall()
        articles = [self.database._row_to_dict(row) for row in rows]

        return total, articles

    def get_article(self, article_id: str) -> Optional[dict]:
        """Get a single AI article by article_id."""
        return self.database.get_by_article_id(article_id)

    def get_tags(self) -> list[str]:
        """Get all unique tags from AI articles."""
        conn = self.database._get_conn()
        rows = conn.execute(
            f"SELECT tags FROM articles WHERE tags IS NOT NULL AND {self._ai_source_filter()}",
            self._ai_source_params(),
        ).fetchall()
        tag_set = set()
        for row in rows:
            try:
                tags = json.loads(row["tags"]) if row["tags"] else []
                tag_set.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(tag_set)

    def get_stats(self) -> dict:
        """Get AI article statistics."""
        conn = self.database._get_conn()
        src_filter = self._ai_source_filter()
        params = self._ai_source_params()
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {src_filter}", params
        ).fetchone()[0]
        published = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {src_filter} AND cms_id IS NOT NULL", params
        ).fetchone()[0]

        # By category
        categories = {}
        for row in conn.execute(
            f"SELECT category, COUNT(*) as cnt FROM articles WHERE {src_filter} AND category IS NOT NULL GROUP BY category",
            params,
        ):
            categories[row["category"]] = row["cnt"]

        # Score distribution
        avg_score = conn.execute(
            f"SELECT AVG(score) FROM articles WHERE {src_filter} AND score IS NOT NULL", params
        ).fetchone()[0]

        return {
            "total": total,
            "published": published,
            "unpublished": total - published,
            "avg_score": round(avg_score, 1) if avg_score else 0,
            "categories": categories,
            "sources": list(self.scrapers.keys()),
        }
