# -*- coding: utf-8 -*-
"""Pipeline service: orchestration, state management, RunState, SchedulerState.

This is the canonical service used by the API routes.
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.loader import load_config
from pipelines import create_scrapers
from services.article_store import ArticleStore
from services.filter_service import FilterService
from services.publisher import Publisher
from services.push_scheduler import PushScheduler
from services.broadcast_scheduler import BroadcastScheduler
from services.auto_publish_scheduler import AutoPublishScheduler
from services.scorer import ScorerService
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
        self.filter_service = FilterService(database) if database else None
        self.scorer = ScorerService(database) if database else None
        self.push_scheduler = PushScheduler(self) if database else None
        self.broadcast_scheduler = BroadcastScheduler(self) if database else None
        self.auto_publish_scheduler = AutoPublishScheduler(self) if database else None
        if self.filter_service:
            self.filter_service.ensure_default_rules()
        if self.database:
            self._refresh_published_state()

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

        # Initialize database if configured (needed for scrapers with LLM features)
        database = None
        db_path = cfg.get("database", {}).get("sqlite_path")
        if db_path:
            from services.database import ArticleDatabase
            database = ArticleDatabase(base_dir / db_path)
            log.info("Database initialized: %s", base_dir / db_path)

        # Build components
        scrapers = create_scrapers(cfg, session, base_dir, db=database)

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
            push_url=cfg["chainthink"].get("push_url", ""),
        )

        article_store = ArticleStore(scrapers)
        state_file = base_dir / cfg["paths"]["state_file"]

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

    def _refresh_published_state(self):
        """Keep the dedupe state aligned with truly public CMS articles only."""
        if not self.database:
            return
        state = self.load_state()
        state["published_ids"] = sorted(set(self.database.get_published_ids()))
        if self.database:
            state["published_ids"] = sorted(set(self.database.get_published_ids()))
        self.save_state(state)

    def get_managed_source_keys(self) -> list[str]:
        """Return blockchain source keys handled by this service."""
        return list(self.scrapers.keys())

    def _canonical_article_id(self, source_key: str, article_or_id) -> str:
        if isinstance(article_or_id, dict):
            article_id = article_or_id.get("article_id", "")
            raw_id = article_or_id.get("raw_id", "")
        else:
            article_id = str(article_or_id or "")
            raw_id = ""
        if article_id and ":" in article_id:
            return article_id
        raw = raw_id or article_id
        return f"{source_key}:{raw}" if raw else raw

    def _store_and_score_article(self, article: dict):
        """Persist article, generate abstract, score it and assign a review lane."""
        if not self.database:
            return

        article["article_id"] = self._canonical_article_id(article.get("source_key", ""), article)
        self.database.insert_or_update(article)

        from services.llm import generate_abstract

        ai_abstract = generate_abstract(article, self.database)
        self.database.update_abstract(article["article_id"], ai_abstract)
        article["abstract"] = ai_abstract

        if not self.scorer:
            return

        score_result = self.scorer.score_article(article)
        self.database.update_scoring(
            article_id=article["article_id"],
            score=score_result["score"],
            score_reason=score_result["reason"],
            tags=score_result["tags"],
            review_status=score_result["review_status"],
            auto_publish_enabled=score_result["auto_publish_enabled"],
            score_status="done",
        )

        # Auto-save CMS draft for 70+ scored articles
        score = score_result["score"]
        if score is not None and score >= 70:
            # LLM 优化文章（如果启用）
            enable_llm_optimization = self.database.get_setting("llm_optimization_enabled") == "true"
            enable_author_info = self.database.get_setting("llm_author_info_enabled") == "true"

            if enable_llm_optimization:
                try:
                    from services.llm import optimize_article_for_publishing
                    article = optimize_article_for_publishing(
                        article,
                        self.database,
                        enable_author_info=enable_author_info
                    )
                    log.info("LLM optimization completed for %s (score=%d)", article["article_id"], score)
                except Exception as exc:
                    log.warning("LLM optimization failed for %s: %s", article["article_id"], exc)

            try:
                self.save_article_draft(article, strategy="auto_score")
                log.info("Auto-saved draft for %s (score=%d)", article["article_id"], score)
            except Exception as exc:
                log.warning("Auto-draft save failed for %s: %s", article["article_id"], exc)

    def get_workflow_status(self) -> dict:
        """Expose auto-publish metrics and recent history for the dashboard."""
        if not self.database:
            return {"metrics": {}, "scheduler": {}, "broadcast": {}}
        return {
            "metrics": self.database.get_source_metrics(self.get_managed_source_keys()),
            "scheduler": self.auto_publish_scheduler.get_status() if self.auto_publish_scheduler else {},
            "broadcast": self.auto_publish_scheduler.get_status() if self.auto_publish_scheduler else {},
        }

    def _merge_database_article_fields(self, article: dict) -> dict:
        """Merge CMS-related fields from the database before a CMS submit."""
        if not self.database:
            return article

        article_id = self._canonical_article_id(article.get("source_key", ""), article)
        db_article = self.database.get_by_article_id(article_id) if article_id else None
        if not db_article:
            return article

        merged = dict(db_article)
        merged.update(article)
        if db_article.get("abstract") and not merged.get("abstract"):
            merged["abstract"] = db_article["abstract"]
        if db_article.get("cms_id") and not merged.get("cms_id"):
            merged["cms_id"] = db_article["cms_id"]
        if db_article.get("publish_stage") and not merged.get("publish_stage"):
            merged["publish_stage"] = db_article["publish_stage"]
        return merged

    def save_article_draft(self, article: dict, strategy: str = "manual") -> dict:
        """Create or update a CMS draft without making it public."""
        prepared = self._merge_database_article_fields(article)
        result = self.publisher.save_draft(prepared)
        prepared["cms_id"] = result["cms_id"]
        prepared["publish_stage"] = "draft"
        if self.database:
            self.database.mark_cms_draft(prepared["article_id"], result["cms_id"], strategy=strategy)
        return result

    def publish_article(self, article: dict, strategy: str = "manual") -> dict:
        """Create or update a CMS article and make it public."""
        prepared = self._merge_database_article_fields(article)
        result = self.publisher.publish(prepared)
        prepared["cms_id"] = result["cms_id"]
        prepared["publish_stage"] = "published"

        if self.database:
            self.database.mark_published(prepared["article_id"], result["cms_id"], strategy=strategy)

        state = self.load_state()
        published_ids = set(state.get("published_ids", []))
        published_ids.add(prepared["article_id"])
        state["published_ids"] = sorted(published_ids)
        self.save_state(state)
        return result

    def broadcast_article(self, article: dict, strategy: str = "manual") -> dict:
        """Push a published article to App desktop notification."""
        cms_id = article.get("cms_id")
        if not cms_id:
            raise RuntimeError("Article must be published (have cms_id) before broadcast")

        score = article.get("score") or 0
        push_label = "爆文" if score >= 85 else "热文"

        result = self.publisher.push_to_app(
            cms_id=cms_id,
            title=article.get("title", ""),
            push_label=push_label if strategy == "auto" else "",
        )

        if self.database:
            self.database.mark_broadcasted(article["article_id"], strategy=strategy)
            self.database.record_broadcast_history(
                article_id=article["article_id"],
                source_key=article.get("source_key", ""),
                cms_id=cms_id,
                push_title=push_label if strategy == "auto" else (article.get("title", "") or "")[:120],
                score=score,
                strategy=strategy,
                result="ok",
            )

        return result

    def auto_publish_and_broadcast(self, article: dict, push_label: str = "热文") -> dict:
        """Atomic: publish article to CMS then broadcast to App.

        Used by AutoPublishScheduler for the unified publish+push flow.
        """
        prepared = self._merge_database_article_fields(article)

        # Step 1: Publish to CMS
        pub_result = self.publisher.publish(prepared)
        cms_id = pub_result["cms_id"]
        prepared["cms_id"] = cms_id
        prepared["publish_stage"] = "published"

        if self.database:
            self.database.mark_published(prepared["article_id"], cms_id, strategy="auto")

        state = self.load_state()
        published_ids = set(state.get("published_ids", []))
        published_ids.add(prepared["article_id"])
        state["published_ids"] = sorted(published_ids)
        self.save_state(state)

        # Step 2: Broadcast to App
        push_result = self.publisher.push_to_app(
            cms_id=cms_id,
            title=article.get("title", ""),
            push_label=push_label,
        )

        if self.database:
            self.database.mark_broadcasted(prepared["article_id"], strategy="auto")
            self.database.record_broadcast_history(
                article_id=prepared["article_id"],
                source_key=prepared.get("source_key", ""),
                cms_id=cms_id,
                push_title=push_label,
                score=prepared.get("score"),
                strategy="auto",
                result="ok",
            )
            # Also record in push_history for window dedup
            now = datetime.now()
            window_start = self.auto_publish_scheduler._window_start(now) if self.auto_publish_scheduler else now
            self.database.record_push_history(
                article_id=prepared["article_id"],
                source_key=prepared.get("source_key", ""),
                score=prepared.get("score"),
                cms_id=cms_id,
                window_start=window_start,
                strategy="auto",
            )

        return {"cms_id": cms_id, "push": push_result}

    # -- Orchestration --

    def ingest_sources(self, source: str, state: dict) -> list[str]:
        fetched = []
        published_ids = set(state.get("published_ids", []))

        if self.database:
            published_ids.update(self.database.get_published_ids())

        for key, scraper in self.scrapers.items():
            if self.run_state.cancelled:
                log.warning("Ingest cancelled, stopping early")
                break

            src_cfg = self.cfg.get("sources", {}).get(key, {})
            if source not in (key, "all") or not src_cfg.get("enabled", True):
                continue

            for item in scraper.parse_list():
                if self.run_state.cancelled:
                    break

                canonical_id = self._canonical_article_id(key, item.get("article_id", ""))
                title = item.get("title", "")

                if canonical_id in published_ids:
                    continue

                if self.filter_service and title:
                    title_hit = self.filter_service.check_title(key, title)
                    if title_hit:
                        log.info("Skip %s before fetch by title rule: %s", canonical_id, title_hit["reason"])
                        continue

                if self.database:
                    existing = self.database.get_by_article_id(canonical_id)
                    if existing:
                        continue

                    if self.filter_service and title:
                        duplicate_key, duplicate = self.filter_service.check_duplicate(
                            self.get_managed_source_keys(),
                            title,
                        )
                        if duplicate:
                            log.info(
                                "Skip duplicate list item %s -> %s (key=%s)",
                                canonical_id,
                                duplicate.get("article_id", ""),
                                duplicate_key,
                            )
                            continue

                try:
                    article = scraper.fetch_detail(item)
                    article["source_key"] = key
                    article["article_id"] = self._canonical_article_id(key, article)

                    if self.filter_service:
                        title_hit = self.filter_service.check_title(key, article.get("title", ""))
                        if title_hit:
                            log.info("Skip %s after detail fetch by title rule: %s", article["article_id"], title_hit["reason"])
                            continue

                        duplicate_key, duplicate = self.filter_service.check_duplicate(
                            self.get_managed_source_keys(),
                            article.get("title", ""),
                            exclude_article_id=article["article_id"],
                        )
                        article["duplicate_key"] = duplicate_key
                        if duplicate:
                            log.info(
                                "Skip duplicate article %s -> %s (key=%s)",
                                article["article_id"],
                                duplicate.get("article_id", ""),
                                duplicate_key,
                            )
                            continue

                        article = self.filter_service.clean_article(article)
                        if article.get("filter_status") == "blocked":
                            log.info("Skip %s by content rule: %s", article["article_id"], article.get("filter_reason", ""))
                            continue

                    scraper.save(article)
                    if self.database:
                        self._store_and_score_article(article)

                    fetched.append(article["article_id"])
                    log.info("Fetched %s: %s - %s", key.upper(), article["article_id"], article.get("title", "")[:40])
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
            refetch_odaily_urls=None,
            dry_run=False, republish_refetched=False) -> dict:
        """Run fetch/refetch workflow. Default mode only ingests and scores articles."""
        started = datetime.now()
        log.info("Pipeline started: source=%s dry_run=%s", source, dry_run)

        state = self.load_state()
        if self.database:
            state["published_ids"] = sorted(set(self.database.get_published_ids()))

        refetch_mode = bool(refetch_stcn_urls or refetch_techflow_ids or refetch_blockbeats_urls or refetch_chaincatcher_urls or refetch_odaily_urls)
        refreshed = []

        if refetch_mode:
            refreshed = self._do_refetch_v2(source, refetch_stcn_urls, refetch_techflow_ids, refetch_blockbeats_urls, refetch_chaincatcher_urls, refetch_odaily_urls)
        elif not skip_fetch:
            refreshed = [{"id": aid} for aid in self.ingest_sources(source, state)]

        republish_set = set(republish_ids or [])

        if refetch_mode and republish_refetched and not republish_set:
            republish_set = {item.get("id", "") for item in refreshed if item.get("id")}

        if refetch_mode and not republish_set:
            self.save_state(state)
            return {"ok": True, "refetched": refreshed, "ingested": len(refreshed), "published": [], "skipped": [], "failed": []}

        published = []
        failed = []

        if republish_set and not dry_run:
            for article_id in republish_set:
                if self.run_state.cancelled:
                    break
                article = self.article_store.get_article(article_id)
                if not article and self.database:
                    article = self.database.get_by_article_id(article_id)
                if not article:
                    failed.append({"id": article_id, "error": "article not found", "source": ""})
                    continue
                try:
                    result = self.publish_article(article, strategy="manual")
                    published.append({
                        "article_id": article["article_id"],
                        "cms_id": result["cms_id"],
                        "title": article.get("title", ""),
                        "cover_image": result.get("cover_image", ""),
                    })
                except Exception as e:
                    failed.append({"id": article_id, "error": str(e), "source": article.get("source_key", "")})

        self.save_state(state)

        elapsed = (datetime.now() - started).total_seconds()
        log.info("Pipeline done: ingested=%d published=%d failed=%d %.1fs", len(refreshed), len(published), len(failed), elapsed)

        self.cleanup_old_articles(days=7)

        return {
            "ok": True,
            "refetched": refreshed,
            "ingested": len(refreshed),
            "published": published,
            "skipped": [],
            "failed": failed,
        }

    def _do_refetch(self, source, stcn_urls, techflow_ids, blockbeats_urls, chaincatcher_urls=None, odaily_urls=None):
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
        return refreshed

    def _refetch_and_process(self, scraper, item: dict) -> dict | None:
        article = scraper.fetch_detail(item)
        article["source_key"] = scraper.source_key
        article["article_id"] = self._canonical_article_id(scraper.source_key, article)

        if self.filter_service:
            title_hit = self.filter_service.check_title(scraper.source_key, article.get("title", ""))
            if title_hit:
                log.info("Refetch skipped by title rule: %s (%s)", article["article_id"], title_hit["reason"])
                return None

            duplicate_key, duplicate = self.filter_service.check_duplicate(
                self.get_managed_source_keys(),
                article.get("title", ""),
                exclude_article_id=article["article_id"],
            )
            article["duplicate_key"] = duplicate_key
            if duplicate:
                log.info("Refetch skipped duplicate: %s -> %s", article["article_id"], duplicate.get("article_id", ""))
                return None

            article = self.filter_service.clean_article(article)
            if article.get("filter_status") == "blocked":
                return None

        path = scraper.save(article)
        if self.database:
            self._store_and_score_article(article)
        return {"id": article["article_id"], "path": str(path)}

    def _do_refetch_v2(self, source, stcn_urls, techflow_ids, blockbeats_urls, chaincatcher_urls=None, odaily_urls=None):
        refreshed = []
        if source in ("stcn", "all") and stcn_urls:
            scraper = self.scrapers.get("stcn")
            if scraper:
                for url in stcn_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        result = self._refetch_and_process(scraper, item)
                        if result:
                            refreshed.append(result)
                    except Exception as e:
                        log.error("Refetch STCN %s failed: %s", url, e)
        if source in ("techflow", "all") and techflow_ids:
            scraper = self.scrapers.get("techflow")
            if scraper:
                tf_items = {it["article_id"]: it for it in scraper.parse_list()}
                for aid in techflow_ids:
                    item = tf_items.get(str(aid)) or {
                        "article_id": str(aid),
                        "title": str(aid),
                        "original_url": f"https://www.techflowpost.com/zh-CN/article/{aid}",
                        "source": "TechFlow",
                    }
                    try:
                        result = self._refetch_and_process(scraper, item)
                        if result:
                            refreshed.append(result)
                    except Exception as e:
                        log.error("Refetch TechFlow %s failed: %s", aid, e)
        if source in ("blockbeats", "all") and blockbeats_urls:
            scraper = self.scrapers.get("blockbeats")
            if scraper:
                for url in blockbeats_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        result = self._refetch_and_process(scraper, item)
                        if result:
                            refreshed.append(result)
                    except Exception as e:
                        log.error("Refetch BlockBeats %s failed: %s", url, e)
        if source in ("chaincatcher", "all") and chaincatcher_urls:
            scraper = self.scrapers.get("chaincatcher")
            if scraper:
                for url in chaincatcher_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        result = self._refetch_and_process(scraper, item)
                        if result:
                            refreshed.append(result)
                    except Exception as e:
                        log.error("Refetch ChainCatcher %s failed: %s", url, e)
        if source in ("odaily", "all") and odaily_urls:
            scraper = self.scrapers.get("odaily")
            if scraper:
                for url in odaily_urls:
                    try:
                        item = scraper.build_item_from_url(url)
                        result = self._refetch_and_process(scraper, item)
                        if result:
                            refreshed.append(result)
                    except Exception as e:
                        log.error("Refetch Odaily %s failed: %s", url, e)
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
        if self.auto_publish_scheduler:
            self.auto_publish_scheduler.stop()
        if self.push_scheduler:
            self.push_scheduler.stop()
        if self.broadcast_scheduler:
            self.broadcast_scheduler.stop()

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
