#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for configurable auto-publish score threshold."""

import sys
from datetime import datetime
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from services.auto_publish_scheduler import AutoPublishScheduler
from services.pipeline_service import PipelineService


class FakeDatabase:
    def __init__(self, settings: dict[str, str] | None = None, candidates: list[dict] | None = None):
        self.settings = settings or {}
        self.candidates = candidates or []
        self.captured_min_score = None

    def get_setting(self, key: str):
        return self.settings.get(key)

    def count_pushes_in_window(self, *_args, **_kwargs):
        return 0

    def get_auto_publish_broadcast_candidates(self, *, min_score, **_kwargs):
        self.captured_min_score = min_score
        return self.candidates

    def list_push_history(self, *_args, **_kwargs):
        return []

    def find_recent_by_keyword_overlap(self, *_args, **_kwargs):
        return []

    def cleanup_stale_candidates(self, *_args, **_kwargs):
        return 0

    def mark_broadcasted(self, *_args, **_kwargs):
        return True

    def record_broadcast_history(self, *_args, **_kwargs):
        return True

    def record_push_history(self, *_args, **_kwargs):
        return True


class FakePipelineService:
    def __init__(self, database):
        self.database = database
        self.filter_service = None
        self.broadcast_calls = []

    get_push_label = staticmethod(PipelineService.get_push_label)

    def auto_publish_and_broadcast(self, article: dict, **kwargs):
        self.broadcast_calls.append({"article": article, **kwargs})
        return {"cms_id": f"cms-{article['article_id']}"}


def _scheduler(settings: dict[str, str] | None = None, candidates: list[dict] | None = None):
    db = FakeDatabase(settings, candidates)
    pipeline_service = FakePipelineService(db)
    scheduler = AutoPublishScheduler(pipeline_service)
    return scheduler, db, pipeline_service


def _candidate(article_id: str, score: int) -> dict:
    return {
        "article_id": article_id,
        "source_key": "techflow",
        "title": f"Test {article_id}",
        "score": score,
    }


def test_auto_publish_scheduler_uses_push_auto_score_for_candidates():
    scheduler, db, _pipeline_service = _scheduler({"push_auto_score": "90"})
    scheduler.get_window_context = lambda: {
        "active": True,
        "window_start": datetime(2026, 6, 11, 8, 0, 0),
        "window_end": datetime(2026, 6, 11, 10, 0, 0),
        "auto_sources": ["techflow", "blockbeats"],
        "is_morning": True,
    }

    result = scheduler.run_once()

    assert result["reason"] == "no_candidates"
    assert db.captured_min_score == 90


def test_auto_publish_scheduler_status_reports_push_auto_score():
    scheduler, _db, _pipeline_service = _scheduler({"push_auto_score": "88"})

    status = scheduler.get_status()
    context = scheduler.get_window_context(datetime(2026, 6, 11, 9, 0, 0))

    assert status["auto_score"] == 88
    assert status["hot_score"] == 88
    assert context["min_score"] == 88


def test_auto_publish_scheduler_invalid_push_auto_score_falls_back_to_75():
    scheduler, db, _pipeline_service = _scheduler({"push_auto_score": "invalid"})
    scheduler.get_window_context = lambda: {
        "active": True,
        "window_start": datetime(2026, 6, 11, 8, 0, 0),
        "window_end": datetime(2026, 6, 11, 10, 0, 0),
        "auto_sources": ["techflow"],
        "is_morning": True,
    }

    scheduler.run_once()

    assert db.captured_min_score == 75


def test_auto_publish_scheduler_pushes_hot_label_for_scores_from_60_to_84():
    scheduler, _db, pipeline_service = _scheduler(
        {
            "push_auto_score": "60",
            "broadcast_enabled": "1",
            "push_in_site_check_enabled": "0",
        },
        [_candidate("techflow:70", 70)],
    )
    scheduler.get_window_context = lambda: {
        "active": True,
        "window_start": datetime(2026, 6, 11, 8, 0, 0),
        "window_end": datetime(2026, 6, 11, 10, 0, 0),
        "auto_sources": ["techflow"],
        "is_morning": True,
    }

    result = scheduler.run_once()

    assert result["reason"] == "published_and_broadcasted"
    assert result["push_label"] == "热文"
    assert pipeline_service.broadcast_calls[0]["push_label"] == "热文"


def test_auto_publish_scheduler_pushes_explosive_label_for_scores_at_least_85():
    scheduler, _db, pipeline_service = _scheduler(
        {
            "push_auto_score": "60",
            "broadcast_enabled": "1",
            "push_in_site_check_enabled": "0",
        },
        [_candidate("techflow:85", 85)],
    )
    scheduler.get_window_context = lambda: {
        "active": True,
        "window_start": datetime(2026, 6, 11, 8, 0, 0),
        "window_end": datetime(2026, 6, 11, 10, 0, 0),
        "auto_sources": ["techflow"],
        "is_morning": True,
    }

    result = scheduler.run_once()

    assert result["reason"] == "published_and_broadcasted"
    assert result["push_label"] == "爆文"
    assert pipeline_service.broadcast_calls[0]["push_label"] == "爆文"
