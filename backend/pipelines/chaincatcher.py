# -*- coding: utf-8 -*-
"""ChainCatcher (链捕手) scraper."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")

ARTICLE_SOURCE_NAME = "链捕手 ChainCatcher"


class ChainCatcherScraper(BaseScraper):
    """Scraper for ChainCatcher (链捕手) articles. URL-only, no listing page."""

    source_key = "chaincatcher"

    def _article_id_from_path(self, path: Path) -> str | None:
        """Extract article_id from ChainCatcher JSON file path."""
        if path.suffix != ".json":
            return None
        # Filename: chaincatcher_{id}.json
        stem = path.stem
        if stem.startswith("chaincatcher_"):
            article_id = stem.replace("chaincatcher_", "")
            return f"chaincatcher:{article_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Parse article list from https://www.chaincatcher.com/article

        ChainCatcher is a Nuxt.js SPA, article IDs are embedded in HTML.
        Pattern: "/article/2255956"
        """
        list_url = "https://www.chaincatcher.com/article"
        html = self.fetch_html(list_url)

        # Extract article IDs using regex: "/article/1234567"
        article_ids = re.findall(r'"/article/(\d{7})"', html)

        items = []
        seen = set()
        for aid in article_ids:
            if aid in seen:
                continue
            seen.add(aid)
            items.append({
                "article_id": aid,
                "original_url": f"https://www.chaincatcher.com/article/{aid}",
                "source": ARTICLE_SOURCE_NAME,
            })

        log.info("ChainCatcher found %d articles", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article detail from ChainCatcher.

        ChainCatcher is a Nuxt.js SPA with content embedded in HTML.
        """
        url = item["original_url"]
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # Title — try meta tags first, then h1
        title_el = soup.find("meta", property="og:title")
        if title_el:
            title = title_el.get("content", "")
        else:
            title_el = soup.find("h1")
            title = title_el.get_text(strip=True) if title_el else ""

        # Remove " - ChainCatcher" suffix from title
        title = re.sub(r'\s*-\s*ChainCatcher$', '', title, flags=re.IGNORECASE)

        # Cover (og:image)
        og = soup.find("meta", property="og:image")
        cover_src = og["content"] if og else ""

        # Publish time — use crawl time
        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Content area — ChainCatcher uses .rich_text_content class
        content = soup.select_one(".rich_text_content")
        if not content:
            # Fallback: look for class containing "rich_text" or "article-content"
            content = soup.find("div", class_=re.compile(r"rich_text|article-content", re.I))

        if not content:
            raise RuntimeError("页面中未找到文章内容区域")

        # Parse blocks — flatten nested <div> wrappers
        # Regular articles: rich_text_content > div > (p, h2, h3, ...)
        # 早报 articles:   rich_text_content > (p, h3, ul, div, p, div, ...)
        # <div> elements are treated as transparent wrappers.
        blocks = []

        def _parse_children(parent):
            for child in parent.children:
                if not hasattr(child, "name") or not child.name:
                    continue
                if child.name == "div":
                    _parse_children(child)
                elif child.name in ("p", "h2", "h3", "h4"):
                    imgs = child.find_all("img")
                    if imgs:
                        for img in imgs:
                            src = img.get("src") or img.get("data-src") or ""
                            if src and not src.startswith("data:"):
                                blocks.append({"type": "img", "src": src})
                    text = child.get_text(strip=True)
                    if text:
                        blocks.append({"type": child.name, "text": text})
                elif child.name == "img":
                    src = child.get("src") or child.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        blocks.append({"type": "img", "src": src})
                elif child.name == "ul":
                    for li in child.find_all("li"):
                        text = li.get_text(strip=True)
                        if text:
                            blocks.append({"type": "p", "text": text})

        _parse_children(content)

        if not blocks:
            raise RuntimeError("无法解析文章内容")

        return {
            **item,
            "source_key": "chaincatcher",
            "article_id_full": item.get("article_id_full", f"chaincatcher:{item.get('article_id', '')}"),
            "title": title,
            "source": ARTICLE_SOURCE_NAME,
            "author": "",
            "publish_time": publish_time,
            "cover_src": cover_src,
            "blocks": blocks,
        }

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article.get("article_id", ""))
        path = self.output_dir / f"chaincatcher_{raw_id}.json"
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
            "source_key": "chaincatcher",
            "article_id": f"chaincatcher:{data['article_id']}",
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
        if "chaincatcher.com" not in url:
            raise ValueError(f"invalid ChainCatcher article URL: {url}")
        m = re.search(r"/article/(\d+)", url)
        article_id = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "article_id": article_id,
            "article_id_full": f"chaincatcher:{article_id}",
            "raw_id": article_id,
            "title": "",
            "original_url": url,
            "source": ARTICLE_SOURCE_NAME,
        }
