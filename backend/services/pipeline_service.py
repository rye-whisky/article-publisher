# -*- coding: utf-8 -*-
"""Pipeline service: orchestration, state management, RunState, SchedulerState.

This is the canonical service used by the API routes.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from pipelines import create_scrapers
from services.article_store import ArticleStore
from services.publisher import Publisher
from utils.cos import COSUploader

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# State trackers
# ---------------------------------------------------------------------------

class RunState:
    """Thread-safe tracker for background pipeline runs."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.started_at: Optional[str] = None
        self.result: Optional[dict] = None

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            return True

    def finish(self, result: dict):
        with self._lock:
            self.running = False
            self.result = result

    def status(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "last_result": self.result,
        }


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
                 publisher: Publisher, article_store: ArticleStore, state_file: Path):
        self.cfg = cfg
        self.base_dir = base_dir
        self.session = session
        self.scrapers = scrapers
        self.publisher = publisher
        self.article_store = article_store
        self.state_file = state_file
        self.run_state = RunState()
        self.source_schedules: dict[str, SourceScheduleState] = {}

    # -- Factory --

    @classmethod
    def create(cls, base_dir: Path = None) -> "PipelineService":
        """Build a PipelineService from config.yaml."""
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent.parent

        import re
        import os
        import yaml
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        # Load config
        config_path = base_dir / "config.yaml"
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        def _expand_env(value):
            if not isinstance(value, str):
                return value
            return re.sub(r'\$\$(\w+)', lambda m: os.environ.get(m.group(1), ''), value)

        def _expand_recursive(obj):
            if isinstance(obj, str):
                return _expand_env(obj)
            if isinstance(obj, dict):
                return {k: _expand_recursive(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_expand_recursive(v) for v in obj]
            return obj

        cfg = _expand_recursive(raw)

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

        return cls(cfg, base_dir, session, scrapers, publisher, article_store, state_file)

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
        for key, scraper in self.scrapers.items():
            src_cfg = self.cfg.get("sources", {}).get(key, {})
            if source not in (key, "all"):
                continue
            if not src_cfg.get("enabled", True):
                continue
            for item in scraper.parse_list():
                aid = f"{key}:{item['article_id']}"
                if aid in published_ids:
                    continue
                try:
                    article = scraper.fetch_detail(item)
                    scraper.save(article)
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
            refetch_blockbeats_urls=None, dry_run=False) -> dict:
        """Run the full pipeline. Returns result dict."""
        started = datetime.now()
        log.info("Pipeline started: source=%s dry_run=%s", source, dry_run)

        state = self.load_state()
        refetch_mode = bool(refetch_stcn_urls or refetch_techflow_ids or refetch_blockbeats_urls)
        refreshed = []

        if refetch_mode:
            refreshed = self._do_refetch(source, refetch_stcn_urls, refetch_techflow_ids, refetch_blockbeats_urls)
        elif not skip_fetch:
            self.ingest_sources(source, state)

        published_ids = set(state.get("published_ids", []))
        republish_set = set(republish_ids or [])

        if refetch_mode and not republish_set:
            self.save_state(state)
            return {"ok": True, "refetched": refreshed, "published": [], "skipped": [], "failed": []}

        articles = self.load_articles(source)
        accepted, skipped = [], []

        # Author filtering for STCN
        stcn_scraper = self.scrapers.get("stcn")
        allowed_authors = stcn_scraper.allowed_authors if stcn_scraper else set()

        for article in articles:
            aid = article["article_id"]
            sk = article.get("source_key", "")

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
                try:
                    result = self.publisher.publish(article)
                    published.append({
                        "article_id": article["article_id"],
                        "cms_id": result["cms_id"],
                        "title": article["title"],
                        "cover_image": result.get("cover_image", ""),
                    })
                    published_ids.add(article["article_id"])
                    log.info("Published %s -> CMS %s", article["article_id"], result["cms_id"])
                except Exception as e:
                    failed.append({"id": article["article_id"], "error": str(e), "source": article.get("source_key", "")})
                    log.error("Publish failed %s: %s", article["article_id"], e)

        state["published_ids"] = sorted(published_ids)
        self.save_state(state)

        elapsed = (datetime.now() - started).total_seconds()
        log.info("Pipeline done: published=%d skipped=%d failed=%d %.1fs", len(published), len(skipped), len(failed), elapsed)
        return {"ok": True, "refetched": refreshed, "published": published, "skipped": skipped, "failed": failed}

    def _do_refetch(self, source, stcn_urls, techflow_ids, blockbeats_urls):
        refreshed = []
        if source in ("stcn", "all") and stcn_urls:
            scraper = self.scrapers.get("stcn")
            if scraper:
                for url in stcn_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        article = scraper.fetch_detail(item)
                        path = scraper.save(article)
                        refreshed.append({"id": article["article_id_full"], "path": str(path)})
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
                        refreshed.append({"id": article["article_id_full"], "path": str(path)})
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
                        refreshed.append({"id": article.get("article_id_full", ""), "path": str(path)})
                    except Exception as e:
                        log.error("Refetch BlockBeats %s failed: %s", url, e)
        return refreshed

    # -- Per-source scheduler helpers --

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

    def get_source_schedules(self) -> dict:
        self._init_schedules()
        return {key: sched.status() for key, sched in self.source_schedules.items()}

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
