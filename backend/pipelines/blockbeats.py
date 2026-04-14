# -*- coding: utf-8 -*-
"""BlockBeats (律动) scraper."""

import json
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")

ARTICLE_SOURCE_NAME = "律动 BlockBeats"

TAIL_CUT_TRIGGERS = [
    "点击了解律动BlockBeats 在招岗位",
    "欢迎加入律动 BlockBeats 官方社群",
    "Telegram 订阅群",
    "Telegram 交流群",
    "Twitter 官方账号",
]


class BlockBeatsScraper(BaseScraper):
    """Scraper for BlockBeats (律动) articles."""

    source_key = "blockbeats"

    def _article_id_from_path(self, path: Path) -> str | None:
        """Extract article_id from BlockBeats JSON file path."""
        if path.suffix != ".json":
            return None
        # Filename: blockbeats_{id}.json
        stem = path.stem
        if stem.startswith("blockbeats_"):
            article_id = stem.replace("blockbeats_", "")
            return f"blockbeats:{article_id}"
        return None

    # -- List parsing (from article_choice page __NUXT__ data) --

    def parse_list(self) -> list[dict]:
        """Fetch article_choice page and extract article IDs from __NUXT__ data."""
        list_url = self.cfg.get("list_url", "https://www.theblockbeats.info/article_choice")
        html = self.fetch_html(list_url)

        # Extract article_id values from __NUXT__ embedded JSON
        # The NUXT data uses a compressed format, but article_id always appears
        # as a literal number: article_id:61768
        ids = re.findall(r'article_id:(\d+)', html)
        seen = set()
        items = []
        for aid in ids:
            if aid in seen:
                continue
            seen.add(aid)
            items.append({
                "article_id": aid,
                "article_id_full": f"blockbeats:{aid}",
                "raw_id": aid,
                "title": "",
                "original_url": f"https://www.theblockbeats.info/news/{aid}",
                "source": ARTICLE_SOURCE_NAME,
            })
        log.info("BlockBeats article_choice: found %d articles", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        url = item["original_url"]
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # Title
        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # Cover (og:image)
        og = soup.find("meta", property="og:image")
        cover_src = og["content"] if og else ""

        # Content area
        content = soup.find(class_="news-content")
        if not content:
            raise RuntimeError("页面中未找到 .news-content 元素")

        # Parse blocks
        blocks = []
        for child in content.children:
            if not hasattr(child, "name") or not child.name:
                continue
            if child.name in ("p", "h2", "h3", "h4"):
                imgs = child.find_all("img")
                if imgs:
                    for img in imgs:
                        src = img.get("src") or img.get("data-src") or ""
                        if src:
                            blocks.append({"type": "img", "src": src})
                else:
                    text = child.get_text(strip=True)
                    if text:
                        blocks.append({"type": child.name, "text": text})
            elif child.name == "img":
                src = child.get("src") or child.get("data-src") or ""
                if src:
                    blocks.append({"type": "img", "src": src})

        # Truncate at tail hooks
        for i, b in enumerate(blocks):
            if b["type"] == "p":
                for trigger in TAIL_CUT_TRIGGERS:
                    if trigger in b["text"]:
                        blocks = blocks[:i]
                        break
            if i >= len(blocks):
                break

        return {
            **item,
            "source_key": "blockbeats",
            "article_id_full": item.get("article_id_full", f"blockbeats:{item.get('article_id', '')}"),
            "title": title,
            "source": ARTICLE_SOURCE_NAME,
            "cover_src": cover_src,
            "blocks": blocks,
        }

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article.get("article_id", ""))
        path = self.output_dir / f"blockbeats_{raw_id}.json"
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
            "source_key": "blockbeats",
            "article_id": f"blockbeats:{data['article_id']}",
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

    # -- Load all --

    def load_articles(self) -> list[dict]:
        articles = []
        if not self.output_dir.exists():
            return articles
        for f in sorted(self.output_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            articles.append(self.parse_article_file(f))
        return articles

    # -- URL-based fetch --

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        if "theblockbeats.info/news/" not in url:
            raise ValueError(f"invalid BlockBeats article URL: {url}")
        # Extract an article_id from the URL
        m = re.search(r"/news/(\d+)", url)
        article_id = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "article_id": article_id,
            "article_id_full": f"blockbeats:{article_id}",
            "raw_id": article_id,
            "title": "",
            "original_url": url,
            "source": ARTICLE_SOURCE_NAME,
        }
