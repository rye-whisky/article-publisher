# -*- coding: utf-8 -*-
"""Unified auto-publish + app broadcast scheduler.

Rules:
  - Active windows (8-23): articles scoring >= push_auto_score trigger publish.
  - Before publishing, semantic dedup against the last 6 auto-published articles.
  - Auto-publish ALWAYS includes App broadcast (push_title="爆文"/"热文").
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta

log = logging.getLogger("pipeline")

ACTIVE_START = 8
ACTIVE_END = 23
MORNING_START = 8
MORNING_END = 10
AUTO_SKIP_STRATEGY = "auto_skip"
AUTO_SKIP_IN_SITE_INTERVAL = "auto_skip_in_site_interval"
AUTO_SKIP_IN_SITE_UNAVAILABLE = "auto_skip_in_site_unavailable"
AUTO_SKIP_IN_SITE_TIME_UNPARSEABLE = "auto_skip_in_site_time_unparseable"
DEFAULT_IN_SITE_ARTICLE_URL = "https://chainthink.cn/zh-CN/article"

EXPLOSIVE_THRESHOLD = 85  # 爆文
HOT_THRESHOLD = 75        # 热文


class AutoPublishScheduler:
    """Run window-based auto publish + app broadcast checks."""

    def __init__(self, pipeline_service):
        self.pipeline_service = pipeline_service
        self.database = pipeline_service.database
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="auto-publish-scheduler")
        self._thread.start()
        log.info("AutoPublishScheduler started")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def run_once(self) -> dict:
        """Run a single scheduling cycle."""
        if not self.database:
            return {"ok": False, "reason": "database_not_configured"}

        if not self._is_enabled():
            return {"ok": True, "reason": "disabled"}

        context = self.get_window_context()
        if not context["active"]:
            return {
                "ok": True,
                "reason": "outside_active_hours",
                "next_window_start": context["window_start"].isoformat(),
                "next_window_end": context["window_end"].isoformat(),
            }

        window_start = context["window_start"]
        window_end = context["window_end"]
        auto_sources = context["auto_sources"]
        is_morning = context["is_morning"]
        max_per_window = self._get_int_setting("push_max_per_window", 1)

        # Check if window already has a publish
        pushed_in_window = self.database.count_pushes_in_window(window_start, strategy="auto")
        if pushed_in_window >= max_per_window:
            return {"ok": True, "reason": "window_full", "window_start": window_start.isoformat()}

        # 自动发布阈值由后台设置控制，默认沿用 75 分。
        min_score = self._get_auto_publish_score()

        # Query candidates with determined min_score
        candidates = self.database.get_auto_publish_broadcast_candidates(
            min_score=min_score,
            limit=max(10, max_per_window * 10),
            source_keys=auto_sources,
            window_start=window_start,
            window_end=window_end,
        )
        if not candidates:
            return {"ok": True, "reason": "no_candidates"}

        return self._try_publish_candidates(
            candidates, window_start, auto_sources, is_fallback=not is_morning
        )

    def _check_duplicate_via_keywords_and_semantic(self, article: dict) -> bool:
        """Check if article duplicates recently published ones via keywords + LLM."""
        if not article.get("title"):
            return False

        # Get keywords from article (extract if not present)
        keywords = article.get("keywords") or []
        if not keywords:
            from services.scorer import ScorerService
            keywords = ScorerService._extract_keywords(article)

        if not keywords:
            return False

        # Find recent published articles with overlapping keywords
        overlap_candidates = self.database.find_recent_by_keyword_overlap(
            keywords,
            days=7,
            min_overlap=2,
            limit=6,
            exclude_article_id=article.get("article_id"),
        )

        if not overlap_candidates:
            return False

        log.info(
            "[AutoPublish] Found %d keyword-overlap candidates for '%s': %s",
            len(overlap_candidates),
            article.get("title", "")[:40],
            [c.get("title", "")[:20] for c in overlap_candidates],
        )

        # Use enhanced semantic dedup with candidate articles (title + abstract)
        from services.llm import semantic_dedup

        return semantic_dedup(
            title=article.get("title", ""),
            recent_titles=[],
            db=self.database,
            abstract=article.get("abstract", ""),
            candidate_articles=overlap_candidates,
        )

    def _try_publish_candidates(
        self,
        candidates: list[dict],
        window_start: datetime,
        auto_sources: list[str],
        *,
        is_fallback: bool = False,
    ) -> dict:
        """Try candidates in order: dedup → exclude → in-site interval → publish + broadcast."""
        in_site_articles = []
        in_site_fetch_failed = False
        if self._is_in_site_check_enabled():
            try:
                from services.llm import fetch_website_articles

                in_site_articles = fetch_website_articles(
                    limit=self._get_int_setting("push_in_site_limit", 10),
                    url=self._get_setting("push_in_site_article_url", DEFAULT_IN_SITE_ARTICLE_URL),
                )
            except Exception as exc:
                in_site_fetch_failed = True
                log.warning("AutoPublishScheduler failed to fetch in-site articles: %s", exc)
            if not in_site_articles:
                in_site_fetch_failed = True

        for chosen in candidates:
            score = chosen.get("score") or 0
            title = chosen.get("title", "")
            source_key = chosen.get("source_key", "")

            excluded, keyword = self._is_auto_publish_excluded(chosen)
            if excluded:
                log.info("AutoPublishScheduler skip %s: title excluded by %s", chosen["article_id"], keyword)
                self._record_auto_skip(chosen, window_start)
                continue

            # Enhanced dedup: keyword overlap + semantic (title + abstract)
            is_dup = self._check_duplicate_via_keywords_and_semantic(chosen)
            if is_dup:
                log.info("AutoPublishScheduler skip %s: keyword+semantic duplicate", chosen["article_id"])
                self._record_auto_skip(chosen, window_start)
                continue

            if self._is_in_site_check_enabled():
                if in_site_fetch_failed:
                    if self._is_in_site_check_strict():
                        # Strict mode: skip all candidates when in-site fetch fails
                        log.info("AutoPublishScheduler skip %s: in-site articles unavailable (strict mode)", chosen["article_id"])
                        self._record_auto_skip(
                            chosen,
                            window_start,
                            strategy=AUTO_SKIP_IN_SITE_UNAVAILABLE,
                            skip_reason="failed_to_fetch_in_site_articles",
                            in_site_article={"url": self._get_setting("push_in_site_article_url", DEFAULT_IN_SITE_ARTICLE_URL)},
                        )
                        continue
                    else:
                        # Fallback mode: log warning but proceed with publishing
                        log.warning("AutoPublishScheduler: in-site articles unavailable, proceeding due to fallback mode (aid=%s)", chosen["article_id"])

                allowed, matched_article, skip_reason = self._check_in_site_publish_interval(
                    chosen,
                    in_site_articles,
                    self._get_int_setting("push_in_site_min_interval_minutes", 30),
                )
                if not allowed:
                    strategy = AUTO_SKIP_IN_SITE_INTERVAL
                    if "unparseable" in skip_reason:
                        strategy = AUTO_SKIP_IN_SITE_TIME_UNPARSEABLE
                    log.info(
                        "AutoPublishScheduler skip %s: %s (site=%s)",
                        chosen["article_id"],
                        skip_reason,
                        (matched_article or {}).get("url", ""),
                    )
                    self._record_auto_skip(
                        chosen,
                        window_start,
                        strategy=strategy,
                        skip_reason=skip_reason,
                        in_site_article=matched_article,
                    )
                    continue

            push_label = self.pipeline_service.get_push_label(score) or "热文"
            broadcast_enabled = self._is_broadcast_enabled()

            try:
                if broadcast_enabled:
                    result = self.pipeline_service.auto_publish_and_broadcast(
                        chosen,
                        push_label=push_label,
                        window_start=window_start,
                    )
                    cms_id = result.get("cms_id", "")
                else:
                    result = self.pipeline_service.publish_article(chosen, strategy="auto")
                    cms_id = result.get("cms_id", "")
                    self.database.record_push_history(
                        article_id=chosen["article_id"],
                        source_key=source_key,
                        score=score,
                        cms_id=cms_id,
                        window_start=window_start,
                        strategy="auto",
                    )

                log.info(
                    "AutoPublishScheduler publish %s (score=%s, cms_id=%s, label=%s, source=%s, broadcast=%s, fallback=%s)",
                    chosen["article_id"],
                    score,
                    cms_id,
                    push_label,
                    source_key,
                    broadcast_enabled,
                    is_fallback,
                )
                self._cleanup_stale_candidates(chosen["article_id"], window_start)
                return {
                    "ok": True,
                    "reason": "published_and_broadcasted" if broadcast_enabled else "published",
                    "article_id": chosen["article_id"],
                    "cms_id": cms_id,
                    "score": score,
                    "push_label": push_label if broadcast_enabled else "",
                }
            except Exception as exc:
                log.error("AutoPublishScheduler failed for %s: %s", chosen.get("article_id", ""), exc)
                continue

        return {"ok": True, "reason": "no_eligible_candidates"}

    # -- Status helpers --

    def get_status(self) -> dict:
        """Return scheduler config and recent history."""
        enabled = self._is_enabled()
        interval = self._get_int_setting("push_check_interval_minutes", 10)
        window_hours = self._get_int_setting("push_window_hours", 2)
        history = []
        if self.database:
            history = self.database.list_push_history(limit=8)
        return {
            "enabled": enabled,
            "window_hours": window_hours,
            "active_hours": {"start_hour": ACTIVE_START, "end_hour": ACTIVE_END},
            "morning_window": {"start_hour": MORNING_START, "end_hour": MORNING_END},
            "auto_score": self._get_auto_publish_score(),
            "hot_score": self._get_auto_publish_score(),
            "explosive_score": EXPLOSIVE_THRESHOLD,
            "review_score": self._get_int_setting("push_review_score", 70),
            "max_per_window": self._get_int_setting("push_max_per_window", 1),
            "check_interval_minutes": interval,
            "auto_sources": sorted(self._get_auto_sources()),
            "history": history,
        }

    def get_broadcast_status(self) -> dict:
        """Return broadcast config and recent history."""
        history = []
        if self.database:
            history = self.database.list_broadcast_history(limit=8)
        return {
            "enabled": self._is_broadcast_enabled(),
            "grace_minutes": self._get_int_setting("broadcast_grace_minutes", 15),
            "history": history,
        }

    # -- Window helpers --

    def get_window_context(self, now: datetime | None = None) -> dict:
        """Return the currently active publish window context."""
        current = now or datetime.now()
        if not self._is_active_time(current):
            next_window_start = self._next_active_start(current)
            next_window_end = next_window_start.replace(hour=MORNING_END, minute=0, second=0, microsecond=0)
            return {
                "active": False,
                "now": current,
                "is_morning": True,
                "window_start": next_window_start,
                "window_end": next_window_end,
                "min_score": self._get_auto_publish_score(),
                "auto_sources": sorted(self._get_auto_sources()),
            }

        is_morning = self._is_morning_window(current)
        window_start = self._window_start(current)
        window_end = self._window_end(window_start)
        return {
            "active": True,
            "now": current,
            "is_morning": is_morning,
            "window_start": window_start,
            "window_end": window_end,
            "min_score": self._get_auto_publish_score(),
            "auto_sources": sorted(self._get_auto_sources()),
        }

    def _window_start(self, now: datetime) -> datetime:
        if self._is_morning_window(now):
            return now.replace(hour=MORNING_START, minute=0, second=0, microsecond=0)
        window_hours = max(1, self._get_int_setting("push_window_hours", 2))
        base_hour = max(MORNING_END, (now.hour // window_hours) * window_hours)
        return now.replace(hour=base_hour, minute=0, second=0, microsecond=0)

    def _window_end(self, window_start: datetime) -> datetime:
        window_hours = max(1, self._get_int_setting("push_window_hours", 2))
        proposed = window_start + timedelta(hours=window_hours)
        day_end = window_start.replace(hour=ACTIVE_END, minute=0, second=0, microsecond=0)
        return proposed if proposed <= day_end else day_end

    # -- Retry stale local articles --

    def _retry_stale_local_articles(self):
        """Retry CMS draft saves for articles stuck at publish_stage='local'.

        Recovers articles that scored well but failed to enter CMS due to
        transient errors (e.g. expired token). Runs each scheduler cycle.
        """
        all_sources = self.pipeline_service.get_managed_source_keys()
        if not all_sources:
            return

        stale = self.database.get_stale_local_scored_articles(
            source_keys=all_sources,
            min_score=70,
            limit=5,
        )
        if not stale:
            return

        log.info("AutoPublishScheduler retrying %d stale local articles", len(stale))
        for article in stale:
            aid = article["article_id"]
            try:
                self.pipeline_service.save_article_draft(article, strategy="auto_score")
                log.info("Retry draft save succeeded for %s (score=%s)", aid, article.get("score"))
            except Exception as exc:
                log.warning("Retry draft save failed for %s: %s", aid, exc)

    # -- Loop --

    def _loop(self):
        while not self._stop_event.is_set():
            interval_minutes = self._get_int_setting("push_check_interval_minutes", 10)
            self._stop_event.wait(max(1, interval_minutes) * 60)
            if self._stop_event.is_set():
                break
            try:
                self._retry_stale_local_articles()
                self.run_once()
            except Exception as exc:
                log.error("AutoPublishScheduler loop failed: %s", exc)

    # -- Utility methods --

    def _record_auto_skip(
        self,
        article: dict,
        window_start: datetime,
        strategy: str = AUTO_SKIP_STRATEGY,
        skip_reason: str = "",
        in_site_article: dict | None = None,
    ) -> None:
        in_site_article = in_site_article or {}
        self.database.record_push_history(
            article_id=article["article_id"],
            source_key=article.get("source_key", ""),
            score=article.get("score"),
            cms_id="",
            window_start=window_start,
            strategy=strategy,
            skip_reason=skip_reason,
            in_site_article_url=in_site_article.get("url", ""),
            in_site_article_title=in_site_article.get("title", ""),
            in_site_article_published_at=in_site_article.get("published_at", ""),
        )
        if in_site_article:
            self.database.record_in_site_conflict(article["article_id"], in_site_article, skip_reason)

    def _check_in_site_publish_interval(
        self,
        article: dict,
        in_site_articles: list[dict],
        min_minutes: int,
    ) -> tuple[bool, dict | None, str]:
        candidate_time = self._parse_article_time(
            article.get("source_publish_time")
            or article.get("publish_time")
            or article.get("published_at")
            or article.get("scored_at")
            or article.get("created_at")
        )
        if not candidate_time:
            return False, None, "candidate_time_unparseable"

        parsed_articles: list[tuple[dict, datetime]] = []
        first_article = in_site_articles[0] if in_site_articles else None
        for item in in_site_articles:
            published_at = self._parse_article_time(item.get("published_at") or item.get("raw_time"))
            if published_at:
                parsed_articles.append((item, published_at))

        if not parsed_articles:
            return False, first_article, "all_in_site_article_times_unparseable"

        threshold = timedelta(minutes=max(0, min_minutes))
        for item, published_at in parsed_articles:
            interval = abs(candidate_time - published_at)
            if interval <= threshold:
                return False, item, f"in_site_interval_not_greater_than_{min_minutes}_minutes"
        return True, None, ""

    @staticmethod
    def _parse_article_time(value: str | None) -> datetime | None:
        raw = (value or "").strip()
        if not raw:
            return None
        text = raw.replace("Z", "").strip()
        text = re.sub(r"([+-]\d{2}:?\d{2})$", "", text).strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass

        text = text.replace("年", "-").replace("月", "-").replace("日", " ").replace("/", "-")

        # Try YYYY-MM-DD format first
        match = re.search(r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
        if match:
            value = match.group(0)
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue

        # Try MM-DD HH:MM format (without year), use current year
        match = re.search(r"(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
        if match:
            try:
                month, day = int(match.group(1)), int(match.group(2))
                hour, minute = 0, 0
                if match.group(3):
                    hour, minute = int(match.group(3)), int(match.group(4))
                current_year = datetime.now().year
                return datetime(current_year, month, day, hour, minute)
            except (ValueError, TypeError):
                pass

        return None

    def _get_setting(self, key: str, default: str = "") -> str:
        raw = self.database.get_setting(key)
        return raw if raw not in {None, ""} else default

    def _is_in_site_check_enabled(self) -> bool:
        # Default enabled for 30-min interval checking
        raw = (self.database.get_setting("push_in_site_check_enabled") or "1").strip().lower()
        return raw in {"1", "true", "on", "yes"}

    def _is_in_site_check_strict(self) -> bool:
        raw = (self.database.get_setting("push_in_site_check_strict") or "1").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def _cleanup_stale_candidates(self, published_id: str, window_start: datetime) -> int:
        """Mark stale candidates (scored before current window) as ineligible."""
        count = self.database.cleanup_stale_candidates(published_id, window_start)
        if count > 0:
            log.info("AutoPublishScheduler cleaned up %d stale candidates (before %s)", count, window_start.isoformat())
        return count

    def _is_auto_publish_excluded(self, article: dict) -> tuple[bool, str]:
        filter_service = getattr(self.pipeline_service, "filter_service", None)
        if not filter_service:
            return False, ""
        return filter_service.should_exclude_from_auto_publish(
            article.get("source_key", ""),
            article.get("title", ""),
        )

    def _get_int_setting(self, key: str, default: int) -> int:
        raw = (self.database.get_setting(key) or "").strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _get_auto_publish_score(self) -> int:
        """读取后台自动发布阈值，非法值回退到默认热文线。"""
        return self._get_int_setting("push_auto_score", HOT_THRESHOLD)

    def _is_enabled(self) -> bool:
        raw = (self.database.get_setting("push_enabled") or "1").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def _is_wait_for_high_score_enabled(self) -> bool:
        """If True, normal windows wait for >=85 articles before falling back to >=75."""
        raw = (self.database.get_setting("push_wait_for_high_score") or "0").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def _get_auto_sources(self) -> set[str]:
        raw = (self.database.get_setting("push_auto_sources") or "techflow,blockbeats").strip()
        if raw.startswith("["):
            try:
                return {str(item).strip() for item in json.loads(raw) if str(item).strip()}
            except json.JSONDecodeError:
                pass
        return {item.strip() for item in raw.split(",") if item.strip()}

    def _is_broadcast_enabled(self) -> bool:
        raw = (self.database.get_setting("broadcast_enabled") or "0").strip().lower()
        return raw in {"1", "true", "on", "yes"}

    @staticmethod
    def _is_morning_window(now: datetime) -> bool:
        return MORNING_START <= now.hour < MORNING_END

    @staticmethod
    def _is_active_time(now: datetime) -> bool:
        return ACTIVE_START <= now.hour < ACTIVE_END

    @staticmethod
    def _next_active_start(now: datetime) -> datetime:
        if now.hour < ACTIVE_START:
            return now.replace(hour=ACTIVE_START, minute=0, second=0, microsecond=0)
        next_day = now + timedelta(days=1)
        return next_day.replace(hour=ACTIVE_START, minute=0, second=0, microsecond=0)
