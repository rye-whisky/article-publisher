# -*- coding: utf-8 -*-
"""量子位 (qbitai.com) 资讯爬虫 — WordPress 站点 HTML 抓取."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.base import BaseScraper

log = logging.getLogger("pipeline")

SOURCE_NAME = "量子位"
CATEGORY_URL = "https://www.qbitai.com/category/%E8%B5%84%E8%AE%AF"


class QbitaiScraper(BaseScraper):
    """Scraper for 量子位 AI 资讯频道.

    量子位是 WordPress 站点，列表页和详情页均为服务端渲染 HTML。
    文章 URL 格式: https://www.qbitai.com/YYYY/MM/{post_id}.html
    """

    source_key = "qbitai"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.category_url = cfg.get("category_url", CATEGORY_URL)

    def _article_id_from_path(self, path: Path) -> str | None:
        if path.suffix != ".json":
            return None
        stem = path.stem
        if stem.startswith("qbitai_"):
            raw_id = stem.replace("qbitai_", "")
            return f"qbitai:{raw_id}"
        return None

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        """Fetch category page and extract article cards."""
        r = self.session.get(self.category_url, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"

        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        for item in soup.find_all("div", class_="picture_text"):
            # Title + URL
            h4 = item.find("h4")
            if not h4:
                continue
            a = h4.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "").strip()
            if not title or not href:
                continue

            # Extract post_id from URL: /YYYY/MM/{id}.html
            m = re.search(r"/(\d+)\.html$", href)
            if not m:
                continue
            raw_id = m.group(1)

            # Cover image
            cover_src = ""
            pic_div = item.find("div", class_="picture")
            if pic_div:
                img = pic_div.find("img")
                if img:
                    cover_src = img.get("src", "") or img.get("data-src", "") or ""

            # Time (relative, e.g. "3分钟前", "19小时前")
            time_span = item.find("span", class_="time")
            time_text = time_span.get_text(strip=True) if time_span else ""

            # Author
            author_span = item.find("span", class_="author")
            author = author_span.get_text(strip=True) if author_span else ""

            # Abstract (first <p> in text_box)
            abstract = ""
            text_box = item.find("div", class_="text_box")
            if text_box:
                p = text_box.find("p")
                if p:
                    abstract = p.get_text(strip=True)

            results.append({
                "article_id": f"qbitai:{raw_id}",
                "raw_id": raw_id,
                "title": title,
                "source": SOURCE_NAME,
                "author": author,
                "publish_time": time_text,
                "original_url": href,
                "cover_src": cover_src,
                "abstract": abstract,
                "blocks": [],  # filled by fetch_detail
            })

        log.info("[qbitai] List page returned %d articles", len(results))
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

            article_div = soup.find("div", class_="article")
            if not article_div:
                log.warning("[qbitai] No div.article found for %s", url)
                return item

            # Title (first h1)
            h1 = article_div.find("h1")
            if h1:
                item["title"] = h1.get_text(strip=True)

            # Date from span.date
            date_span = soup.find("span", class_="date")
            if date_span:
                item["publish_time"] = date_span.get_text(strip=True)

            # Abstract from div.zhaiyao
            zhaiyao = article_div.find("div", class_="zhaiyao")
            if zhaiyao:
                item["abstract"] = zhaiyao.get_text(strip=True)

            # Cover from og:image (more reliable than list page)
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                cover = og_img["content"]
                # Prefer actual cover over the generic logo
                if "qbitai-logo" not in cover and "qbitai_icon" not in cover:
                    item["cover_src"] = cover

            # Tags
            tags_div = soup.find("div", class_="tags")
            if tags_div:
                tags = [a.get_text(strip=True) for a in tags_div.find_all("a") if a.get_text(strip=True)]
                if tags:
                    item["tags"] = tags

            # Content blocks (pass title to skip it)
            blocks = self._extract_blocks(article_div, title=item.get("title", ""))
            item["blocks"] = blocks
            log.info("[qbitai] Detail OK: %s (%d blocks)", item["title"][:40], len(blocks))

        except Exception as e:
            log.warning("[qbitai] Detail fetch failed for %s: %s", url, e)

        return item

    @staticmethod
    def _extract_blocks(article_div: BeautifulSoup, title: str = "") -> list[dict]:
        """Extract content blocks from div.article.

        Notes on 量子位 HTML structure:
        - Section subtitles use <h1> (not h2/h3), mapped to h2 blocks
        - The first h1 is the article title — skipped (already stored in title field)
        - Author byline is in <blockquote>, skipped
        - Summary is in div.zhaiyao, skipped (stored separately)
        - Footer/copyright is in div.line_font, skipped
        """
        blocks = []
        first_h1_seen = False

        # Skip these containers (byline, summary, footer, tags)
        skip_classes = {"zhaiyao", "line_font", "tags", "article_info", "person_box"}
        skip_ids = {"related", "xiangguan"}

        for child in article_div.children:
            if not hasattr(child, "name") or child.name is None:
                continue

            # Skip known non-content divs
            if child.name == "div":
                cls_set = set(child.get("class", []))
                if cls_set & skip_classes:
                    continue
                child_id = child.get("id", "")
                if child_id in skip_ids:
                    continue
                # Skip empty or script-only divs
                if not child.get_text(strip=True) and not child.find("img"):
                    continue

            # Blockquote: byline ("鱼羊 发自 凹非寺"), skip
            if child.name == "blockquote":
                continue

            # h1: section subtitle (量子位用 h1 代替 h2/h3)
            # Skip the first h1 (article title, already in title field)
            if child.name == "h1":
                text = child.get_text(strip=True)
                if not first_h1_seen:
                    first_h1_seen = True
                    continue
                if text:
                    blocks.append({"type": "h2", "text": text})
                continue

            # h2/h3/h4: standard headings
            if child.name in ("h2", "h3", "h4"):
                text = child.get_text(strip=True)
                if text:
                    blocks.append({"type": child.name, "text": text})
                continue

            # img
            if child.name == "img":
                src = child.get("src") or child.get("data-src") or ""
                if src and not src.startswith("data:") and "head.jpg" not in src:
                    blocks.append({"type": "img", "src": src})
                continue

            # p: paragraph
            if child.name == "p":
                # Skip empty paragraphs or author attribution
                text = child.get_text(strip=True)
                if not text:
                    # Check for images inside p
                    img = child.find("img")
                    if img:
                        src = img.get("src") or img.get("data-src") or ""
                        if src and not src.startswith("data:") and "head.jpg" not in src:
                            blocks.append({"type": "img", "src": src})
                    continue
                # Skip byline patterns
                if text.startswith("量子位 | 公众号") or "发自 凹非寺" in text:
                    continue
                blocks.append({"type": "p", "text": text})
                continue

            # ul/ol
            if child.name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        blocks.append({"type": "p", "text": text})
                continue

            # Nested div: might contain p/img children
            if child.name == "div":
                for sub in child.find_all(["p", "img", "h1", "h2", "h3", "h4", "ul", "ol"]):
                    # Skip deeply nested (already handled by parent)
                    if sub.parent and sub.parent.name in ("p", "blockquote"):
                        continue

                    if sub.name == "img":
                        src = sub.get("src") or sub.get("data-src") or ""
                        if src and not src.startswith("data:") and "head.jpg" not in src:
                            blocks.append({"type": "img", "src": src})
                    elif sub.name in ("h1",):
                        text = sub.get_text(strip=True)
                        if text:
                            blocks.append({"type": "h2", "text": text})
                    elif sub.name in ("h2", "h3", "h4"):
                        text = sub.get_text(strip=True)
                        if text:
                            blocks.append({"type": sub.name, "text": text})
                    elif sub.name in ("ul", "ol"):
                        for li in sub.find_all("li", recursive=False):
                            text = li.get_text(strip=True)
                            if text:
                                blocks.append({"type": "p", "text": text})
                    elif sub.name == "p":
                        text = sub.get_text(strip=True)
                        if text and not text.startswith("量子位 | 公众号") and "发自 凹非寺" not in text:
                            blocks.append({"type": "p", "text": text})

        return blocks

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = article.get("raw_id", "")
        path = self.output_dir / f"qbitai_{raw_id}.json"
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
            "source_key": "qbitai",
            "article_id": f"qbitai:{data['article_id']}",
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
