# -*- coding: utf-8 -*-
"""Odaily (星球日报) scraper."""

import json
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")

ARTICLE_SOURCE_NAME = "Odaily星球日报"


class OdailyScraper(BaseScraper):
    """Scraper for Odaily (星球日报) articles.

    Odaily is a Next.js SPA with content embedded in HTML.
    List page: https://www.odaily.news/zh-CN/post
    Detail page: https://www.odaily.news/zh-CN/post/{id}
    """

    source_key = "odaily"

    def _article_id_from_path(self, path: Path) -> str | None:
        """Extract article_id from Odaily JSON file path."""
        if path.suffix != ".json":
            return None
        # Filename: odaily_{id}.json
        stem = path.stem
        if stem.startswith("odaily_"):
            article_id = stem.replace("odaily_", "")
            return f"odaily:{article_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Parse article list from https://www.odaily.news/zh-CN/post

        Odaily is a Next.js SPA, article URLs are embedded in HTML.
        Pattern: "/zh-CN/post/1234567"
        """
        list_url = "https://www.odaily.news/zh-CN/post"
        html = self.fetch_html(list_url)

        # Extract article IDs using regex: "/zh-CN/post/1234567"
        article_ids = re.findall(r'"/zh-CN/post/(\d{7})"', html)

        items = []
        seen = set()
        for aid in article_ids:
            if aid in seen:
                continue
            seen.add(aid)
            items.append({
                "article_id": aid,
                "original_url": f"https://www.odaily.news/zh-CN/post/{aid}",
                "source": ARTICLE_SOURCE_NAME,
            })

        log.info("Odaily found %d articles", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch article detail from Odaily.

        Odaily is a Next.js SPA with content embedded in HTML.
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

        # Cover (og:image)
        og = soup.find("meta", property="og:image")
        cover_src = og["content"] if og else ""

        # Publish time
        publish_time_el = soup.find("meta", attrs={"property": "article:published_time"})
        publish_time = publish_time_el.get("content", "") if publish_time_el else ""

        # Author
        author_el = soup.find("meta", attrs={"property": "article:author"})
        author = author_el.get("content", "") if author_el else ""

        # Description/summary
        desc_el = soup.find("meta", attrs={"name": "description"})
        description = desc_el.get("content", "") if desc_el else ""

        # Content area — Odaily uses various selectors
        # The actual article content is in <p> tags throughout the page
        # AI summary is in a div with specific classes (bg-custom-F2F2F2)
        # We need to skip the AI summary div and capture all other <p> tags

        # Parse blocks — handle paragraphs, headings, images, lists
        blocks = []

        # Find AI summary container to skip
        ai_summary_divs = soup.find_all('div', class_=lambda x: x and any('bg-custom-F2F2F2' in str(c) or 'dark:bg-custom-292929' in str(c) for c in x))

        # Get all p tags from the page, excluding those in AI summary
        all_ps = soup.find_all('p')
        for p in all_ps:
            # Skip if this p is inside an AI summary div
            in_ai_summary = False
            for ai_div in ai_summary_divs:
                if p in ai_div.descendants:
                    in_ai_summary = True
                    break
            if in_ai_summary:
                continue

            # Get text content
            text = p.get_text(strip=True)
            if not text:
                continue

            # Check for images first
            imgs = p.find_all('img')
            if imgs:
                for img in imgs:
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        # Convert relative URLs to absolute
                        if src.startswith("/"):
                            src = "https://www.odaily.news" + src
                        blocks.append({"type": "img", "src": src})

            # Add text block
            blocks.append({"type": "p", "text": text})

        # Also look for headings and standalone images
        for tag in ['h2', 'h3', 'h4']:
            for el in soup.find_all(tag):
                text = el.get_text(strip=True)
                if text:
                    blocks.append({"type": tag, "text": text})

        # Add description as first block if available and blocks is empty
        if not blocks and description:
            blocks.append({"type": "p", "text": description})

        if not blocks:
            raise RuntimeError("无法解析文章内容")

        return {
            **item,
            "source_key": "odaily",
            "article_id_full": item.get("article_id_full", f"odaily:{item.get('article_id', '')}"),
            "title": title,
            "source": ARTICLE_SOURCE_NAME,
            "author": author,
            "publish_time": publish_time,
            "cover_src": cover_src,
            "blocks": blocks,
        }

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", article.get("article_id", ""))
        path = self.output_dir / f"odaily_{raw_id}.json"
        path.write_text(
            json.dumps(
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
            ),
            encoding="utf-8",
        )
        return path

    # -- File parsing --

    def parse_article_file(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {
            "source_key": "odaily",
            "article_id": f"odaily:{data['article_id']}",
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

    # -- URL-based fetch --

    def build_item_from_url(self, url: str, **kwargs) -> dict:
        if "odaily.news" not in url:
            raise ValueError(f"invalid Odaily article URL: {url}")
        m = re.search(r"/zh-CN/post/(\d+)", url)
        if not m:
            m = re.search(r"/post/(\d+)", url)
        article_id = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "article_id": article_id,
            "article_id_full": f"odaily:{article_id}",
            "raw_id": article_id,
            "title": "",
            "original_url": url,
            "source": ARTICLE_SOURCE_NAME,
        }
