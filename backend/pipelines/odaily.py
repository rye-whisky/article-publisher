# -*- coding: utf-8 -*-
"""Odaily (星球日报) scraper — uses web-api.odaily.news API."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")

ARTICLE_SOURCE_NAME = "Odaily星球日报"
API_BASE = "https://web-api.odaily.news"


class OdailyScraper(BaseScraper):
    """Scraper for Odaily (星球日报) articles.

    Uses the public API at web-api.odaily.news:
    - List:  GET /post/page?page=1&size=20
    - Detail: GET /post/detail/{id}
    """

    source_key = "odaily"

    def _article_id_from_path(self, path: Path) -> str | None:
        """Extract article_id from Odaily JSON file path."""
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("odaily_"):
            article_id = stem.replace("odaily_", "")
            return f"odaily:{article_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Fetch article list from API.

        Only fetches page 1 (latest 20 articles). Older articles
        should already be in the database from previous runs.
        """
        items = []
        r = self.session.get(
            f"{API_BASE}/post/page",
            params={"page": 1, "size": 20},
            headers={
                "Origin": "https://www.odaily.news",
                "Referer": "https://www.odaily.news/",
                "x-locale": "zh-CN",
            },
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data") or {}
        article_list = data.get("list") or []

        for art in article_list:
            aid = str(art.get("id", ""))
            if not aid:
                continue
            items.append({
                "article_id": aid,
                "original_url": f"https://www.odaily.news/zh-CN/post/{aid}",
                "source": ARTICLE_SOURCE_NAME,
            })

        log.info("Odaily found %d articles (page 1 only)", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article detail from API."""
        aid = item.get("article_id", "")

        # Try API first
        try:
            detail = self._fetch_detail_api(aid)
            if detail:
                return {**item, **detail}
        except Exception as e:
            log.warning("[Odaily] API fetch failed for %s: %s, trying HTML fallback", aid, e)

        # Fallback to HTML scraping
        return self._fetch_detail_html(item)

    def _fetch_detail_api(self, article_id: str) -> dict | None:
        """Fetch article detail via API. Returns dict with blocks, title, etc."""
        r = self.session.get(
            f"{API_BASE}/post/detail/{article_id}",
            headers={
                "Origin": "https://www.odaily.news",
                "Referer": "https://www.odaily.news/",
                "x-locale": "zh-CN",
            },
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data")
        if not data:
            return None

        # Extract fields
        title = data.get("title", "")
        cover_src = data.get("cover", "")
        summary = data.get("summary", "") or data.get("aiSummary", "")
        author_info = data.get("author", {})
        author = author_info.get("nickname", "") if isinstance(author_info, dict) else str(author_info)
        ts = data.get("publishTimestamp")
        publish_time = ""
        if ts:
            try:
                publish_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
            except Exception:
                publish_time = str(ts)

        # Parse content HTML into blocks
        content_html = data.get("content", "")
        blocks = self._html_to_blocks(content_html) if content_html else []

        if not blocks:
            raise RuntimeError("API returned empty content")

        log.info("[Odaily] API fetch OK: %s (%d blocks)", title[:40], len(blocks))
        return {
            "source_key": "odaily",
            "article_id_full": f"odaily:{article_id}",
            "raw_id": article_id,
            "title": title,
            "source": ARTICLE_SOURCE_NAME,
            "author": author,
            "publish_time": publish_time,
            "cover_src": cover_src,
            "abstract": summary,
            "blocks": blocks,
        }

    def _fetch_detail_html(self, item: dict) -> dict:
        """Fallback: scrape article from HTML."""
        url = item["original_url"]
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        title_el = soup.find("meta", property="og:title")
        title = title_el.get("content", "") if title_el else ""
        og = soup.find("meta", property="og:image")
        cover_src = og["content"] if og else ""

        blocks = []
        ai_summary_divs = soup.find_all('div', class_=lambda x: x and any(
            'bg-custom-F2F2F2' in str(c) or 'dark:bg-custom-292929' in str(c) for c in x))

        for el in soup.find_all(['p', 'h2', 'h3', 'h4']):
            in_ai = any(el in d.descendants for d in ai_summary_divs)
            if in_ai:
                continue
            tag = el.name
            if tag == 'p':
                for img in el.find_all('img'):
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        if src.startswith("/"):
                            src = "https://www.odaily.news" + src
                        blocks.append({"type": "img", "src": src})
            text = el.get_text(strip=True)
            if text:
                block_type = tag if tag in ('h2', 'h3', 'h4') else 'p'
                blocks.append({"type": block_type, "text": text})

        if not blocks:
            desc_el = soup.find("meta", attrs={"name": "description"})
            desc = desc_el.get("content", "") if desc_el else ""
            if desc:
                blocks.append({"type": "p", "text": desc})

        if not blocks:
            raise RuntimeError("无法解析文章内容")

        return {
            **item,
            "source_key": "odaily",
            "article_id_full": item.get("article_id_full", f"odaily:{item.get('article_id', '')}"),
            "title": title,
            "source": ARTICLE_SOURCE_NAME,
            "author": "",
            "publish_time": "",
            "cover_src": cover_src,
            "blocks": blocks,
        }

    @staticmethod
    def _html_to_blocks(html: str) -> list[dict]:
        """Convert API content HTML into blocks list."""
        soup = BeautifulSoup(html, "html.parser")
        blocks = []
        for el in soup.find_all(True):
            if el.name == 'img':
                src = el.get("src") or el.get("data-src") or ""
                if src and not src.startswith("data:"):
                    if src.startswith("/"):
                        src = "https://www.odaily.news" + src
                    blocks.append({"type": "img", "src": src})
            elif el.name in ('p', 'h2', 'h3', 'h4', 'li'):
                # Skip nested elements (process only top-level)
                if el.parent and el.parent.name in ('p', 'h2', 'h3', 'h4', 'li', 'ul', 'ol'):
                    continue
                text = el.get_text(strip=True)
                if text:
                    tag = el.name if el.name in ('h2', 'h3', 'h4') else 'p'
                    blocks.append({"type": tag, "text": text})
            elif el.name == 'ul' or el.name == 'ol':
                for li in el.find_all('li', recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
        return blocks

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article.get("article_id", ""))
        path = self.output_dir / f"odaily_{raw_id}.json"
        content = json.dumps(
            {
                "article_id": raw_id,
                "title": article.get("title", ""),
                "source": article.get("source", ARTICLE_SOURCE_NAME),
                "author": article.get("author", ""),
                "publish_time": article.get("publish_time", ""),
                "original_url": article.get("original_url", ""),
                "cover_src": article.get("cover_src", ""),
                "blocks": article.get("blocks", []),
            },
            ensure_ascii=False,
            indent=2,
        )
        self._write_file_with_lock(path, content)
        return path

    # -- File parsing --

    def parse_article_file(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {
            "source_key": "odaily",
            "article_id": f"odaily:{data['article_id']}",
            "raw_id": str(data["article_id"]),
            "title": data.get("title", path.stem),
            "author": data.get("author", ""),
            "source": data.get("source", ARTICLE_SOURCE_NAME),
            "publish_time": data.get("publish_time", ""),
            "original_url": data.get("original_url", ""),
            "cover_src": data.get("cover_src", ""),
            "blocks": data.get("blocks", []),
            "path": str(path),
        }

    # -- URL-based fetch --

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        if "odaily.news" not in url:
            raise ValueError(f"invalid Odaily article URL: {url}")
        m = re.search(r"/zh-CN/post/(\d+)", url)
        if not m:
            m = re.search(r"/post/(\d+)", url)
        article_id = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "article_id": article_id,
            "article_id_full": f"odaily:{article_id}",
            "raw_id": article_id,
            "title": "",
            "original_url": url,
            "source": ARTICLE_SOURCE_NAME,
        }
