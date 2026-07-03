#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for CMS publish payload construction."""

import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from services.publisher import Publisher


class FakeCos:
    def upload_cover_from_url(self, _url):
        return "https://cos.example/cover.jpg"


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"code": 0, "data": {"id": "cms_1"}}


def test_publish_uses_user_id_provider_when_article_has_no_user_id(monkeypatch):
    captured = {}

    def fake_post(url, headers, data, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json.loads(data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("services.publisher.requests.post", fake_post)

    publisher = Publisher(
        api_url="https://api.example/ccs/v1/admin/content/publish",
        api_headers={"x-token": "token"},
        cos_uploader=FakeCos(),
        user_id_provider=lambda: "1",
    )
    result = publisher.publish({
        "title": "Test",
        "cover_src": "https://example.com/cover.jpg",
        "blocks": [{"type": "p", "text": "body"}],
    })

    assert result["cms_id"] == "cms_1"
    assert captured["payload"]["user_id"] == "1"
    assert captured["payload"]["as_user_id"] == "1"
    assert captured["payload"]["info"]["cover_image"] == "https://cos.example/cover.jpg"
    assert "link" not in captured["payload"]["info"]
    assert "original_url" not in captured["payload"]["info"]
    assert "source_url" not in captured["payload"]["info"]
    assert "original_url" not in captured["payload"]
    assert "source_url" not in captured["payload"]
    assert captured["payload"]["admin_detail"] == {}


def test_build_html_drops_original_link_blocks():
    publisher = Publisher(
        api_url="https://api.example/ccs/v1/admin/content/publish",
        api_headers={},
        cos_uploader=FakeCos(),
    )

    html = publisher.build_html({
        "title": "Test",
        "source": "BlockBeats",
        "source_key": "blockbeats",
        "original_url": "https://www.theblockbeats.info/news/123",
        "blocks": [
            {"type": "p", "text": "正文"},
            {"type": "p", "text": "原文链接", "href": "https://www.theblockbeats.info/news/123"},
            {"type": "p", "text": "参考资料", "href": "https://example.com/ref"},
            {
                "type": "p",
                "html": '<p><a href="https://www.theblockbeats.info/news/123">阅读全文</a></p>',
                "text": "阅读全文",
            },
        ],
    })

    assert "正文" in html
    assert "https://example.com/ref" in html
    assert "theblockbeats.info/news/123" not in html
    assert "阅读全文" not in html
