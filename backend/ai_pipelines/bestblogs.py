# -*- coding: utf-8 -*-
"""BestBlogs.dev scraper for AI-curated articles — uses OpenAPI for full content."""

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "BestBlogs"
RSS_URL = "https://www.bestblogs.dev/zh/feeds/rss?minScore=80&timeFilter=1w"
API_BASE = "https://api.bestblogs.dev/openapi/v1"


class BestBlogsScraper(BaseScraper):
    """Scraper for BestBlogs.dev articles.

    Uses OpenAPI for full article content when api_key is configured,
    falls back to RSS feed (summary only) otherwise.
    """

    source_key = "bestblogs"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.api_key = cfg.get("api_key", "")
        self.rss_url = cfg.get("rss_url", RSS_URL)
        self.min_score = cfg.get("min_score", 70)
        self.use_api = bool(self.api_key)

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
            raw_id = stem.replace("bestblogs_", "")
            return f"bestblogs:{raw_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Fetch article list. Uses OpenAPI if api_key is set, else RSS."""
        if self.use_api:
            return self._parse_list_api()
        return self._parse_list_rss()

    def _parse_list_api(self) -> list[dict]:
        """Fetch curated article list from BestBlogs OpenAPI."""
        items = []
        body = {
            "currentPage": 1,
            "pageSize": 20,
            "timeFilter": "1w",
            "type": "ARTICLE",
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

        for art in article_list:
            raw_id = art.get("id", "")
            if not raw_id:
                continue

            score = art.get("score", 0) or 0
            if score < self.min_score:
                continue

            short_id = raw_id.replace("RAW_", "") if raw_id.startswith("RAW_") else raw_id

            authors = art.get("authors", [])
            source_name = art.get("sourceName", "")
            author = authors[0] if authors else source_name

            items.append({
                "article_id": f"bestblogs:{short_id}",
                "raw_id": short_id,
                "raw_api_id": raw_id,
                "title": art.get("title", "") or art.get("originalTitle", ""),
                "source": f"{SOURCE_NAME} · {source_name}" if source_name else SOURCE_NAME,
                "author": author,
                "publish_time": art.get("publishDateTimeStr", ""),
                "original_url": art.get("url", ""),
                "cover_src": art.get("cover", ""),
                "score": score,
                "tags": art.get("tags", []),
                "category": art.get("mainDomainDesc", "") or art.get("categoryDesc", ""),
                "one_sentence_summary": art.get("oneSentenceSummary", ""),
                "blocks": [],
            })

        log.info("[BestBlogs] API found %d articles (score >= %d)", len(items), self.min_score)
        return items

    def _parse_list_rss(self) -> list[dict]:
        """Fallback: fetch and parse RSS feed."""
        r = self.session.get(self.rss_url, timeout=30)
        r.raise_for_status()

        root = ET.fromstring(r.text)
        channel = root.find("channel")
        items_el = channel.findall("item") if channel is not None else []

        results = []
        for item in items_el:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            guid = item.findtext("guid", "").strip()
            author = item.findtext("author", "").strip()
            category = item.findtext("category", "").strip()
            keywords = item.findtext("keywords", "").strip()
            score_text = item.findtext("score", "0").strip()
            pub_date = item.findtext("pubDate", "").strip()
            description_html = item.findtext("description", "")

            if "/status/" in link:
                continue

            enclosure = item.find("enclosure")
            cover_src = enclosure.get("url", "") if enclosure is not None else ""

            raw_id = guid.replace("RAW_", "") if guid.startswith("RAW_") else guid

            try:
                score = int(score_text)
            except ValueError:
                score = 0

            tags = [kw.strip() for kw in keywords.split(",") if kw.strip()] if keywords else []
            desc_data = self._parse_description_html(description_html) if description_html else {}

            results.append({
                "article_id": f"bestblogs:{raw_id}",
                "raw_id": raw_id,
                "title": title,
                "source": SOURCE_NAME,
                "author": author,
                "publish_time": self._parse_pubdate(pub_date),
                "original_url": link,
                "cover_src": cover_src,
                "score": score,
                "tags": tags,
                "category": category,
                "one_sentence_summary": desc_data.get("one_sentence_summary", ""),
                "blocks": desc_data.get("blocks", []),
            })

        log.info("[BestBlogs] RSS returned %d articles", len(results))
        return results

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch full content via OpenAPI, or return item as-is (RSS already has blocks)."""
        if not self.use_api:
            return item

        raw_api_id = item.get("raw_api_id", f"RAW_{item.get('raw_id', '')}")

        try:
            html = self._fetch_content_api(raw_api_id)
            if html:
                blocks = self._html_to_blocks(html)
                if blocks:
                    item["blocks"] = blocks
                    log.info("[BestBlogs] Full content OK: %s (%d blocks)",
                             item.get("title", "")[:40], len(blocks))
                    return item
        except Exception as e:
            log.warning("[BestBlogs] Content API failed for %s: %s", raw_api_id, e)

        # Fallback to summary blocks (from RSS or list metadata)
        if not item.get("blocks") and item.get("one_sentence_summary"):
            item["blocks"] = [
                {"type": "p", "text": item["one_sentence_summary"]},
            ]
        return item

    def _fetch_content_api(self, raw_api_id: str) -> str | None:
        """Fetch full article HTML content from OpenAPI."""
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
        return data.get("displayDocument", "") or None

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"bestblogs_{raw_id}.json"
        self._write_file_with_lock(
            path,
            json.dumps(
                {
                    "article_id": raw_id,
                    "title": article.get("title", ""),
                    "source": article.get("source", SOURCE_NAME),
                    "author": article.get("author", ""),
                    "publish_time": article.get("publish_time", ""),
                    "original_url": article.get("original_url", ""),
                    "cover_src": article.get("cover_src", ""),
                    "score": article.get("score"),
                    "tags": article.get("tags", []),
                    "category": article.get("category", ""),
                    "one_sentence_summary": article.get("one_sentence_summary", ""),
                    "blocks": article.get("blocks", []),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
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
            "source": data.get("source", SOURCE_NAME),
            "publish_time": data.get("publish_time", ""),
            "original_url": data.get("original_url", ""),
            "cover_src": data.get("cover_src", ""),
            "score": data.get("score"),
            "tags": data.get("tags", []),
            "category": data.get("category", ""),
            "one_sentence_summary": data.get("one_sentence_summary", ""),
            "blocks": data.get("blocks", []),
            "path": str(path),
        }

    # -- Helpers --

    @staticmethod
    def _parse_pubdate(pub_date: str) -> str:
        try:
            dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return pub_date

    @staticmethod
    def _parse_description_html(html: str) -> dict:
        """Extract one_sentence_summary and content blocks from RSS <description>."""
        soup = BeautifulSoup(html, "html.parser")
        result = {"one_sentence_summary": "", "blocks": []}

        for h3 in soup.find_all("h3"):
            title = h3.get_text(strip=True)

            if "一句话摘要" in title:
                p = h3.find_next_sibling("p")
                if p:
                    result["one_sentence_summary"] = p.get_text(strip=True)

            elif "详细摘要" in title:
                p = h3.find_next_sibling("p")
                if p:
                    result["blocks"].append({"type": "h2", "text": "详细摘要"})
                    result["blocks"].append({"type": "p", "text": p.get_text(strip=True)})

            elif "主要观点" in title:
                result["blocks"].append({"type": "h2", "text": "主要观点"})
                ol = h3.find_next_sibling("ol")
                if ol:
                    for li in ol.find_all("li"):
                        strong = li.find("strong")
                        span = li.find("span")
                        if strong:
                            result["blocks"].append({"type": "p", "text": strong.get_text(strip=True)})
                        if span:
                            result["blocks"].append({"type": "p", "text": span.get_text(strip=True)})

            elif "文章金句" in title:
                result["blocks"].append({"type": "h2", "text": "文章金句"})
                ul = h3.find_next_sibling("ul")
                if ul:
                    for li in ul.find_all("li"):
                        text = li.get_text(strip=True)
                        if text:
                            result["blocks"].append({"type": "p", "text": text})

        return result

    @staticmethod
    def _html_to_blocks(html: str) -> list[dict]:
        """Convert API content HTML into blocks list."""
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        blocks = []
        seen_texts = set()

        main = (
            soup.find("article")
            or soup.find("div", class_=re.compile(r"article|content|post", re.I))
            or soup.find("main")
            or soup
        )

        for el in main.find_all(["p", "h1", "h2", "h3", "h4", "img", "figure", "blockquote", "pre"]):
            if el.parent and el.parent.name in ("p", "blockquote", "pre"):
                continue

            if el.name == "img":
                src = el.get("src") or el.get("data-src") or ""
                if src and not src.startswith("data:"):
                    blocks.append({"type": "img", "src": src})

            elif el.name == "figure":
                img = el.find("img")
                if img:
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        blocks.append({"type": "img", "src": src})
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
                for img in el.find_all("img"):
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        blocks.append({"type": "img", "src": src})

                text = el.get_text(strip=True)
                if not text or text in seen_texts or len(text) < 3:
                    continue
                seen_texts.add(text)

                tag = el.name if el.name in ("h2", "h3", "h4") else "p"
                if tag == "h1":
                    tag = "h2"
                blocks.append({"type": tag, "text": text})

        return blocks
