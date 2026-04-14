# -*- coding: utf-8 -*-
"""BestBlogs.dev RSS scraper for AI-curated articles."""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "BestBlogs"
RSS_URL = "https://www.bestblogs.dev/zh/feeds/rss?minScore=80&timeFilter=1w"


class BestBlogsScraper(BaseScraper):
    """Scraper for BestBlogs.dev RSS feed."""

    source_key = "bestblogs"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.rss_url = cfg.get("rss_url", RSS_URL)
        self.min_score = cfg.get("min_score", 70)

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("bestblogs_"):
            raw_id = stem.replace("bestblogs_", "")
            return f"bestblogs:{raw_id}"
        return None

    # -- List parsing (RSS) --

    def parse_list(self) -> list[dict]:
        """Fetch and parse RSS feed. Filter out /status/ links, keep only articles."""
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

            # Filter: skip /status/ links, only keep articles
            if "/status/" in link:
                continue

            # enclosure
            enclosure = item.find("enclosure")
            cover_src = enclosure.get("url", "") if enclosure is not None else ""

            # Extract raw_id from guid (e.g. RAW_2c79231c -> 2c79231c)
            raw_id = guid.replace("RAW_", "") if guid.startswith("RAW_") else guid

            try:
                score = int(score_text)
            except ValueError:
                score = 0

            tags = [kw.strip() for kw in keywords.split(",") if kw.strip()] if keywords else []

            # Parse description HTML for summary + content blocks
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
                "language": "zh",
                "one_sentence_summary": desc_data.get("one_sentence_summary", ""),
                "blocks": desc_data.get("blocks", []),
            })

        log.info("[BestBlogs] RSS returned %d articles (after filtering status posts)", len(results))
        return results

    def fetch_detail(self, item: dict) -> dict:
        """No-op: RSS description already contains curated content blocks."""
        return item

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"bestblogs_{raw_id}.json"
        path.write_text(
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
                    "language": article.get("language", "zh"),
                    "one_sentence_summary": article.get("one_sentence_summary", ""),
                    "blocks": article.get("blocks", []),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
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
            "language": data.get("language", "zh"),
            "one_sentence_summary": data.get("one_sentence_summary", ""),
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

    @staticmethod
    def _parse_description_html(html: str) -> dict:
        """Extract one_sentence_summary and content blocks from RSS <description>.

        Sections: 一句话摘要, 详细摘要, 主要观点, 文章金句.
        Skips 📊 文章信息 (metadata already in RSS fields).
        """
        soup = BeautifulSoup(html, "html.parser")
        result = {"one_sentence_summary": "", "blocks": []}

        for h3 in soup.find_all("h3"):
            title = h3.get_text(strip=True)

            # 一句话摘要 — store separately, don't add to blocks
            if "一句话摘要" in title:
                p = h3.find_next_sibling("p")
                if p:
                    result["one_sentence_summary"] = p.get_text(strip=True)

            # 详细摘要
            elif "详细摘要" in title:
                p = h3.find_next_sibling("p")
                if p:
                    result["blocks"].append({"type": "h2", "text": "详细摘要"})
                    result["blocks"].append({"type": "p", "text": p.get_text(strip=True)})

            # 主要观点
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

            # 文章金句
            elif "文章金句" in title:
                result["blocks"].append({"type": "h2", "text": "文章金句"})
                ul = h3.find_next_sibling("ul")
                if ul:
                    for li in ul.find_all("li"):
                        text = li.get_text(strip=True)
                        if text:
                            result["blocks"].append({"type": "p", "text": text})

        return result
