# -*- coding: utf-8 -*-
"""BestBlogs.dev scraper — uses OpenAPI for article discovery and full content."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")

ARTICLE_SOURCE_NAME = "BestBlogs"
API_BASE = "https://api.bestblogs.dev/openapi/v1"


class BestBlogsScraper(BaseScraper):
    """Scraper for BestBlogs.dev curated articles.

    Uses the BestBlogs OpenAPI:
    - List:  POST /resource/list  (with score/category/time filters)
    - Meta:  GET  /resource/meta?id=RAW_xxx
    - Content: GET /resource/content?id=RAW_xxx
    """

    source_key = "bestblogs"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.api_key = cfg.get("api_key", "")
        self.min_score = cfg.get("min_score", 85)
        self.resource_type = cfg.get("resource_type", "ARTICLE")
        self.time_filter = cfg.get("time_filter", "1w")
        self.page_size = cfg.get("page_size", 20)

    def _api_headers(self) -> dict:
        return {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("bestblogs_"):
            article_id = stem.replace("bestblogs_", "")
            return f"bestblogs:{article_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Fetch curated article list from BestBlogs API."""
        items = []
        body = {
            "currentPage": 1,
            "pageSize": self.page_size,
            "timeFilter": self.time_filter,
            "type": self.resource_type,
            "userLanguage": "zh_CN",
            "sortType": "default",
        }

        r = self.session.post(
            f"{API_BASE}/resource/list",
            json=body,
            headers=self._api_headers(),
            timeout=30,
        )
        r.raise_for_status()
        resp = r.json()

        if not resp.get("success"):
            log.error("[BestBlogs] API error: %s", resp.get("message", "unknown"))
            return items

        data = resp.get("data") or {}
        article_list = data.get("dataList") or []
        total = data.get("totalCount", 0)

        for art in article_list:
            raw_id = art.get("id", "")
            if not raw_id:
                continue

            score = art.get("score", 0) or 0
            if score < self.min_score:
                continue

            # Extract short ID from RAW_xxx format
            short_id = raw_id.replace("RAW_", "") if raw_id.startswith("RAW_") else raw_id

            # Original URL from API
            original_url = art.get("url", "")
            title = art.get("title", "") or art.get("originalTitle", "")
            cover = art.get("cover", "")
            source_name = art.get("sourceName", "")
            authors = art.get("authors", [])
            author = authors[0] if authors else source_name
            publish_time = art.get("publishDateTimeStr", "")
            summary = art.get("oneSentenceSummary", "")
            tags = art.get("tags", [])
            category = art.get("mainDomainDesc", "") or art.get("categoryDesc", "")

            items.append({
                "article_id": short_id,
                "raw_api_id": raw_id,
                "original_url": original_url,
                "title": title,
                "cover_src": cover,
                "source": f"{ARTICLE_SOURCE_NAME} · {source_name}",
                "author": author,
                "publish_time": publish_time,
                "abstract": summary,
                "tags": tags,
                "score": score,
                "category": category,
                "source_name": source_name,
            })

        log.info("[BestBlogs] Found %d articles (score >= %d, total=%d)",
                 len(items), self.min_score, total)
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article full content from BestBlogs API."""
        raw_api_id = item.get("raw_api_id", f"RAW_{item.get('article_id', '')}")

        # Try fetching full content via API
        try:
            content_html = self._fetch_content(raw_api_id)
            if content_html:
                blocks = self._html_to_blocks(content_html)
                if blocks:
                    log.info("[BestBlogs] Full content OK: %s (%d blocks)",
                             item.get("title", "")[:40], len(blocks))
                    return self._build_detail(item, blocks, content_source="full")
        except Exception as e:
            log.warning("[BestBlogs] Content API failed for %s: %s", raw_api_id, e)

        # Fallback: build blocks from summary/metadata in list item
        log.info("[BestBlogs] Using summary fallback for: %s", item.get("title", "")[:40])
        blocks = self._summary_to_blocks(item)
        return self._build_detail(item, blocks, content_source="summary")

    def _fetch_content(self, raw_api_id: str) -> str | None:
        """Fetch full article HTML content from API."""
        r = self.session.get(
            f"{API_BASE}/resource/content",
            params={"id": raw_api_id},
            headers=self._api_headers(),
            timeout=30,
        )
        r.raise_for_status()
        resp = r.json()

        if not resp.get("success"):
            return None

        data = resp.get("data") or {}
        html = data.get("displayDocument", "")
        return html if html else None

    def _build_detail(self, item: dict, blocks: list[dict], content_source: str) -> dict:
        """Build the detail dict from item + blocks."""
        short_id = item.get("article_id", "")
        return {
            **item,
            "source_key": "bestblogs",
            "article_id_full": f"bestblogs:{short_id}",
            "raw_id": short_id,
            "blocks": blocks,
            "content_source": content_source,
        }

    def _summary_to_blocks(self, item: dict) -> list[dict]:
        """Convert summary/metadata from list item into blocks (fallback)."""
        blocks = []

        # One-sentence summary
        if item.get("abstract"):
            blocks.append({"type": "h3", "text": "摘要"})
            blocks.append({"type": "p", "text": item["abstract"]})

        # Tags
        tags = item.get("tags", [])
        if tags:
            blocks.append({"type": "p", "text": f"标签：{', '.join(tags)}"})

        # Original link
        url = item.get("original_url", "")
        if url:
            blocks.append({"type": "p", "text": f"原文链接：{url}"})

        return blocks

    @staticmethod
    def _html_to_blocks(html: str) -> list[dict]:
        """Convert API content HTML into blocks list."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove script, style, nav, footer
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        blocks = []
        seen_texts = set()

        # Find main content container
        main = (
            soup.find("article")
            or soup.find("div", class_=re.compile(r"article|content|post", re.I))
            or soup.find("main")
            or soup
        )

        for el in main.find_all(["p", "h1", "h2", "h3", "h4", "img", "figure", "blockquote", "pre"]):
            # Skip nested elements inside already-processed parents
            if el.parent and el.parent.name in ("p", "blockquote", "pre"):
                continue

            if el.name == "img":
                src = el.get("src") or el.get("data-src") or ""
                if src and not src.startswith("data:"):
                    if src.startswith("/"):
                        src = f"https://www.bestblogs.dev{src}"
                    blocks.append({"type": "img", "src": src})

            elif el.name == "figure":
                img = el.find("img")
                if img:
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        blocks.append({"type": "img", "src": src})
                # Also get figcaption text
                cap = el.find("figcaption")
                if cap:
                    text = cap.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})

            elif el.name == "blockquote":
                text = el.get_text(strip=True)
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    blocks.append({"type": "p", "text": text})

            elif el.name == "pre":
                text = el.get_text(strip=True)
                if text and len(text) < 5000:
                    blocks.append({"type": "p", "text": text})

            else:
                # Handle images inside paragraphs
                for img in el.find_all("img"):
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        blocks.append({"type": "img", "src": src})

                text = el.get_text(strip=True)
                if not text:
                    continue

                # Deduplicate
                if text in seen_texts:
                    continue
                seen_texts.add(text)

                # Skip very short texts that are likely labels
                if len(text) < 3:
                    continue

                tag = el.name if el.name in ("h2", "h3", "h4") else "p"
                # Don't use h1 (title is already separate)
                if tag == "h1":
                    tag = "h2"
                blocks.append({"type": tag, "text": text})

        return blocks

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article.get("article_id", ""))
        path = self.output_dir / f"bestblogs_{raw_id}.json"
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
                "tags": article.get("tags", []),
                "score": article.get("score", 0),
                "category": article.get("category", ""),
                "content_source": article.get("content_source", ""),
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
            "source_key": "bestblogs",
            "article_id": f"bestblogs:{data['article_id']}",
            "raw_id": str(data["article_id"]),
            "title": data.get("title", path.stem),
            "author": data.get("author", ""),
            "source": data.get("source", ARTICLE_SOURCE_NAME),
            "publish_time": data.get("publish_time", ""),
            "original_url": data.get("original_url", ""),
            "cover_src": data.get("cover_src", ""),
            "blocks": data.get("blocks", []),
            "tags": data.get("tags", []),
            "score": data.get("score", 0),
            "category": data.get("category", ""),
            "path": str(path),
        }

    # -- URL-based fetch --

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        """Build item from a bestblogs.dev article URL or API resource ID."""
        # Handle direct API IDs like RAW_xxx
        if url.startswith("RAW_"):
            short_id = url.replace("RAW_", "")
            return {
                "article_id": short_id,
                "raw_api_id": url,
                "title": "",
                "original_url": "",
                "source": ARTICLE_SOURCE_NAME,
            }

        # Handle bestblogs.dev/article/xxx URLs
        m = re.search(r"/article/([a-f0-9]+)", url)
        if m:
            short_id = m.group(1)
            return {
                "article_id": short_id,
                "raw_api_id": f"RAW_{short_id}",
                "title": "",
                "original_url": url,
                "source": ARTICLE_SOURCE_NAME,
            }

        raise ValueError(f"Invalid BestBlogs article URL or ID: {url}")
