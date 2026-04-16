# -*- coding: utf-8 -*-
"""新智元 (aiera.com.cn) 资讯爬虫 — WordPress REST API."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "新智元"
API_BASE = "https://aiera.com.cn/wp-json/wp/v2"


class AieraScraper(BaseScraper):
    """Scraper for 新智元 AI 资讯.

    新智元是 WordPress 站点，使用 Blocksy 主题。
    通过 WP REST API 获取文章列表和详情，比 HTML 抓取更稳定。
    API: /wp-json/wp/v2/posts?per_page=N
    文章 URL 格式: /YYYY/MM/DD/other/{author}/{post_id}/{slug}/
    """

    source_key = "aiera"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.per_page = cfg.get("per_page", 20)

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("aiera_"):
            raw_id = stem.replace("aiera_", "")
            return f"aiera:{raw_id}"
        return None

    # -- List parsing (via WP REST API) --

    def parse_list(self) -> list[dict]:
        """Fetch recent posts via WordPress REST API."""
        url = f"{API_BASE}/posts"
        params = {"per_page": self.per_page, "_fields": "id,date,title,link,excerpt,featured_media,content"}
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        posts = r.json()

        results = []
        for post in posts:
            post_id = post["id"]
            title = post.get("title", {}).get("rendered", "")
            # Strip HTML tags from title
            title = re.sub(r"<[^>]+>", "", title).strip()

            # Extract abstract from excerpt
            excerpt_html = post.get("excerpt", {}).get("rendered", "")
            abstract = re.sub(r"<[^>]+>", "", excerpt_html).strip()
            if abstract.endswith("[&hellip;]"):
                abstract = abstract[:-10].rstrip(". ")

            # Date
            date_str = post.get("date", "")
            publish_time = ""
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str)
                    publish_time = dt.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    publish_time = date_str

            results.append({
                "article_id": f"aiera:{post_id}",
                "raw_id": str(post_id),
                "title": title,
                "source": SOURCE_NAME,
                "author": "",
                "publish_time": publish_time,
                "original_url": post.get("link", ""),
                "cover_src": "",  # filled in fetch_detail
                "abstract": abstract,
                "blocks": [],  # filled in fetch_detail
                "_content_html": post.get("content", {}).get("rendered", ""),
                "_featured_media": post.get("featured_media", 0),
            })

        log.info("[aiera] API returned %d articles", len(results))
        return results

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Parse article content from API data (no extra fetch needed)."""
        content_html = item.pop("_content_html", "")
        featured_media = item.pop("_featured_media", 0)

        # Cover image from featured media
        if featured_media:
            try:
                media_url = f"{API_BASE}/media/{featured_media}"
                r = self.session.get(media_url, timeout=15)
                if r.status_code == 200:
                    media = r.json()
                    src = media.get("source_url", "")
                    if src:
                        item["cover_src"] = src
            except Exception as e:
                log.warning("[aiera] Failed to fetch media %s: %s", featured_media, e)

        # Fallback: extract first image from content as cover
        if not item.get("cover_src") and content_html:
            soup = BeautifulSoup(content_html, "html.parser")
            img = soup.find("img")
            if img:
                src = img.get("src", "")
                if src and "gravatar" not in src:
                    item["cover_src"] = src

        # Parse content HTML into blocks
        if content_html:
            blocks = self._html_to_blocks(content_html)
            item["blocks"] = blocks
            log.info("[aiera] Detail OK: %s (%d blocks)", item["title"][:40], len(blocks))

        return item

    @staticmethod
    def _html_to_blocks(html: str) -> list[dict]:
        """Parse WordPress content HTML into standardized blocks.

        WP content uses standard HTML: <p>, <h2>-<h4>, <figure><img>, <ul>/<ol>.
        Subtitles often appear as <p><strong>text</strong></p>.
        """
        soup = BeautifulSoup(html, "html.parser")
        blocks = []

        # Find the root content container or use the whole soup
        # WP REST API returns content without wrapper div
        root = soup

        for child in root.children:
            if not hasattr(child, "name") or child.name is None:
                continue

            # Skip empty text nodes
            if isinstance(child, str):
                continue

            # <figure>: image block
            if child.name == "figure":
                img = child.find("img")
                if img:
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        blocks.append({"type": "img", "src": src})
                continue

            # <h2>-<h4>: heading blocks
            if child.name in ("h2", "h3", "h4"):
                text = child.get_text(strip=True)
                if text:
                    blocks.append({"type": child.name, "text": text})
                continue

            # <p>: paragraph (may contain <strong> for subtitles or <img>)
            if child.name == "p":
                text = child.get_text(strip=True)
                if not text:
                    # Check for image inside paragraph
                    img = child.find("img")
                    if img:
                        src = img.get("src") or img.get("data-src") or ""
                        if src and not src.startswith("data:"):
                            blocks.append({"type": "img", "src": src})
                    continue

                # Check if this is a subtitle (<p><strong>text</strong></p>)
                # Only treat as heading if the paragraph contains ONLY a <strong>
                strong = child.find("strong")
                if strong and strong.get_text(strip=True) == text:
                    blocks.append({"type": "h2", "text": text})
                else:
                    blocks.append({"type": "p", "text": text})
                continue

            # <ul>/<ol>: list items
            if child.name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
                continue

            # Nested div: might contain paragraphs/images
            if child.name == "div":
                for sub in child.find_all(["p", "img", "h2", "h3", "h4", "figure"]):
                    if sub.name == "figure":
                        img = sub.find("img")
                        if img:
                            src = img.get("src") or img.get("data-src") or ""
                            if src and not src.startswith("data:"):
                                blocks.append({"type": "img", "src": src})
                    elif sub.name == "img":
                        src = sub.get("src") or sub.get("data-src") or ""
                        if src and not src.startswith("data:"):
                            blocks.append({"type": "img", "src": src})
                    elif sub.name in ("h2", "h3", "h4"):
                        text = sub.get_text(strip=True)
                        if text:
                            blocks.append({"type": sub.name, "text": text})
                    elif sub.name == "p":
                        text = sub.get_text(strip=True)
                        if text:
                            strong = sub.find("strong")
                            if strong and strong.get_text(strip=True) == text:
                                blocks.append({"type": "h2", "text": text})
                            else:
                                blocks.append({"type": "p", "text": text})

        return blocks

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"aiera_{raw_id}.json"
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
                "tags": article.get("tags", []),
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
            "source_key": "aiera",
            "article_id": f"aiera:{data['article_id']}",
            "raw_id": str(data["article_id"]),
            "title": data.get("title", path.stem),
            "author": data.get("author", ""),
            "source": data.get("source", SOURCE_NAME),
            "publish_time": data.get("publish_time", ""),
            "original_url": data.get("original_url", ""),
            "cover_src": data.get("cover_src", ""),
            "abstract": data.get("abstract", ""),
            "tags": data.get("tags", []),
            "blocks": data.get("blocks", []),
            "path": str(path),
        }
