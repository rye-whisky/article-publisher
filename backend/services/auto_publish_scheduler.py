# -*- coding: utf-8 -*-
"""Unified auto-publish + broadcast scheduler.

Replaces the old separate PushScheduler and BroadcastScheduler.
Rules:
  - 8:00-10:00 window: any article ≥75 → publish + broadcast (hot/explosive)
  - Other windows: prefer ≥85 (explosive), fallback to highest ≥75 (hot)
  - Each window only publishes one article
  - Before publishing, semantic dedup against last 6 broadcasted titles
  - Push label: "热文" for 75-84, "爆文" for 85+
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

log = logging.getLogger("pipeline")

# Morning rush window: 8:00-10:00
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

        now = datetime.now()
        hour = now.hour
        is_morning = MORNING_START <= hour < MORNING_END

        # Check if already published in this window
        window_start = self._window_start(now)
        pushed_in_window = self.database.count_pushes_in_window(window_start, strategy="auto")
        if pushed_in_window > 0:
            return {"ok": True, "reason": "window_full", "window_start": window_start.isoformat()}

        # Select candidate based on time window
        if is_morning:
            # Morning window: any ≥75
            candidates = self.database.get_auto_publish_broadcast_candidates(
                min_score=75, limit=1,
            )
        else:
            # Other times: prefer ≥85
            candidates = self.database.get_auto_publish_broadcast_candidates(
                min_score=85, limit=1,
            )
            # Fallback to highest ≥75 if no 85+
            if not candidates:
                candidates = self.database.get_auto_publish_broadcast_candidates(
                    min_score=75, limit=1,
                )

        if not candidates:
            return {"ok": True, "reason": "no_candidates"}

        chosen = candidates[0]
        score = chosen.get("score") or 0

        # Semantic dedup against last 6 broadcasted titles
        recent_titles = self.database.get_recent_broadcasted_titles(limit=6)
        if recent_titles:
            from services.llm import semantic_dedup
            is_dup = semantic_dedup(chosen.get("title", ""), recent_titles, self.database)
            if is_dup:
                log.info(
                    "AutoPublishScheduler skip %s: semantic duplicate",
                    chosen["article_id"],
                )
                # Record as skipped to avoid re-checking
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

        # Determine push label
        push_label = "爆文" if score >= 85 else "热文"

        try:
            result = self.pipeline_service.auto_publish_and_broadcast(
                chosen, push_label=push_label,
            )
            log.info(
                "AutoPublishScheduler %s %s (score=%s, cms_id=%s, label=%s)",
                "morning-publish" if is_morning else "publish",
                chosen["article_id"],
                score,
                chosen.get("cms_id", ""),
                push_label,
            )
            return {
                "ok": True,
                "reason": "published_and_broadcasted",
                "article_id": chosen["article_id"],
                "cms_id": chosen.get("cms_id", ""),
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
        history = []
        broadcast_history = []
        if self.database:
            history = self.database.list_push_history(limit=8)
            broadcast_history = self.database.list_broadcast_history(limit=8)
        return {
            "enabled": enabled,
            "check_interval_minutes": interval,
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

    @staticmethod
    def _window_start(now: datetime) -> datetime:
        """2-hour aligned window start."""
        base_hour = (now.hour // 2) * 2
        return now.replace(hour=base_hour, minute=0, second=0, microsecond=0)

    def _get_int_setting(self, key: str, default: int) -> int:
        raw = (self.database.get_setting(key) or "").strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _is_enabled(self) -> bool:
        raw = (self.database.get_setting("push_enabled") or "1").strip().lower()
        return raw not in {"0", "false", "off", "no"}
