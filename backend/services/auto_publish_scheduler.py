# -*- coding: utf-8 -*-
"""Unified auto-publish + broadcast scheduler.

Rules:
  - 8:00-10:00: any article with score >= 75 can publish + broadcast
  - Other windows: only score >= 85 can publish + broadcast
  - Each publish window can only consume one slot
  - Before publishing, semantic dedup against the last 6 auto-published+broadcasted titles
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

log = logging.getLogger("pipeline")

MORNING_START = 8
MORNING_END = 10


class AutoPublishScheduler:
    """Unified auto-publish + broadcast scheduler."""

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
        is_morning = context["is_morning"]
        window_start = context["window_start"]
        window_end = context["window_end"]
        auto_sources = context["auto_sources"]
        pushed_in_window = self.database.count_pushes_in_window(window_start, strategy="auto")
        if pushed_in_window > 0:
            return {"ok": True, "reason": "window_full", "window_start": window_start.isoformat()}

        candidates = self.database.get_auto_publish_broadcast_candidates(
            min_score=context["min_score"],
            limit=1,
            source_keys=auto_sources,
            window_start=window_start,
            window_end=window_end,
        )
        if not candidates:
            return {"ok": True, "reason": "no_candidates"}

        chosen = candidates[0]
        score = chosen.get("score") or 0

        recent_titles = self.database.get_recent_auto_publish_broadcast_titles(limit=6)
        if recent_titles:
            from services.llm import semantic_dedup

            is_dup = semantic_dedup(chosen.get("title", ""), recent_titles, self.database)
            if is_dup:
                log.info("AutoPublishScheduler skip %s: semantic duplicate", chosen["article_id"])
                self.database.record_push_history(
                    article_id=chosen["article_id"],
                    source_key=chosen.get("source_key", ""),
                    score=score,
                    cms_id="",
                    window_start=window_start,
                    strategy="auto",
                )
                return {
                    "ok": True,
                    "reason": "semantic_duplicate",
                    "article_id": chosen["article_id"],
                }

        push_label = self.pipeline_service.get_push_label(score) or "热文"

        try:
            result = self.pipeline_service.auto_publish_and_broadcast(
                chosen,
                push_label=push_label,
            )
            cms_id = result.get("cms_id", "")
            log.info(
                "AutoPublishScheduler %s %s (score=%s, cms_id=%s, label=%s)",
                "morning-publish" if is_morning else "publish",
                chosen["article_id"],
                score,
                cms_id,
                push_label,
            )
            return {
                "ok": True,
                "reason": "published_and_broadcasted",
                "article_id": chosen["article_id"],
                "cms_id": cms_id,
                "score": score,
                "push_label": push_label,
            }
        except Exception as exc:
            log.error("AutoPublishScheduler failed for %s: %s", chosen.get("article_id", ""), exc)
            return {
                "ok": False,
                "reason": "publish_failed",
                "article_id": chosen.get("article_id", ""),
                "error": str(exc),
            }

    def get_status(self) -> dict:
        """Return scheduler config and recent history."""
        enabled = self._is_enabled()
        interval = self._get_int_setting("push_check_interval_minutes", 10)
        window_hours = self._get_int_setting("push_window_hours", 2)
        history = []
        broadcast_history = []
        if self.database:
            history = self.database.list_push_history(limit=8)
            broadcast_history = self.database.list_broadcast_history(limit=8)
        return {
            "enabled": enabled,
            "window_hours": window_hours,
            "morning_window": {"start_hour": MORNING_START, "end_hour": MORNING_END},
            "hot_score": 75,
            "auto_score": self._get_int_setting("push_auto_score", 85),
            "review_score": self._get_int_setting("push_review_score", 70),
            "max_per_window": self._get_int_setting("push_max_per_window", 1),
            "check_interval_minutes": interval,
            "auto_sources": sorted(self._get_auto_sources()),
            "history": history,
            "broadcast_history": broadcast_history,
        }

    def _loop(self):
        while not self._stop_event.is_set():
            interval_minutes = self._get_int_setting("push_check_interval_minutes", 10)
            self._stop_event.wait(max(1, interval_minutes) * 60)
            if self._stop_event.is_set():
                break
            try:
                self.run_once()
            except Exception as exc:
                log.error("AutoPublishScheduler loop failed: %s", exc)

    def get_window_context(self, now: datetime | None = None) -> dict:
        """Return the currently active publish window context."""
        current = now or datetime.now()
        is_morning = self._is_morning_window(current)
        window_start = self._window_start(current)
        window_end = self._window_end(window_start)
        return {
            "now": current,
            "is_morning": is_morning,
            "window_start": window_start,
            "window_end": window_end,
            "min_score": 75 if is_morning else 85,
            "auto_sources": sorted(self._get_auto_sources()),
        }

    def _window_start(self, now: datetime) -> datetime:
        """Return the aligned window start for the current moment."""
        if self._is_morning_window(now):
            return now.replace(hour=MORNING_START, minute=0, second=0, microsecond=0)
        window_hours = max(1, self._get_int_setting("push_window_hours", 2))
        base_hour = (now.hour // window_hours) * window_hours
        return now.replace(hour=base_hour, minute=0, second=0, microsecond=0)

    def _window_end(self, window_start: datetime) -> datetime:
        """Return the exclusive end boundary for the active publish window."""
        if self._is_morning_window(window_start):
            return window_start.replace(hour=MORNING_END, minute=0, second=0, microsecond=0)
        window_hours = max(1, self._get_int_setting("push_window_hours", 2))
        return window_start + timedelta(hours=window_hours)

    @staticmethod
    def _is_morning_window(now: datetime) -> bool:
        return MORNING_START <= now.hour < MORNING_END

    def _get_int_setting(self, key: str, default: int) -> int:
        raw = (self.database.get_setting(key) or "").strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _is_enabled(self) -> bool:
        raw = (self.database.get_setting("push_enabled") or "1").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def _get_auto_sources(self) -> set[str]:
        raw = (self.database.get_setting("push_auto_sources") or "techflow,blockbeats").strip()
        if raw.startswith("["):
            try:
                return {str(item).strip() for item in json.loads(raw) if str(item).strip()}
            except json.JSONDecodeError:
                pass
        return {item.strip() for item in raw.split(",") if item.strip()}
