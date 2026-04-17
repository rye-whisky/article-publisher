# -*- coding: utf-8 -*-
"""Independent auto-publish scheduler for high-scoring blockchain articles."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

log = logging.getLogger("pipeline")


class PushScheduler:
    """Select and publish at most one high-scoring article per time window."""

    def __init__(self, pipeline_service):
        self.pipeline_service = pipeline_service
        self.database = pipeline_service.database
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """Start background loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="push-scheduler")
        self._thread.start()
        log.info("PushScheduler started")

    def stop(self):
        """Stop background loop."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def run_once(self) -> dict:
        """Run a single scheduling cycle."""
        if not self.database:
            return {"ok": False, "reason": "database_not_configured"}

        if not self._is_enabled():
            return {"ok": True, "reason": "disabled"}

        source_keys = sorted(self._get_auto_sources())
        if not source_keys:
            return {"ok": True, "reason": "no_auto_sources"}

        window_hours = self._get_int_setting("push_window_hours", 2)
        max_per_window = self._get_int_setting("push_max_per_window", 1)
        threshold = self._get_int_setting("push_auto_score", 85)

        now = datetime.now()
        window_start = self._window_start(now, window_hours)
        window_end = window_start + timedelta(hours=window_hours)

        pushed_count = self.database.count_pushes_in_window(window_start, strategy="auto")
        if pushed_count >= max_per_window:
            return {"ok": True, "reason": "window_full", "window_start": window_start.isoformat()}

        candidates = self.database.get_auto_publish_candidates(
            source_keys=source_keys,
            threshold=threshold,
            window_start=window_start,
            window_end=window_end,
            limit=5,
        )
        if not candidates:
            return {"ok": True, "reason": "no_candidates", "window_start": window_start.isoformat()}

        chosen = candidates[0]
        if self.database.has_push_history(chosen["article_id"], strategy="auto"):
            log.info("PushScheduler skip %s: already auto-published before", chosen["article_id"])
            return {
                "ok": True,
                "reason": "already_auto_published",
                "article_id": chosen["article_id"],
                "window_start": window_start.isoformat(),
            }
        try:
            result = self.pipeline_service.publish_article(chosen, strategy="auto")
            cms_id = result["cms_id"]
            self.database.record_push_history(
                article_id=chosen["article_id"],
                source_key=chosen.get("source_key", ""),
                score=chosen.get("score"),
                cms_id=cms_id,
                window_start=window_start,
                strategy="auto",
            )

            log.info(
                "PushScheduler auto-published %s (score=%s, cms_id=%s)",
                chosen["article_id"],
                chosen.get("score"),
                cms_id,
            )
            return {
                "ok": True,
                "reason": "published",
                "article_id": chosen["article_id"],
                "score": chosen.get("score"),
                "cms_id": cms_id,
                "window_start": window_start.isoformat(),
            }
        except Exception as exc:
            log.error("PushScheduler publish failed for %s: %s", chosen.get("article_id", ""), exc)
            return {
                "ok": False,
                "reason": "publish_failed",
                "article_id": chosen.get("article_id", ""),
                "error": str(exc),
            }

    def get_status(self) -> dict:
        """Return current push scheduler config and recent history."""
        return {
            "enabled": self._is_enabled(),
            "window_hours": self._get_int_setting("push_window_hours", 2),
            "auto_score": self._get_int_setting("push_auto_score", 85),
            "review_score": self._get_int_setting("push_review_score", 70),
            "max_per_window": self._get_int_setting("push_max_per_window", 1),
            "check_interval_minutes": self._get_int_setting("push_check_interval_minutes", 10),
            "auto_sources": sorted(self._get_auto_sources()),
            "history": self.database.list_push_history(limit=8, source_keys=list(self.pipeline_service.scrapers.keys())),
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
                log.error("PushScheduler loop failed: %s", exc)

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

    @staticmethod
    def _window_start(now: datetime, window_hours: int) -> datetime:
        base_hour = (now.hour // max(1, window_hours)) * max(1, window_hours)
        return now.replace(hour=base_hour, minute=0, second=0, microsecond=0)
