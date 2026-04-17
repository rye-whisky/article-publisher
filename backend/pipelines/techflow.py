# -*- coding: utf-8 -*-
"""TechFlow (深潮) scraper."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")


class TechFlowScraper(BaseScraper):
    """Scraper for TechFlow (深潮 TechFlow) articles."""

    source_key = "techflow"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.excluded_keywords = ["加密早报", "Bitget", "HashKey", "Pharos", "火币", "Deepcoin"]

    def _article_id_from_path(self, path: Path) -> str | None:
        """Extract article_id from TechFlow JSON file path."""
        if path.suffix != ".json":
            return None
        # Filename: techflow_{id}.json
        stem = path.stem
        if stem.startswith("techflow_"):
            article_id = stem.replace("techflow_", "")
            return f"techflow:{article_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        list_url = self.cfg["list_url"]
        html = self.fetch_html(list_url)
        soup = BeautifulSoup(html, "html.parser")
        items = []
        seen = set()
        for a in soup.select('a[href*="/zh-CN/article/"]'):
            href = a.get("href") or ""
            full_url = urljoin(list_url, href)
            m = re.search(r"/article/(\d+)", full_url)
            if not m:
                continue
            article_id = m.group(1)
            if article_id in seen:
                continue
            seen.add(article_id)
            text = a.get_text(" ", strip=True)
            if not text:
                continue
            title = re.sub(r"^\d{4}\.\d{2}\.\d{2}(?:-\s*\d+小时前)?", "", text).strip()
            title = re.sub(r"^(原创)?", "", title).strip()
            if any(kw in title for kw in self.excluded_keywords):
                continue
            items.append({"article_id": article_id, "title": title[:120], "original_url": full_url, "source": "深潮 TechFlow"})
        log.info("TechFlow list: found %d articles", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        html = self.fetch_html(item["original_url"])
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article") or soup.find("main") or soup.body
        title = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else item["title"]
        blocks, cover_src = [], ""
        preamble_done = False
        for el in article.find_all(["h2", "h3", "p", "img"]):
            if el.name == "img":
                src = el.get("src") or ""
                if src and src.startswith("http"):
                    if not cover_src:
                        cover_src = src
                    blocks.append({"type": "img", "src": src, "alt": el.get("alt", "")})
                continue
            text = el.get_text(" ", strip=True)
            if not text or text in {title, "TechFlow Selected 深潮精选"} or self._is_leadin_text(text):
                continue
            # Skip preamble metadata (quote, attribution, guest info, etc.)
            if not preamble_done:
                if self._is_preamble_text(text):
                    continue
                preamble_done = True
            if self._is_hook_text(text):
                break
            blocks.append({"type": el.name, "text": text})
        while blocks and blocks[-1].get("type") != "img" and self._is_hook_text(blocks[-1].get("text", "")):
            blocks.pop()
        return {
            "source_key": "techflow",
            "article_id_full": f"techflow:{item['article_id']}",
            "article_id": item["article_id"],
            "raw_id": item["article_id"],
            "title": title,
            "author": "",
            "source": item["source"],
            "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "original_url": item["original_url"],
            "cover_src": cover_src,
            "blocks": blocks,
        }

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", str(article.get("article_id", "")).split(":")[-1])
        path = self.output_dir / f"techflow_{raw_id}.json"
        content = json.dumps(
            {
                "article_id": raw_id,
                "title": article["title"],
                "source": article["source"],
                "author": article.get("author", ""),
                "publish_time": article.get("publish_time", ""),
                "original_url": article["original_url"],
                "cover_src": article.get("cover_src", ""),
                "blocks": article["blocks"],
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
            "source_key": "techflow",
            "article_id": f"techflow:{data['article_id']}",
            "raw_id": str(data["article_id"]),
            "title": data["title"],
            "author": data.get("author", ""),
            "source": data.get("source", "深潮 TechFlow"),
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

    # -- URL-based refetch (not typically used but fallback) --

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        m = re.search(r"/article/(\d+)", url)
        if not m:
            raise ValueError(f"invalid techflow article url: {url}")
        return {
            "article_id": m.group(1),
            "title": m.group(1),
            "original_url": url,
            "source": "深潮 TechFlow",
        }

    # -- Static helpers --

    @staticmethod
    def _is_leadin_text(text):
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return True
        return any(re.match(p, text, flags=re.IGNORECASE) for p in [r"^深潮导读\s*[：:]?.*$"])

    @staticmethod
    def _is_preamble_text(text):
        """Detect preamble metadata: quotes, attribution, guest info, etc."""
        text = (text or "").strip()
        if not text:
            return True
        # Pull quote: text wrapped in Chinese curly quotes at the start
        if re.match(r'^\u201c.+\u201d$', text):
            return True
        # Pull quote: text wrapped in straight quotes
        if re.match(r'^".+"$', text):
            return True
        # Attribution / guest / host / source / date lines
        return bool(re.match(
            r'^(?:整理\s*[&＆]\s*编译|嘉宾|主持人|播客源|原标题|播出日期|来源|编译|作者)[：:\s]',
            text,
        ))

    @staticmethod
    def _is_hook_text(text):
        text = (text or "").strip()
        return any(
            re.search(p, text, flags=re.IGNORECASE)
            for p in [
                r"欢迎加入深潮\s*TechFlow官方社群",
                r"^Telegram订阅群\s*[：:]",
                r"^Twitter官方账号\s*[：:]",
                r"^Twitter英文账号\s*[：:]",
                r"t\.me/TechFlowDaily",
                r"x\.com/TechFlowPost",
                r"x\.com/BlockFlow_News",
                r"关注.*深潮",
                r"加入.*社群",
            ]
        )
