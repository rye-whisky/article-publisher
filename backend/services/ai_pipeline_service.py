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
                 database: ArticleDatabase, cos_uploader=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.session = session
        self.scrapers = scrapers
        self.database = database
        self.cos_uploader = cos_uploader
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

        # Build COS uploader from chainthink config
        cos_uploader = None
        ct_cfg = cfg.get("chainthink", {})
        if ct_cfg.get("upload_url"):
            from utils.cos import COSUploader
            api_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://admin.chainthink.cn",
                "Referer": "https://admin.chainthink.cn/",
                "User-Agent": "Mozilla/5.0",
                "x-token": ct_cfg.get("token", ""),
                "x-user-id": str(ct_cfg.get("user_id", "")),
                "X-App-Id": str(ct_cfg.get("app_id", "")),
            }
            cos_uploader = COSUploader(
                upload_url=ct_cfg["upload_url"],
                api_headers=api_headers,
                session=session,
                x_app_id=str(ct_cfg.get("app_id", "")),
            )

        return cls(cfg, base_dir, session, scrapers, database, cos_uploader)

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

                    # Re-upload images to COS (avoid expiring CDN tokens / hotlink blocks)
                    if self.cos_uploader:
                        article = self._rehost_images(article)

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

    def _rehost_images(self, article: dict) -> dict:
        """Download images from external CDNs and re-upload to our COS.

        Many CDNs (知乎 pic-out.zhimg.com, etc.) use expiring auth tokens
        or referer-based hotlink protection. Re-hosting ensures images
        remain accessible indefinitely.

        Images that fail to download (expired tokens, 403, etc.) are
        removed from blocks so the frontend doesn't show broken images.
        """
        # Collect all image URLs to rehost
        urls_to_rehost = []
        failed_urls = set()  # URLs that can't be downloaded

        # Use the article's original URL as Referer for hotlink-protected CDNs
        article_referer = article.get("original_url", "")

        # Cover image
        cover_src = article.get("cover_src", "")
        if cover_src and "cos.chainthink.cn" not in cover_src:
            urls_to_rehost.append(("cover_src", cover_src))

        # Body images
        blocks = article.get("blocks", [])
        for i, block in enumerate(blocks):
            if block.get("type") == "img" and block.get("src"):
                src = block["src"]
                if "cos.chainthink.cn" not in src:
                    urls_to_rehost.append(("block", i, src))

        if not urls_to_rehost:
            return article

        # Rehost each image
        rehosted = {}  # original_url -> new_url
        for entry in urls_to_rehost:
            original_url = entry[-1]
            if original_url in rehosted:
                new_url = rehosted[original_url]
            elif original_url in failed_urls:
                continue  # Already failed, skip
            else:
                referer = self._build_referer(original_url, article_referer)
                try:
                    new_url = self.cos_uploader.upload_cover_from_url(original_url, referer=referer)
                    if new_url:
                        rehosted[original_url] = new_url
                        log.info("[AI] Rehosted: %s -> %s", original_url[:80], new_url[:80])
                    else:
                        log.warning("[AI] Rehost returned empty for: %s", original_url[:80])
                        failed_urls.add(original_url)
                        continue
                except Exception as e:
                    log.warning("[AI] Rehost failed (%s), will remove: %s", str(e)[:40], original_url[:80])
                    failed_urls.add(original_url)
                    continue

            # Apply rehosted URL
            if entry[0] == "cover_src":
                article["cover_src"] = new_url
            elif entry[0] == "block":
                blocks[entry[1]]["src"] = new_url

        # Remove blocks with failed images (broken image URLs)
        if failed_urls:
            before = len(blocks)
            article["blocks"] = [b for b in blocks if b.get("src") not in failed_urls]
            removed = before - len(article["blocks"])
            if removed:
                log.info("[AI] Removed %d broken image blocks from '%s'", removed, article.get("title", "")[:40])

            # Clear cover if it failed
            if article.get("cover_src") in failed_urls:
                article["cover_src"] = ""

        return article

    @staticmethod
    def _build_referer(image_url: str, article_url: str) -> str:
        """Build a Referer header for downloading images.

        Some CDNs require a specific Referer to allow access.
        - pic-out.zhimg.com / zhimg.com: needs zhihu.com Referer
        - Others: use the article's original URL as Referer
        """
        if "zhimg.com" in image_url:
            return "https://www.zhihu.com/"
        if article_url:
            return article_url
        return ""

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
