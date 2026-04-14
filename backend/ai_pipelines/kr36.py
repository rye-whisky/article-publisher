# -*- coding: utf-8 -*-
"""36氪 AI 资讯 scraper — uses embedded initialState JSON."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "36氪"
LIST_URL = "https://www.36kr.com/information/AI/"
DETAIL_URL = "https://www.36kr.com/p/{item_id}"


class Kr36Scraper(BaseScraper):
    """Scraper for 36氪 AI 资讯频道."""

    source_key = "kr36"

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("kr36_"):
            raw_id = stem.replace("kr36_", "")
            return f"kr36:{raw_id}"
        return None

    # -- Helpers --

    @staticmethod
    def _extract_initial_state(html: str) -> dict | None:
        """Parse window.initialState={...} from HTML using JSONDecoder."""
        m = re.search(r"window\.initialState\s*=\s*", html)
        if not m:
            return None
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(html, m.end())
            return obj
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _html_to_blocks(html: str) -> list[dict]:
        """Convert article HTML into blocks list."""
        soup = BeautifulSoup(html, "html.parser")
        blocks = []
        for el in soup.find_all(True):
            if el.name == "img":
                src = el.get("src") or el.get("data-src") or ""
                if src and not src.startswith("data:"):
                    if src.startswith("/"):
                        src = "https://www.36kr.com" + src
                    blocks.append({"type": "img", "src": src})
            elif el.name in ("p", "h2", "h3", "h4", "li"):
                if el.parent and el.parent.name in ("p", "h2", "h3", "h4", "li", "ul", "ol"):
                    continue
                text = el.get_text(strip=True)
                if text:
                    tag = el.name if el.name in ("h2", "h3", "h4") else "p"
                    blocks.append({"type": tag, "text": text})
            elif el.name in ("ul", "ol"):
                for li in el.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
        return blocks

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Fetch 36kr AI channel list page and extract article items."""
        r = self.session.get(LIST_URL, timeout=30)
        r.raise_for_status()
        # Keep server-declared charset (36kr sends utf-8). Only fallback when missing.
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"

        state = self._extract_initial_state(r.text)
        if not state:
            log.error("[kr36] Failed to extract initialState from list page")
            return []

        item_list = (
            state.get("information", {})
            .get("informationList", {})
            .get("itemList", [])
        )

        results = []
        for item in item_list:
            mat = item.get("templateMaterial", {})
            item_id = str(mat.get("itemId", ""))
            if not item_id:
                continue

            title = mat.get("widgetTitle", "").strip()
            summary = mat.get("summary", "").strip()
            author = mat.get("authorName", "").strip()
            cover = mat.get("widgetImage", "")
            ts = mat.get("publishTime")

            publish_time = ""
            if ts:
                try:
                    publish_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            results.append({
                "article_id": f"kr36:{item_id}",
                "raw_id": item_id,
                "title": title,
                "source": SOURCE_NAME,
                "author": author,
                "publish_time": publish_time,
                "original_url": DETAIL_URL.format(item_id=item_id),
                "cover_src": cover,
                "abstract": summary,
                "blocks": [],  # filled by fetch_detail
            })

        log.info("[kr36] List page returned %d articles", len(results))
        return results

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article detail page and extract full content."""
        raw_id = item.get("raw_id", "")
        url = DETAIL_URL.format(item_id=raw_id)
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        # Keep server-declared charset (36kr sends utf-8). Only fallback when missing.
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"

        state = self._extract_initial_state(r.text)
        if not state:
            log.warning("[kr36] No initialState in detail page for %s", raw_id)
            return item

        detail = (
            state.get("articleDetail", {})
            .get("articleDetailData", {})
            .get("data", {})
        )

        if not detail:
            log.warning("[kr36] Empty detail data for %s", raw_id)
            return item

        content_html = detail.get("widgetContent", "")
        blocks = self._html_to_blocks(content_html) if content_html else []

        # Use detail fields to enrich item
        if detail.get("widgetTitle"):
            item["title"] = detail["widgetTitle"]
        if detail.get("author"):
            # Detail author is the canonical byline (e.g. "强调Next")
            item["author"] = str(detail["author"]).strip()
        if detail.get("summary"):
            item["abstract"] = detail["summary"]

        # Cover from detail imgSources if not already set
        if not item.get("cover_src"):
            img_sources = detail.get("imgSources") or []
            if img_sources:
                item["cover_src"] = img_sources[0]

        item["blocks"] = blocks
        log.info("[kr36] Detail OK: %s (%d blocks)", item["title"][:40], len(blocks))
        return item

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"kr36_{raw_id}.json"
        content = json.dumps(
            {
                "article_id": raw_id,
                "title": article.get("title", ""),
                "source": article.get("source", SOURCE_NAME),
                "author": article.get("author", ""),
                "publish_time": article.get("publish_time", ""),
                "original_url": article.get("original_url", ""),
                "cover_src": article.get("cover_src", ""),
                "abstract": article.get("abstract", ""),
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
            "source_key": "kr36",
            "article_id": f"kr36:{data['article_id']}",
            "raw_id": str(data["article_id"]),
            "title": data.get("title", path.stem),
            "author": data.get("author", ""),
            "source": data.get("source", SOURCE_NAME),
            "publish_time": data.get("publish_time", ""),
            "original_url": data.get("original_url", ""),
            "cover_src": data.get("cover_src", ""),
            "abstract": data.get("abstract", ""),
            "blocks": data.get("blocks", []),
            "path": str(path),
        }
