# -*- coding: utf-8 -*-
"""AIBase (news.aibase.com) 资讯爬虫."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "AIBase"
BASE_URL = "https://news.aibase.com"
LIST_URL = f"{BASE_URL}/zh/news"


class AibaseScraper(BaseScraper):
    """Scraper for AIBase AI 资讯.

    AIBase 是一个 AI 资讯网站，提供中文 AI 行业新闻。
    通过 HTML 解析获取文章列表和详情。
    文章 URL 格式: /zh/news/{id}
    """

    source_key = "aibase"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.per_page = cfg.get("per_page", 30)

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("aibase_"):
            raw_id = stem.replace("aibase_", "")
            return f"aibase:{raw_id}"
        return None

    @staticmethod
    def _parse_time(time_str: str) -> str:
        """Parse various time formats into standard format."""
        if not time_str:
            return ""
        # Already in standard format
        if re.match(r"\d{4}-\d{2}-\d{2}", time_str):
            return time_str
        # Relative time like "2 天前"
        if "天前" in time_str or "days ago" in time_str.lower():
            return time_str
        # Timestamp
        if time_str.isdigit():
            try:
                dt = datetime.fromtimestamp(int(time_str))
                return dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                pass
        return time_str

    # -- List parsing (via HTML) --

    def parse_list(self) -> list[dict]:
        """Fetch recent articles from listing page.

        AIBase is a Nuxt.js SPA. Try to extract article data from __NUXT__ script,
        fallback to parsing article links from HTML.
        """
        html = self.fetch_html(LIST_URL, timeout=30)

        results = []
        seen_ids = set()

        # Method 1: Try to extract from __NUXT__ data
        nuxt_match = re.search(r'<script>window\.__NUXT__\s*=\s*(\{.+?\});</script>', html, re.DOTALL)
        if nuxt_match:
            try:
                import json
                nuxt_data = json.loads(nuxt_match.group(1))
                # Navigate through the Nuxt data structure to find articles
                # The structure varies, so we try multiple paths
                articles_data = None
                if isinstance(nuxt_data, dict):
                    # Try common paths
                    for key in ["data", "pins", "articles", "list", "items"]:
                        if key in nuxt_data:
                            articles_data = nuxt_data[key]
                            break

                    # Try nested paths
                    if not articles_data:
                        for v in nuxt_data.values():
                            if isinstance(v, dict) and "articles" in v:
                                articles_data = v["articles"]
                                break
                            elif isinstance(v, list):
                                articles_data = v
                                break

                if articles_data and isinstance(articles_data, list):
                    for item in articles_data:
                        if not isinstance(item, dict):
                            continue
                        # Extract article ID from various fields
                        article_id = (
                            item.get("id") or
                            item.get("article_id") or
                            item.get("news_id") or
                            item.get("nid")
                        )
                        if not article_id:
                            # Try to extract from URL
                            url = item.get("url") or item.get("link") or item.get("original_url") or ""
                            match = re.search(r"/zh/news/(\d+)", url)
                            if match:
                                article_id = match.group(1)

                        if article_id and str(article_id) not in seen_ids:
                            seen_ids.add(str(article_id))
                            results.append({
                                "article_id": f"aibase:{article_id}",
                                "raw_id": str(article_id),
                                "title": item.get("title") or item.get("subject") or f"Article {article_id}",
                                "source": SOURCE_NAME,
                                "author": item.get("author") or "",
                                "publish_time": self._parse_time(item.get("time") or item.get("publish_time") or item.get("created_at") or ""),
                                "original_url": item.get("url") or item.get("link") or f"{BASE_URL}/zh/news/{article_id}",
                                "cover_src": item.get("cover") or item.get("image") or item.get("thumb") or "",
                                "abstract": item.get("description") or item.get("summary") or item.get("excerpt") or "",
                                "blocks": [],
                            })
                    log.info("[aibase] Found %d articles from __NUXT__ data", len(results))
                    if results:
                        return results[:self.per_page]
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                log.warning("[aibase] Failed to parse __NUXT__ data: %s", e)

        # Method 2: Fallback - parse article links from HTML
        soup = BeautifulSoup(html, "html.parser")

        # Find all article links
        for link in soup.find_all("a", href=re.compile(r"/zh/news/\d+")):
            href = link.get("href", "")
            match = re.search(r"/zh/news/(\d+)", href)
            if not match:
                continue

            article_id = match.group(1)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            # Build full URL
            full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

            # Try to find title - might be in the link text or a nearby heading
            title = ""
            for candidate in [link] + list(link.find_parents("div", limit=3)):
                # Try to find heading
                heading = candidate.find(["h1", "h2", "h3", "h4"])
                if heading:
                    title = heading.get_text(strip=True)
                    break
                # Try to find element with title class
                title_elem = candidate.find(class_=re.compile(r"title|heading|subject", re.I))
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    break

            if not title:
                title = link.get_text(strip=True) or f"Article {article_id}"

            # Find image
            cover_src = ""
            for candidate in [link] + list(link.find_parents("div", limit=3)):
                img = candidate.find("img")
                if img:
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        cover_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                        break

            results.append({
                "article_id": f"aibase:{article_id}",
                "raw_id": str(article_id),
                "title": title,
                "source": SOURCE_NAME,
                "author": "",
                "publish_time": "",
                "original_url": full_url,
                "cover_src": cover_src,
                "abstract": "",
                "blocks": [],
            })

        log.info("[aibase] Found %d articles from HTML fallback", len(results))
        return results[:self.per_page]

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch and parse article detail page."""
        html = self.fetch_html(item["original_url"], timeout=30)
        soup = BeautifulSoup(html, "html.parser")

        # Extract title from detail page (more reliable than list page)
        title_selectors = [
            "h1.article-title",
            "h1.title",
            "h1.heading",
            "h1",
            'h2[class*="title"]',
            'h2[class*="heading"]',
        ]
        for selector in title_selectors:
            title_elem = soup.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                # Filter out generic titles
                if title and title not in ["AI新闻资讯", "AI资讯", "AIBase", "新闻"]:
                    item["title"] = title
                    break

        # Extract publish time from HTML elements
        if not item.get("publish_time"):
            time_selectors = [
                "time",
                'span[class*="time"]',
                'span[class*="date"]',
                'div[class*="time"]',
                'div[class*="date"]',
                "p.time",
                "p.date",
            ]
            for selector in time_selectors:
                time_elem = soup.select_one(selector)
                if time_elem:
                    # Try datetime attribute first
                    datetime_attr = time_elem.get("datetime") or time_elem.get("data-time")
                    if datetime_attr:
                        item["publish_time"] = self._parse_time(datetime_attr)
                        break
                    # Fallback to text content
                    time_text = time_elem.get_text(strip=True)
                    if time_text and len(time_text) < 50:  # Reasonable time text length
                        item["publish_time"] = time_text
                        break

        # Extract publish time from page text (backup method)
        if not item.get("publish_time"):
            page_text = soup.get_text()
            # Match patterns like "2026年4月14号 15:27"
            date_time_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}号)\s*(\d{1,2}:\d{2})', page_text)
            if date_time_match:
                date_part = date_time_match.group(1)
                time_part = date_time_match.group(2)
                # Convert to standard format
                standard_date = re.sub(r'(\d{4})年(\d{1,2})月(\d{1,2})号', r'\1-\2-\3', date_part)
                item["publish_time"] = f"{standard_date} {time_part}"

        # Extract article content
        content_blocks = self._extract_article_content(soup)
        item["blocks"] = content_blocks

        # Update cover if not found in list (find first valid content image)
        if not item.get("cover_src"):
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src and not src.startswith("data:"):
                    # Check it's not a logo/icon
                    if not any(skip in src.lower() for skip in ["userlogo", "logo", "icon", "avatar", "title_"]):
                        # Also check dimensions if available in URL
                        if not any(size in src for size in ["16x16", "32x32", "48x48"]):
                            item["cover_src"] = src if src.startswith("http") else f"{BASE_URL}{src}"
                            break

        # Extract abstract if not found in list
        if not item.get("abstract"):
            # First paragraph as abstract
            for block in content_blocks:
                if block.get("type") == "p" and len(block.get("text", "")) > 20:
                    item["abstract"] = block["text"][:200]
                    break

        log.info("[aibase] Detail OK: %s (%d blocks, time: %s)", item["title"][:40], len(content_blocks), item.get("publish_time", "N/A")[:20])
        return item

    def _extract_article_content(self, soup: BeautifulSoup) -> list[dict]:
        """Extract article content blocks from detail page."""
        blocks = []

        # Filter out known logo/non-content images
        def is_valid_image(src: str) -> bool:
            """Check if image URL is valid content (not logo, icon, etc.)."""
            if not src:
                return False
            # Skip known logos and non-content images
            skip_patterns = [
                "userlogo",
                "logo",
                "icon",
                "avatar",
                "favicon",
                "title_",  # Often decorative title images
            ]
            src_lower = src.lower()
            for pattern in skip_patterns:
                if pattern in src_lower:
                    return False
            # Skip very small images (likely icons)
            if any(size in src for size in ["16x16", "32x32", "48x48"]):
                return False
            return True

        # Find the main content area
        content_selectors = [
            "article",
            'div[class*="content"]',
            'div[class*="article"]',
            'div[class*="detail"]',
            'main',
        ]

        content_root = None
        for selector in content_selectors:
            content_root = soup.select_one(selector)
            if content_root:
                break

        if not content_root:
            content_root = soup

        # Process all relevant elements
        for elem in content_root.find_all(["h1", "h2", "h3", "h4", "p", "figure", "img", "ul", "ol", "blockquote"]):
            tag = elem.name

            # Skip if already processed (nested elements)
            if elem.find_parent(["h1", "h2", "h3", "h4", "p", "figure", "blockquote"]):
                continue

            # Headings
            if tag in ["h1", "h2", "h3", "h4"]:
                text = elem.get_text(strip=True)
                if text:
                    blocks.append({"type": tag, "text": text})
                continue

            # Images in figure tags
            if tag == "figure":
                img = elem.find("img")
                if img:
                    src = img.get("src") or img.get("data-src") or ""
                    if src and not src.startswith("data:") and is_valid_image(src):
                        full_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                        alt = img.get("alt", "")
                        blocks.append({"type": "img", "src": full_src, "alt": alt})
                continue

            # Standalone images
            if tag == "img":
                src = elem.get("src") or elem.get("data-src") or ""
                if src and not src.startswith("data:") and is_valid_image(src):
                    full_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                    alt = elem.get("alt", "")
                    blocks.append({"type": "img", "src": full_src, "alt": alt})
                continue

            # Paragraphs
            if tag == "p":
                text = elem.get_text(strip=True)
                if not text:
                    # Check for image inside
                    img = elem.find("img")
                    if img:
                        src = img.get("src") or img.get("data-src") or ""
                        if src and not src.startswith("data:") and is_valid_image(src):
                            full_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                            alt = img.get("alt", "")
                            blocks.append({"type": "img", "src": full_src, "alt": alt})
                    continue

                # Check if paragraph is actually a heading (all caps or contains specific patterns)
                if len(text) < 50 and (
                    text.isupper() or
                    re.match(r"^【.*】$", text) or
                    re.match(r"^\d+[\.\s]", text)
                ):
                    blocks.append({"type": "h2", "text": text})
                else:
                    blocks.append({"type": "p", "text": text})
                continue

            # Lists
            if tag in ["ul", "ol"]:
                for li in elem.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": f"• {text}"})
                continue

            # Blockquotes
            if tag == "blockquote":
                text = elem.get_text(strip=True)
                if text:
                    blocks.append({"type": "p", "text": text})

        return blocks

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"aibase_{raw_id}.json"
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
            "source_key": "aibase",
            "article_id": f"aibase:{data['article_id']}",
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
