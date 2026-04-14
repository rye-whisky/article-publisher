# -*- coding: utf-8 -*-
"""Claude 官方博客 (claude.com/blog) scraper for AI articles."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "Claude"
LIST_URL = "https://claude.com/blog"


class ClaudeScraper(BaseScraper):
    """Scraper for claude.com/blog — HTML list + detail."""

    source_key = "claude"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("claude_"):
            raw_id = stem.replace("claude_", "")
            return f"claude:{raw_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Fetch blog list page and extract article links."""
        r = self.session.get(LIST_URL, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"

        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        # Find all blog post links
        for link in soup.find_all("a", href=re.compile(r"^/blog/[^/]+$")):
            href = link.get("href", "").strip()
            if not href or href == "/blog":
                continue

            # Extract slug from href
            slug = href.rstrip("/").rsplit("/", 1)[-1]

            # Find title (h3 inside the link)
            title_elem = link.find("h3")
            title = title_elem.get_text(strip=True) if title_elem else ""

            results.append({
                "article_id": f"claude:{slug}",
                "raw_id": slug,
                "title": title,
                "source": SOURCE_NAME,
                "author": "",
                "publish_time": "",
                "original_url": f"https://claude.com{href}",
                "cover_src": "",
                "abstract": "",
                "blocks": [],
            })

        log.info("[claude] List page returned %d articles", len(results))
        return results

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article page and extract full content."""
        url = item.get("original_url", "")
        if not url:
            return item

        try:
            html = self.fetch_html(url)
            soup = BeautifulSoup(html, "html.parser")

            # Extract title (h1)
            title_elem = soup.find("h1")
            if title_elem:
                item["title"] = title_elem.get_text(strip=True)

            # Extract publish time
            time_elem = soup.find("time") or soup.find("span", class_=re.compile(r"time|date"))
            if time_elem:
                time_text = time_elem.get("datetime") or time_elem.get_text(strip=True)
                item["publish_time"] = self._parse_date(time_text)

            # Extract abstract from meta description
            desc_elem = soup.find("meta", attrs={"name": "description"})
            if desc_elem:
                item["abstract"] = desc_elem.get("content", "")

            # Extract cover image
            og_img = soup.find("meta", property="og:image")
            if og_img:
                item["cover_src"] = og_img.get("content", "")

            # Extract content blocks from article
            blocks = self._extract_content(soup)
            item["blocks"] = blocks

            log.info("[claude] Detail OK: %s (%d blocks)", item["title"][:40], len(blocks))

        except Exception as e:
            log.warning("[claude] Detail fetch failed for %s: %s", url, e)

        return item

    @staticmethod
    def _extract_content(soup: BeautifulSoup) -> list[dict]:
        """Extract article content into blocks."""
        blocks = []

        # Find the article content area - look for main content or article
        article = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"content|article|post", re.I))
        if not article:
            article = soup

        for el in article.find_all(["h2", "h3", "h4", "p", "img", "ul", "ol"]):
            # Skip if nested inside another tracked element
            if el.parent and el.parent.name in ("h2", "h3", "h4", "p", "li", "ul", "ol"):
                continue

            if el.name == "img":
                src = el.get("src") or el.get("data-src") or ""
                if src and not src.startswith("data:"):
                    blocks.append({"type": "img", "src": src})
            elif el.name in ("ul", "ol"):
                for li in el.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
            elif el.name in ("p", "h2", "h3", "h4"):
                text = el.get_text(strip=True)
                if text:
                    tag = el.name if el.name in ("h2", "h3", "h4") else "p"
                    blocks.append({"type": tag, "text": text})

        return blocks

    @staticmethod
    def _parse_date(date_str: str) -> str:
        """Parse various date formats to YYYY-MM-DD HH:MM."""
        if not date_str:
            return ""

        # Try ISO format first
        iso_match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
        if iso_match:
            return iso_match.group(1)

        # Try "Apr 10, 2026" format
        try:
            dt = datetime.strptime(date_str, "%b %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

        # Try other formats
        for fmt in ("%B %d, %Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                continue

        return date_str

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"claude_{raw_id}.json"
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
            "source_key": "claude",
            "article_id": f"claude:{data['article_id']}",
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
