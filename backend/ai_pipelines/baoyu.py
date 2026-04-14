# -*- coding: utf-8 -*-
"""宝玉的分享 (baoyu.io) RSS + HTML scraper for AI-curated articles."""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "宝玉的分享"
RSS_URL = "https://baoyu.io/feed.xml"


class BaoyuScraper(BaseScraper):
    """Scraper for baoyu.io blog — RSS list + HTML detail."""

    source_key = "baoyu"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.rss_url = cfg.get("rss_url", RSS_URL)

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("baoyu_"):
            raw_id = stem.replace("baoyu_", "")
            return f"baoyu:{raw_id}"
        return None

    # -- List parsing (RSS) --

    def parse_list(self) -> list[dict]:
        """Fetch and parse RSS feed."""
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
            pub_date = item.findtext("pubDate", "").strip()
            description = item.findtext("description", "").strip()

            if not link:
                continue

            # Extract raw_id from URL path (last segment)
            raw_id = link.rstrip("/").rsplit("/", 1)[-1] if link else guid

            results.append({
                "article_id": f"baoyu:{raw_id}",
                "raw_id": raw_id,
                "title": title,
                "source": SOURCE_NAME,
                "author": author,
                "publish_time": self._parse_pubdate(pub_date),
                "original_url": link,
                "cover_src": "",
                "abstract": description,
                "blocks": [],  # filled by fetch_detail
            })

        log.info("[baoyu] RSS returned %d articles", len(results))
        return results

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article page and extract full content HTML."""
        url = item.get("original_url", "")
        if not url:
            return item

        try:
            html = self.fetch_html(url)
            blocks = self._html_to_blocks(html)

            if blocks:
                item["blocks"] = blocks
                log.info("[baoyu] Detail OK: %s (%d blocks)", item["title"][:40], len(blocks))
            else:
                log.warning("[baoyu] No content blocks for %s", url)

        except Exception as e:
            log.warning("[baoyu] Detail fetch failed for %s: %s", url, e)

        return item

    @staticmethod
    def _html_to_blocks(html: str) -> list[dict]:
        """Parse article HTML into blocks. Handles article content area."""
        soup = BeautifulSoup(html, "html.parser")

        # Try to find the article content area
        article = (
            soup.find("article")
            or soup.find("div", class_=lambda c: c and "prose" in str(c))
            or soup.find("main")
        )
        if not article:
            article = soup

        blocks = []
        for el in article.find_all(["p", "h2", "h3", "h4", "img", "ul", "ol"]):
            if el.parent and el.parent.name in ("blockquote", "figure"):
                # Include blockquote content as paragraphs
                if el.parent.name == "blockquote" and el.name == "p":
                    text = el.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
                continue

            if el.name == "img":
                src = el.get("src") or el.get("data-src") or ""
                if src and not src.startswith("data:"):
                    if src.startswith("/"):
                        src = "https://baoyu.io" + src
                    blocks.append({"type": "img", "src": src})
            elif el.name in ("ul", "ol"):
                for li in el.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
            elif el.name in ("p", "h2", "h3", "h4"):
                # Skip if nested inside another tracked element
                if el.parent and el.parent.name in ("p", "h2", "h3", "h4", "li", "ul", "ol"):
                    continue
                text = el.get_text(strip=True)
                if text:
                    tag = el.name if el.name in ("h2", "h3", "h4") else "p"
                    blocks.append({"type": tag, "text": text})

        return blocks

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"baoyu_{raw_id}.json"
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
            "source_key": "baoyu",
            "article_id": f"baoyu:{data['article_id']}",
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

    # -- Helpers --

    @staticmethod
    def _parse_pubdate(pub_date: str) -> str:
        """Parse RSS pubDate to YYYY-MM-DD HH:MM."""
        try:
            dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return pub_date
