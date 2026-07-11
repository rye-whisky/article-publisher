#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for title-based before-publish routing."""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from services.before_publish import (
    AI_EVENT_TAG,
    AI_EVENT_USER_ID,
    RWA_TAG,
    PREDICTION_MARKET_USER_ID,
    REGULATION_USER_ID,
    RWA_USER_ID,
    SECURITY_USER_ID,
    apply_before_publish_rules,
)
from services.database import ArticleDatabase
from services.pipeline_service import PipelineService


def _article(article_id: str, title: str, tags: list[str] | None = None) -> dict:
    return {
        "article_id": article_id,
        "source_key": "techflow",
        "raw_id": article_id.split(":", 1)[-1],
        "title": title,
        "source": "test",
        "author": "tester",
        "blocks": [{"type": "p", "text": "test content"}],
        "tags": tags or [],
    }


def _service(db: ArticleDatabase, publisher) -> PipelineService:
    svc = PipelineService.__new__(PipelineService)
    svc.database = db
    svc.publisher = publisher
    svc.mark_chainthink_token_ok = lambda: None
    svc.mark_chainthink_token_error = lambda _msg: None
    return svc


class DummyPublisher:
    def __init__(self):
        self.last_article = None

    def save_draft(self, article: dict) -> dict:
        self.last_article = dict(article)
        return {"cms_id": f"cms-{article['article_id']}"}


def test_rwa_rule_adds_tag_and_route():
    routed, matched = apply_before_publish_rules(_article("techflow:1", "Ondo推进代币化美股"), "manual")
    assert matched == "rwa"
    assert routed["user_id"] == RWA_USER_ID
    assert routed["as_user_id"] == RWA_USER_ID
    assert RWA_TAG in routed["tags"]
    assert routed["strong_content_tags"] == {"人工": [RWA_TAG]}


def test_prediction_market_rule_routes_to_prediction_column():
    routed, matched = apply_before_publish_rules(_article("techflow:2", "Polymarket交易量创新高"), "manual")
    assert matched == "prediction_market"
    assert routed["user_id"] == PREDICTION_MARKET_USER_ID
    assert routed["as_user_id"] == PREDICTION_MARKET_USER_ID


def test_regulation_rule_routes_to_regulation_column():
    routed, matched = apply_before_publish_rules(_article("techflow:3", "SEC新法案推进监管"), "manual")
    assert matched == "regulation"
    assert routed["user_id"] == REGULATION_USER_ID
    assert routed["as_user_id"] == REGULATION_USER_ID


def test_security_rule_routes_to_security_column():
    routed, matched = apply_before_publish_rules(_article("techflow:4", "慢雾：黑客利用漏洞引发危机"), "manual")
    assert matched == "security"
    assert routed["user_id"] == SECURITY_USER_ID
    assert routed["as_user_id"] == SECURITY_USER_ID


def test_ai_event_rule_routes_and_adds_tag():
    routed, matched = apply_before_publish_rules(_article("techflow:ai", "OpenAI发布新模型"), "manual")
    assert matched == "ai_event"
    assert routed["user_id"] == AI_EVENT_USER_ID
    assert routed["as_user_id"] == AI_EVENT_USER_ID
    assert routed["tags"] == [AI_EVENT_TAG]
    assert routed["strong_content_tags"] == {"人工": [AI_EVENT_TAG]}


def test_rwa_rule_has_priority_over_ai_event():
    routed, matched = apply_before_publish_rules(_article("techflow:mix", "OpenAI联手Ondo推进代币化"), "manual")
    assert matched == "rwa"
    assert routed["user_id"] == RWA_USER_ID
    assert routed["as_user_id"] == RWA_USER_ID
    assert RWA_TAG in routed["tags"]
    assert AI_EVENT_TAG not in routed["tags"]


def test_daily_report_does_not_apply_before_publish_rules():
    routed, matched = apply_before_publish_rules(_article("techflow:5", "Ondo推进代币化"), "daily_report")
    assert matched is None
    assert "user_id" not in routed
    assert routed["tags"] == []


def test_save_article_draft_persists_rwa_tag_to_database(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    publisher = DummyPublisher()
    svc = _service(db, publisher)
    article = _article("techflow:6", "Binance拓展美股代币化布局", tags=["已有标签"])

    db.insert_or_update(article)
    result = svc.save_article_draft(dict(article), strategy="manual")
    stored = db.get_by_article_id(article["article_id"])

    assert result["cms_id"] == f"cms-{article['article_id']}"
    assert publisher.last_article["user_id"] == RWA_USER_ID
    assert publisher.last_article["as_user_id"] == RWA_USER_ID
    assert publisher.last_article["strong_content_tags"] == {"人工": [RWA_TAG]}
    assert stored["tags"] == ["已有标签", RWA_TAG]

    db.close()
