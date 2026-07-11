#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for automatic ChainThink selected-article spacing."""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from services.database import ArticleDatabase
from services.pipeline_service import PipelineService


def _article(article_id: str) -> dict:
    return {
        "article_id": article_id,
        "source_key": "techflow",
        "raw_id": article_id.split(":", 1)[-1],
        "title": f"Test {article_id}",
        "blocks": [{"type": "p", "text": "test content"}],
    }


def _service(db: ArticleDatabase):
    svc = PipelineService.__new__(PipelineService)
    svc.database = db
    return svc


def _publish(db: ArticleDatabase, article_id: str, is_good: bool):
    db.insert_or_update(_article(article_id))
    db.mark_published(article_id, f"cms-{article_id}", strategy="auto", is_good=is_good)


def _publish_with_strategy(
    db: ArticleDatabase,
    article_id: str,
    is_good: bool,
    *,
    strategy: str = "auto",
    stage: str = "published",
):
    db.insert_or_update(_article(article_id))
    db.mark_published(article_id, f"cms-{article_id}", strategy=strategy, is_good=is_good)
    if stage == "broadcasted":
        db.mark_broadcasted(article_id, strategy=strategy)


def test_auto_good_spacing_sequence(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    assert svc._should_auto_publish_as_good() is True

    _publish(db, "techflow:1", True)
    assert svc._should_auto_publish_as_good() is False

    _publish(db, "techflow:2", False)
    assert svc._should_auto_publish_as_good() is False

    _publish(db, "techflow:3", False)
    assert svc._should_auto_publish_as_good() is True

    _publish(db, "techflow:4", True)
    assert svc._should_auto_publish_as_good() is False

    db.close()


def test_auto_good_spacing_only_applies_to_auto_strategy(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    auto_prepared = svc._apply_auto_good_spacing(_article("techflow:auto"), "auto")
    manual_prepared = svc._apply_auto_good_spacing(_article("techflow:manual"), "manual")

    assert auto_prepared["is_good"] is True
    assert "is_good" not in manual_prepared

    db.close()


def test_auto_good_spacing_repeats_every_third_auto_publish(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    expected = [True, False, False, True, False, False, True]
    actual = []
    for idx, is_good in enumerate(expected, start=1):
        decision = svc._should_auto_publish_as_good()
        actual.append(decision)
        _publish(db, f"techflow:{idx}", is_good)

    assert actual == expected
    db.close()


def test_auto_good_spacing_ignores_manual_publishes_between_auto_publishes(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    _publish(db, "techflow:auto-1", True)
    _publish_with_strategy(db, "techflow:manual-1", False, strategy="manual")
    _publish_with_strategy(db, "techflow:manual-2", False, strategy="manual")

    assert svc._should_auto_publish_as_good() is False

    _publish(db, "techflow:auto-2", False)
    assert svc._should_auto_publish_as_good() is False

    _publish(db, "techflow:auto-3", False)
    assert svc._should_auto_publish_as_good() is True

    db.close()


def test_auto_good_spacing_counts_broadcasted_articles_as_published(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    _publish_with_strategy(db, "techflow:1", True, stage="broadcasted")
    assert svc._should_auto_publish_as_good() is False

    _publish_with_strategy(db, "techflow:2", False, stage="broadcasted")
    assert svc._should_auto_publish_as_good() is False

    _publish_with_strategy(db, "techflow:3", False, stage="broadcasted")
    assert svc._should_auto_publish_as_good() is True

    db.close()


def test_auto_good_spacing_uses_latest_good_article_as_anchor(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    _publish(db, "techflow:1", True)
    _publish(db, "techflow:2", False)
    _publish(db, "techflow:3", False)
    _publish(db, "techflow:4", True)
    _publish(db, "techflow:5", False)

    assert db.count_auto_published_after_last_good() == 1
    assert svc._should_auto_publish_as_good() is False

    _publish(db, "techflow:6", False)
    assert db.count_auto_published_after_last_good() == 2
    assert svc._should_auto_publish_as_good() is True

    db.close()


def test_auto_good_spacing_treats_auto_skip_history_as_irrelevant(tmp_path):
    db = ArticleDatabase(tmp_path / "articles.db")
    svc = _service(db)

    _publish(db, "techflow:1", True)
    _publish_with_strategy(db, "techflow:skip-1", False, strategy="auto_skip")
    _publish_with_strategy(db, "techflow:skip-2", False, strategy="auto_skip")

    assert db.count_auto_published_after_last_good() == 0
    assert svc._should_auto_publish_as_good() is False

    _publish(db, "techflow:2", False)
    _publish(db, "techflow:3", False)
    assert svc._should_auto_publish_as_good() is True

    db.close()
