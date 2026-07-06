#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for UK sync publish and draft modes."""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from services.pipeline_service import PipelineService


class FakeDatabase:
    def __init__(self, settings):
        self.settings = settings
        self.published = []
        self.drafts = []
        self.broadcasted = []
        self.cn_drafts = []
        self.errors = []
        self.rows = {}

    def get_setting(self, key):
        return self.settings.get(key)

    def set_settings_batch(self, values):
        self.settings.update(values)

    def mark_uk_published(self, article_id, uk_cms_id):
        self.published.append((article_id, uk_cms_id))
        return True

    def mark_uk_draft(self, article_id, uk_cms_id):
        self.drafts.append((article_id, uk_cms_id))
        return True

    def mark_uk_broadcasted(self, article_id):
        self.broadcasted.append(article_id)
        return True

    def mark_uk_sync_error(self, article_id, error):
        self.errors.append((article_id, error))
        return True

    def get_by_article_id(self, article_id):
        return self.rows.get(article_id)

    def update_tags(self, article_id, tags):
        return True

    def mark_cms_draft(self, article_id, cms_id, strategy="manual"):
        self.cn_drafts.append((article_id, cms_id, strategy))
        return True


class FakeUkPublisher:
    def __init__(self):
        self.published = []
        self.drafts = []
        self.pushes = []

    def publish(self, article):
        self.published.append(article)
        return {"cms_id": "uk_published_1", "cover_image": "published-cover.jpg"}

    def save_draft(self, article):
        self.drafts.append(article)
        return {"cms_id": "uk_draft_1", "cover_image": "draft-cover.jpg"}

    def push_to_app(self, **kwargs):
        self.pushes.append(kwargs)
        return {"pushed": True}


class FakeCnPublisher:
    def __init__(self):
        self.drafts = []

    def save_draft(self, article):
        self.drafts.append(article)
        return {"cms_id": "cn_draft_1", "publish_stage": "draft"}


def make_service(settings):
    service = PipelineService.__new__(PipelineService)
    service.publisher = FakeCnPublisher()
    service.uk_publisher = FakeUkPublisher()
    service.database = FakeDatabase(settings)
    service.cfg = {"chainthink_uk": {}}
    return service


def test_uk_publish_sync_disabled():
    service = make_service({"chainthink_uk_sync_enabled": "0"})
    result = service._sync_uk_publish({"article_id": "article-1", "title": "Title"})
    assert result is None
    assert not service.uk_publisher.published
    assert not service.uk_publisher.drafts


def test_uk_publish_mode_calls_publish():
    service = make_service({
        "chainthink_uk_sync_enabled": "1",
        "chainthink_uk_draft_enabled": "0",
    })
    result = service._sync_uk_publish({"article_id": "article-1", "title": "Title"})
    assert result["ok"]
    assert result["publish_stage"] == "published"
    assert service.uk_publisher.published
    assert not service.uk_publisher.drafts
    assert service.database.published == [("article-1", "uk_published_1")]


def test_uk_draft_mode_calls_save_draft():
    service = make_service({
        "chainthink_uk_sync_enabled": "1",
        "chainthink_uk_draft_enabled": "1",
    })
    result = service._sync_uk_publish({"article_id": "article-1", "title": "Title"})
    assert result["ok"]
    assert result["publish_stage"] == "draft"
    assert service.uk_publisher.drafts
    assert not service.uk_publisher.published
    assert service.database.drafts == [("article-1", "uk_draft_1")]


def test_cn_draft_sync_always_saves_uk_draft_when_sync_enabled():
    service = make_service({
        "chainthink_uk_sync_enabled": "1",
        "chainthink_uk_draft_enabled": "0",
    })
    result = service._sync_uk_draft({"article_id": "article-1", "title": "Title"})
    assert result["ok"]
    assert result["publish_stage"] == "draft"
    assert service.uk_publisher.drafts
    assert not service.uk_publisher.published
    assert service.database.drafts == [("article-1", "uk_draft_1")]


def test_cn_draft_does_not_sync_when_uk_sync_disabled():
    service = make_service({"chainthink_uk_sync_enabled": "0"})
    result = service._sync_uk_draft({"article_id": "article-1", "title": "Title"})
    assert result is None
    assert not service.uk_publisher.drafts
    assert not service.uk_publisher.published


def test_save_article_draft_syncs_uk_draft_when_sync_enabled():
    service = make_service({
        "chainthink_uk_sync_enabled": "1",
        "chainthink_uk_draft_enabled": "0",
    })
    result = service.save_article_draft({
        "article_id": "source:1",
        "source_key": "source",
        "raw_id": "1",
        "title": "Title",
        "blocks": [{"type": "p", "text": "Body"}],
        "tags": [],
    })
    assert result["cms_id"] == "cn_draft_1"
    assert result["uk_sync"]["ok"]
    assert result["uk_sync"]["publish_stage"] == "draft"
    assert service.uk_publisher.drafts
    assert not service.uk_publisher.published
    assert service.database.cn_drafts == [("source:1", "cn_draft_1", "manual")]
    assert service.database.drafts == [("source:1", "uk_draft_1")]


def test_uk_broadcast_skips_in_draft_mode():
    service = make_service({
        "chainthink_uk_sync_enabled": "1",
        "chainthink_uk_draft_enabled": "1",
    })
    result = service._sync_uk_broadcast({"article_id": "article-1", "uk_cms_id": "uk_draft_1"})
    assert result == {"ok": True, "skipped": True, "reason": "uk_draft_mode"}
    assert not service.uk_publisher.pushes
    assert not service.database.broadcasted


def test_uk_broadcast_pushes_in_publish_mode():
    service = make_service({
        "chainthink_uk_sync_enabled": "1",
        "chainthink_uk_draft_enabled": "0",
    })
    result = service._sync_uk_broadcast({"article_id": "article-1", "uk_cms_id": "uk_published_1"}, title="Title")
    assert result["ok"]
    assert service.uk_publisher.pushes == [{
        "cms_id": "uk_published_1",
        "title": "Title",
        "push_label": "",
        "push_content": "",
    }]
    assert service.database.broadcasted == ["article-1"]


if __name__ == "__main__":
    test_uk_publish_sync_disabled()
    test_uk_publish_mode_calls_publish()
    test_uk_draft_mode_calls_save_draft()
    test_cn_draft_sync_always_saves_uk_draft_when_sync_enabled()
    test_cn_draft_does_not_sync_when_uk_sync_disabled()
    test_save_article_draft_syncs_uk_draft_when_sync_enabled()
    test_uk_broadcast_skips_in_draft_mode()
    test_uk_broadcast_pushes_in_publish_mode()
    print("[OK] UK sync mode tests passed")
