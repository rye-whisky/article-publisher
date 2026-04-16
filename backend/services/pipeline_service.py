# -*- coding: utf-8 -*-
"""Pipeline service: orchestration, state management, RunState, SchedulerState.

This is the canonical service used by the API routes.
"""

import json
import logging
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.loader import load_config
from pipelines import create_scrapers
from services.article_store import ArticleStore
from services.publisher import Publisher
from utils.cos import COSUploader

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# State trackers
# ---------------------------------------------------------------------------

class RunState:
    """Thread-safe tracker for background pipeline runs.

    Includes auto-timeout: if a run exceeds MAX_RUN_SECONDS,
    it is force-reset so the system doesn't get stuck forever.
    """

    MAX_RUN_SECONDS = 600  # 10 minutes

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.started_at: Optional[str] = None
        self.result: Optional[dict] = None
        self._cancel_event = threading.Event()

    def start(self) -> bool:
        with self._lock:
            if self.running:
                # Check for stale run (stuck longer than timeout)
                if self.started_at:
                    try:
                        started = datetime.fromisoformat(self.started_at)
                        elapsed = (datetime.now() - started).total_seconds()
                        if elapsed > self.MAX_RUN_SECONDS:
                            log.warning("RunState: force-resetting stuck run (started %s, %.0fs ago)",
                                        self.started_at, elapsed)
                            self.running = False
                            self.result = {"ok": False, "error": "run timed out, auto-reset"}
                    except (ValueError, TypeError):
                        pass
                if self.running:
                    return False
            self.running = True
            self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._cancel_event.clear()
            return True

    def finish(self, result: dict):
        with self._lock:
            self.running = False
            self.result = result

    def cancel(self) -> bool:
        """Request cancellation of the running pipeline. Returns True if a run was active."""
        with self._lock:
            if not self.running:
                return False
            log.warning("RunState: cancellation requested for run started at %s", self.started_at)
            self._cancel_event.set()
            return True

    @property
    def cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._cancel_event.is_set()

    def status(self) -> dict:
        with self._lock:
            info = {
                "running": self.running,
                "started_at": self.started_at,
                "last_result": self.result,
            }
            # Warn about long-running tasks
            if self.running and self.started_at:
                try:
                    started = datetime.fromisoformat(self.started_at)
                    elapsed = (datetime.now() - started).total_seconds()
                    info["elapsed_seconds"] = int(elapsed)
                except (ValueError, TypeError):
                    pass
            return info


class SourceScheduleState:
    """Thread-safe per-source scheduler state and timer management."""

    def __init__(self, source_key: str, enabled: bool = False, interval_minutes: int = 60):
        self.source_key = source_key
        self._lock = threading.Lock()
        self.enabled = enabled
        self.interval_minutes = max(1, min(1440, interval_minutes))
        self.next_run_time: Optional[str] = None
        self._timer: Optional[threading.Timer] = None
        self._stop_event = threading.Event()

    def set_config(self, enabled: bool, interval_minutes: int, run_fn=None):
        with self._lock:
            self.enabled = enabled
            self.interval_minutes = max(1, min(1440, interval_minutes))
            self._restart_timer(run_fn)

    def _restart_timer(self, run_fn=None):
        self._stop_event.set()
        if self._timer:
            self._timer.cancel()
        self._stop_event.clear()

        if not self.enabled:
            self.next_run_time = None
            return

        import datetime as dt
        next_run = dt.datetime.now() + dt.timedelta(minutes=self.interval_minutes)
        self.next_run_time = next_run.strftime("%Y-%m-%dT%H:%M:%S")

        fn = run_fn or self._default_run

        def _callback():
            if self._stop_event.is_set():
                return
            fn()
            if self.enabled and not self._stop_event.is_set():
                self._restart_timer(run_fn)

        self._timer = threading.Timer(self.interval_minutes * 60, _callback)
        self._timer.daemon = True
        self._timer.start()

    def _default_run(self):
        pass  # overridden via run_fn

    def status(self) -> dict:
        with self._lock:
            return {
                "source_key": self.source_key,
                "enabled": self.enabled,
                "interval_minutes": self.interval_minutes,
                "next_run_time": self.next_run_time,
            }

    def stop(self):
        with self._lock:
            self.enabled = False
            self._stop_event.set()
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self.next_run_time = None


# ---------------------------------------------------------------------------
# PipelineService
# ---------------------------------------------------------------------------

class PipelineService:
    """Main pipeline service: orchestrates scrapers, publisher, state."""

    def __init__(self, cfg: dict, base_dir: Path, session, scrapers: dict,
                 publisher: Publisher, article_store: ArticleStore, state_file: Path,
                 database=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.session = session
        self.scrapers = scrapers
        self.publisher = publisher
        self.article_store = article_store
        self.state_file = state_file
        self.database = database  # Optional: ArticleDatabase
        self.run_state = RunState()
        self.source_schedules: dict[str, SourceScheduleState] = {}

    # -- Factory --

    @classmethod
    def create(cls, base_dir: Path = None) -> "PipelineService":
        """Build a PipelineService from config.yaml."""
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent.parent

        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        # Load config
        config_path = base_dir / "config.yaml"
        cfg = load_config(config_path)

        # HTTP session with retry and connection pool limits (memory optimization)
        retry_cfg = cfg.get("retry", {})
        retry = Retry(
            total=retry_cfg.get("max_retries", 3),
            backoff_factor=retry_cfg.get("backoff_factor", 1),
            status_forcelist=retry_cfg.get("status_forcelist", [500, 502, 503, 504]),
        )
        # Limit pool size for low-memory server (2 cores, 2GB RAM)
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=4,  # Max 4 connection pools
            pool_maxsize=8,      # Max 8 connections per pool
        )
        session = __import__("requests").Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

        # Build components
        scrapers = create_scrapers(cfg, session, base_dir)

        api_headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://admin.chainthink.cn",
            "Referer": "https://admin.chainthink.cn/",
            "User-Agent": "Mozilla/5.0",
            "x-token": cfg["chainthink"]["token"],
            "x-user-id": str(cfg["chainthink"]["user_id"]),
            "X-App-Id": str(cfg["chainthink"]["app_id"]),
        }

        cos = COSUploader(
            upload_url=cfg["chainthink"]["upload_url"],
            api_headers=api_headers,
            session=session,
            x_app_id=str(cfg["chainthink"].get("app_id", "")),
        )

        publisher = Publisher(
            api_url=cfg["chainthink"]["api_url"],
            api_headers=api_headers,
            cos_uploader=cos,
        )

        article_store = ArticleStore(scrapers)
        state_file = base_dir / cfg["paths"]["state_file"]

        # Initialize database if configured
        database = None
        db_path = cfg.get("database", {}).get("sqlite_path")
        if db_path:
            from services.database import ArticleDatabase
            database = ArticleDatabase(base_dir / db_path)
            log.info("Database initialized: %s", base_dir / db_path)

        return cls(cfg, base_dir, session, scrapers, publisher, article_store, state_file, database)

    # -- State management --

    def load_state(self) -> dict:
        if not self.state_file.exists():
            return {"published_ids": [], "updated_at": None}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save_state(self, state: dict):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.now().isoformat()
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- Orchestration --

    def ingest_sources(self, source: str, state: dict) -> list[str]:
        fetched = []
        published_ids = set(state.get("published_ids", []))

        # Also check database for published IDs
        if self.database:
            db_published = set(self.database.get_published_ids())
            published_ids.update(db_published)

        for key, scraper in self.scrapers.items():
            if self.run_state.cancelled:
                log.warning("Ingest cancelled, stopping early")
                break
            src_cfg = self.cfg.get("sources", {}).get(key, {})
            if source not in (key, "all"):
                continue
            if not src_cfg.get("enabled", True):
                continue
            for item in scraper.parse_list():
                if self.run_state.cancelled:
                    break
                aid = f"{key}:{item['article_id']}"

                # Skip if already published to CMS
                if aid in published_ids:
                    continue

                # Skip if already exists in database (fetched in a previous run)
                if self.database:
                    db_id = item.get("article_id", aid.split(":")[-1])
                    existing = self.database.get_by_article_id(db_id)
                    if existing:
                        continue

                try:
                    article = scraper.fetch_detail(item)
                    scraper.save(article)
                    # Also save to database
                    if self.database:
                        self.database.insert_or_update(article)
                        # AI abstract generation
                        from services.llm import generate_abstract
                        ai_abstract = generate_abstract(article, self.database)
                        # Use same ID format as DB stores (raw article_id, not article_id_full)
                        db_id = article.get("article_id", aid.split(":")[-1])
                        updated = self.database.update_abstract(db_id, ai_abstract)
                        log.info("DB abstract updated: db_id=%s, success=%s, len=%d",
                                 db_id, updated, len(ai_abstract))
                    fetched.append(aid)
                    log.info("Fetched %s: %s - %s", key.upper(), aid, item.get("title", "")[:40])
                except Exception as e:
                    log.error("Fetch %s %s failed: %s", key.upper(), item.get("article_id", ""), e)
        log.info("Ingested %d new articles", len(fetched))
        return fetched

    def load_articles(self, source: str = "all", limit: int = 500) -> list[dict]:
        """Load articles with memory-safe limit."""
        return self.article_store.list_articles(source, limit=limit)

    def clear_caches(self):
        """Clear all in-memory caches to free RAM."""
        self.article_store.clear_cache()
        for scraper in self.scrapers.values():
            if hasattr(scraper, "clear_cache"):
                scraper.clear_cache()

    def run(self, source="all", since_today_0700=False, republish_ids=None,
            skip_fetch=False, refetch_stcn_urls=None, refetch_techflow_ids=None,
            refetch_blockbeats_urls=None, refetch_chaincatcher_urls=None,
            refetch_odaily_urls=None, refetch_bestblogs_urls=None,
            dry_run=False, republish_refetched=False) -> dict:
        """Run the full pipeline. Returns result dict."""
        started = datetime.now()
        log.info("Pipeline started: source=%s dry_run=%s", source, dry_run)

        state = self.load_state()
        # Merge database published IDs
        if self.database:
            db_published = self.database.get_published_ids()
            state.setdefault("published_ids", []).extend(db_published)
            state["published_ids"] = sorted(set(state["published_ids"]))

        refetch_mode = bool(refetch_stcn_urls or refetch_techflow_ids or refetch_blockbeats_urls or refetch_chaincatcher_urls or refetch_odaily_urls or refetch_bestblogs_urls)
        refreshed = []

        if refetch_mode:
            refreshed = self._do_refetch(source, refetch_stcn_urls, refetch_techflow_ids, refetch_blockbeats_urls, refetch_chaincatcher_urls, refetch_odaily_urls, refetch_bestblogs_urls)
        elif not skip_fetch:
            self.ingest_sources(source, state)

        published_ids = set(state.get("published_ids", []))
        republish_set = set(republish_ids or [])

        # Refetch + republish mode: default to publishing only the refreshed articles
        # when caller opts in (e.g. /api/refetch with republish=true).
        if refetch_mode and republish_refetched and not republish_set:
            republish_set = {item.get("id", "") for item in refreshed if item.get("id")}

        publish_only_set = set(republish_set) if refetch_mode and republish_set else set()

        if refetch_mode and not republish_set:
            self.save_state(state)
            return {"ok": True, "refetched": refreshed, "published": [], "skipped": [], "failed": []}

        articles = self.load_articles(source)

        # Enrich articles with DB abstract (AI-generated) when available
        if self.database:
            enriched = 0
            for article in articles:
                # Disk articles have "source:rawid" format; DB stores raw ID
                db_lookup_id = article["article_id"].split(":")[-1] if ":" in article["article_id"] else article["article_id"]
                db_art = self.database.get_by_article_id(db_lookup_id)
                if db_art and db_art.get("abstract"):
                    article["abstract"] = db_art["abstract"]
                    enriched += 1
            log.info("Enriched %d/%d articles with DB abstract", enriched, len(articles))

        accepted, skipped = [], []

        # Author filtering for STCN
        stcn_scraper = self.scrapers.get("stcn")
        allowed_authors = stcn_scraper.allowed_authors if stcn_scraper else set()

        for article in articles:
            aid = article["article_id"]
            sk = article.get("source_key", "")

            if publish_only_set and aid not in publish_only_set:
                continue
            if sk == "stcn" and allowed_authors and article.get("author") not in allowed_authors:
                skipped.append({"id": aid, "reason": "author"})
                continue
            if since_today_0700 and sk == "stcn":
                pt = article.get("publish_time", "")
                m = __import__("re").match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", pt)
                if not m or m.group(1) != datetime.now().strftime("%Y-%m-%d") or m.group(2) < "07:00":
                    skipped.append({"id": aid, "reason": "time"})
                    continue
            if aid in published_ids and aid not in republish_set:
                skipped.append({"id": aid, "reason": "already_published"})
                continue
            accepted.append(article)

        published, failed = [], []
        if dry_run:
            log.info("Dry run: would publish %d articles", len(accepted))
        else:
            for article in accepted:
                if self.run_state.cancelled:
                    log.warning("Publish cancelled, stopping early (%d/%d published)", len(published), len(accepted))
                    break
                try:
                    result = self.publisher.publish(article)
                    published.append({
                        "article_id": article["article_id"],
                        "cms_id": result["cms_id"],
                        "title": article["title"],
                        "cover_image": result.get("cover_image", ""),
                    })
                    published_ids.add(article["article_id"])
                    # Also mark as published in database
                    if self.database:
                        self.database.mark_published(article["article_id"], result["cms_id"])
                    log.info("Published %s -> CMS %s", article["article_id"], result["cms_id"])
                except Exception as e:
                    failed.append({"id": article["article_id"], "error": str(e), "source": article.get("source_key", "")})
                    log.error("Publish failed %s: %s", article["article_id"], e)

        state["published_ids"] = sorted(published_ids)
        self.save_state(state)

        elapsed = (datetime.now() - started).total_seconds()
        log.info("Pipeline done: published=%d skipped=%d failed=%d %.1fs", len(published), len(skipped), len(failed), elapsed)

        # Cleanup old articles (files + database) older than 7 days
        self.cleanup_old_articles(days=7)

        return {"ok": True, "refetched": refreshed, "published": published, "skipped": skipped, "failed": failed}

    def _do_refetch(self, source, stcn_urls, techflow_ids, blockbeats_urls, chaincatcher_urls=None, odaily_urls=None, bestblogs_urls=None):
        def _gen_abstract(db, article):
            """Generate AI abstract and update DB. Returns the abstract."""
            from services.llm import generate_abstract
            abstract = generate_abstract(article, db)
            db_id = article.get("article_id", "")
            db.update_abstract(db_id, abstract)
            return abstract

        refreshed = []
        if source in ("stcn", "all") and stcn_urls:
            scraper = self.scrapers.get("stcn")
            if scraper:
                for url in stcn_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        if self.database:
                            self.database.insert_or_update(article)
                            _gen_abstract(self.database, article)
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch STCN %s failed: %s", url, e)
        if source in ("techflow", "all") and techflow_ids:
            scraper = self.scrapers.get("techflow")
            if scraper:
                tf_items = {it["article_id"]: it for it in scraper.parse_list()}
                for aid in techflow_ids:
                    item = tf_items.get(str(aid)) or {
                        "article_id": str(aid), "title": str(aid),
                        "original_url": f"https://www.techflowpost.com/zh-CN/article/{aid}",
                        "source": "深潮 TechFlow",
                    }
                    try:
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        if self.database:
                            self.database.insert_or_update(article)
                            _gen_abstract(self.database, article)
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch TechFlow %s failed: %s", aid, e)
        if source in ("blockbeats", "all") and blockbeats_urls:
            scraper = self.scrapers.get("blockbeats")
            if scraper:
                for url in blockbeats_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        if self.database:
                            self.database.insert_or_update(article)
                            _gen_abstract(self.database, article)
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch BlockBeats %s failed: %s", url, e)
        if source in ("chaincatcher", "all") and chaincatcher_urls:
            scraper = self.scrapers.get("chaincatcher")
            if scraper:
                for url in chaincatcher_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        if self.database:
                            self.database.insert_or_update(article)
                            _gen_abstract(self.database, article)
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch ChainCatcher %s failed: %s", url, e)
        if source in ("odaily", "all") and odaily_urls:
            scraper = self.scrapers.get("odaily")
            if scraper:
                for url in odaily_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        if self.database:
                            self.database.insert_or_update(article)
                            _gen_abstract(self.database, article)
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch Odaily %s failed: %s", url, e)
        if source in ("bestblogs", "all") and bestblogs_urls:
            scraper = self.scrapers.get("bestblogs")
            if scraper:
                for url_or_id in bestblogs_urls:
                    try:
                        item = scraper.build_item_from_url(url_or_id)
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        if self.database:
                            self.database.insert_or_update(article)
                            _gen_abstract(self.database, article)
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch BestBlogs %s failed: %s", url_or_id, e)
        return refreshed

    # -- Per-source scheduler helpers --

    def cleanup_old_articles(self, days: int = 7) -> int:
        """Remove article JSON files and DB records older than N days.

        Scans each scraper's output_dir for JSON files whose mtime is older
        than the cutoff, then deletes the matching DB rows.
        Returns total number of files removed.
        """
        cutoff = datetime.now() - timedelta(days=days)
        total = 0

        for key, scraper in self.scrapers.items():
            if not scraper.output_dir.exists():
                continue
            removed = 0
            for f in scraper.output_dir.glob("*.json"):
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
                    scraper._article_cache.pop(f, None)
                    removed += 1
            if removed:
                total += removed
                log.info("Cleanup %s: removed %d files older than %d days", key, removed, days)

        # Cleanup database
        if self.database:
            db_removed = self.database.cleanup_old(days)
            if db_removed:
                log.info("Cleanup DB: removed %d rows older than %d days", db_removed, days)

        return total

    def _init_schedules(self):
        """Initialize SourceScheduleState for each known source."""
        for key in self.scrapers:
            if key not in self.source_schedules:
                src_cfg = self.cfg.get("sources", {}).get(key, {})
                interval = src_cfg.get("schedule_interval_minutes", 60)
                self.source_schedules[key] = SourceScheduleState(key, enabled=False, interval_minutes=interval)

    def _source_scheduler_run(self, source_key: str):
        """Timer callback: run pipeline for a single source."""
        try:
            if self.run_state.start():
                result = self.run(source=source_key)
                self.run_state.finish(result)
        except Exception as e:
            log.error("Scheduler run failed for %s: %s", source_key, e)
            self.run_state.finish({"ok": False, "error": str(e)})

    def set_source_schedule(self, source_key: str, enabled: bool, interval_minutes: int):
        self._init_schedules()
        sched = self.source_schedules.get(source_key)
        if not sched:
            raise ValueError(f"Unknown source: {source_key}")
        sched.set_config(enabled, interval_minutes, run_fn=lambda: self._source_scheduler_run(source_key))
        # Persist to database
        if self.database:
            self.database.save_schedule(source_key, enabled, interval_minutes)

    def get_source_schedules(self) -> dict:
        self._init_schedules()
        return {key: sched.status() for key, sched in self.source_schedules.items()}

    def restore_schedules(self):
        """Restore schedule configs from database and start enabled timers."""
        if not self.database:
            log.info("No database, skipping schedule restore")
            return
        self._init_schedules()
        saved = self.database.get_all_schedules()
        for source_key, config in saved.items():
            if source_key not in self.scrapers:
                continue
            enabled = config.get("enabled", False)
            interval = config.get("interval_minutes", 60)
            if enabled:
                log.info("Restoring schedule: %s every %d minutes", source_key, interval)
                self.set_source_schedule(source_key, enabled, interval)
            else:
                # Still create the schedule state but disabled
                self._init_schedules()
                self.source_schedules[source_key].enabled = False
                self.source_schedules[source_key].interval_minutes = interval

    def stop_all_schedules(self):
        for sched in self.source_schedules.values():
            sched.stop()

    # -- Log reading (memory-efficient: read from end) --

    def read_logs(self, lines: int = 100) -> list[str]:
        """Read last N lines from log file without loading entire file."""
        log_path = self.base_dir / self.cfg["paths"]["log_file"]
        if not log_path.exists():
            return []

        # For small files, read normally
        if log_path.stat().st_size < 1024 * 100:  # < 100KB
            text = log_path.read_text(encoding="utf-8")
            all_lines = text.strip().split("\n")
            return all_lines[-lines:] if all_lines else []

        # For large files, read from end (seek + read line by line)
        result = []
        with open(log_path, "rb") as f:
            # Seek to end with buffer
            f.seek(0, 2)
            file_size = f.tell()
            pos = file_size - min(8192, file_size)  # Read last 8KB chunk
            f.seek(pos)

            # Read and decode
            chunk = f.read().decode("utf-8", errors="ignore")
            for line in chunk.split("\n"):
                if line.strip():
                    result.append(line.strip())

        return result[-lines:] if result else []
