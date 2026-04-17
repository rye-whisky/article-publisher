# -*- coding: utf-8 -*-
"""Independent auto-broadcast scheduler for published articles -> App desktop push."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

log = logging.getLogger("pipeline")


class BroadcastScheduler:
    """Select and push published articles to App desktop notification."""

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
        self._thread = threading.Thread(target=self._loop, daemon=True, name="broadcast-scheduler")
        self._thread.start()
        log.info("BroadcastScheduler started")

    def stop(self):
        """Stop background loop."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def run_once(self) -> dict:
        """Run a single broadcast cycle."""
        if not self.database:
            return {"ok": False, "reason": "database_not_configured"}

        if not self._is_enabled():
            return {"ok": True, "reason": "disabled"}

        grace_minutes = self._get_int_setting("broadcast_grace_minutes", 15)

        candidates = self.database.get_auto_broadcast_candidates(
            grace_minutes=grace_minutes,
            limit=1,
        )
        if not candidates:
            return {"ok": True, "reason": "no_candidates"}

        chosen = candidates[0]

        if self.database.has_broadcast_history(chosen["article_id"]):
            log.info("BroadcastScheduler skip %s: already broadcasted", chosen["article_id"])
            return {"ok": True, "reason": "already_broadcasted", "article_id": chosen["article_id"]}

        try:
            result = self.pipeline_service.broadcast_article(chosen, strategy="auto")
            log.info(
                "BroadcastScheduler auto-broadcast %s (cms_id=%s)",
                chosen["article_id"],
                chosen.get("cms_id"),
            )
            return {
                "ok": True,
                "reason": "broadcasted",
                "article_id": chosen["article_id"],
                "cms_id": chosen.get("cms_id"),
            }
        except Exception as exc:
            log.error("BroadcastScheduler failed for %s: %s", chosen.get("article_id", ""), exc)
            return {
                "ok": False,
                "reason": "broadcast_failed",
                "article_id": chosen.get("article_id", ""),
                "error": str(exc),
            }

    def get_status(self) -> dict:
        """Return current broadcast scheduler config and recent history."""
        enabled = self._is_enabled()
        grace = self._get_int_setting("broadcast_grace_minutes", 15)
        interval = self._get_int_setting("broadcast_check_interval_minutes", 15)
        history = []
        if self.database:
            history = self.database.list_broadcast_history(limit=8)
        return {
            "enabled": enabled,
            "grace_minutes": grace,
            "check_interval_minutes": interval,
            "history": history,
        }

    def _loop(self):
        while not self._stop_event.is_set():
            interval_minutes = self._get_int_setting("broadcast_check_interval_minutes", 15)
            self._stop_event.wait(max(1, interval_minutes) * 60)
            if self._stop_event.is_set():
                break
            try:
                self.run_once()
            except Exception as exc:
                log.error("BroadcastScheduler loop failed: %s", exc)

    def _get_int_setting(self, key: str, default: int) -> int:
        raw = (self.database.get_setting(key) or "").strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _is_enabled(self) -> bool:
        raw = (self.database.get_setting("broadcast_enabled") or "0").strip().lower()
        return raw in {"1", "true", "on", "yes"}
