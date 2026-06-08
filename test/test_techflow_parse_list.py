#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for TechFlow list parsing."""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from pipelines.techflow import TechFlowScraper


class DummySession:
    def get(self, _url, timeout=30):
        raise AssertionError(f"unexpected network call with timeout={timeout}")


class FakeTechFlowScraper(TechFlowScraper):
    def __init__(self, html: str):
        super().__init__({"list_url": "https://www.techflowpost.com/"}, DummySession(), Path("/tmp"))
        self._html = html

    def fetch_html(self, url: str, timeout: int = 30) -> str:
        return self._html


def test_parse_list_supports_article_and_locale_urls():
    scraper = FakeTechFlowScraper(
        """
        <html>
          <body>
            <a href="/article/31943"><h3>文章一</h3></a>
            <a href="/zh-CN/article/31944"><h3>文章二</h3></a>
            <a href="/article/31943"><h3>文章一重复</h3></a>
          </body>
        </html>
        """
    )

    items = scraper.parse_list()

    assert len(items) == 2
    assert items[0]["article_id"] == "31943"
    assert items[0]["original_url"] == "https://www.techflowpost.com/article/31943"
    assert items[1]["article_id"] == "31944"
    assert items[1]["original_url"] == "https://www.techflowpost.com/zh-CN/article/31944"
