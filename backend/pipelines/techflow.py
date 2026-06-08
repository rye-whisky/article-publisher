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

    def __init__(self, cfg: dict, session, output_dir: Path, db=None):
        super().__init__(cfg, session, output_dir, db)
        # Local fallback for standalone scraping; the canonical rules live in FilterService.
        self.excluded_keywords = ["space", "croo", "bydfi", "赞助商"]

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
        # 深潮首页现在同时存在 /article/ 和 /zh-CN/article/ 两种链接格式。
        for a in soup.select('a[href*="/article/"]'):
            href = a.get("href") or ""
            full_url = urljoin(list_url, href)
            m = re.search(r"/article/(\d+)", full_url)
            if not m:
                continue
            article_id = m.group(1)
            if article_id in seen:
                continue
            title_el = a.find("h3")
            title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True) if title_el else "").strip()
            if not title:
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
            if not title:
                continue
            if any(kw in title.lower() for kw in self.excluded_keywords):
                continue
            seen.add(article_id)
            items.append({"article_id": article_id, "title": title[:120], "original_url": full_url, "source": "深潮 TechFlow"})
        log.info("TechFlow list: found %d articles", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        """Fetch and parse article detail from TechFlow.

        提取规则：
        1. 跳过 <div class="quote"> 开头引用语，并去重正文中重复出现的引用段
        2. 提取灰色文字（rgb(140,140,140)）中的作者/编辑信息
        3. 作者/编辑信息放到文章最后，与来源一起显示
        """
        html = self.fetch_html(item["original_url"])
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article") or soup.find("main") or soup.body
        title = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else item["title"]

        # 封面图在文章头部，不在 art_detail_content 内，需从全文提取
        cover_src = ""
        if article:
            for img in article.find_all("img"):
                src = img.get("src") or ""
                if src and src.startswith("http"):
                    cover_src = src
                    break

        blocks = []
        author_parts = []  # 存储作者、编辑信息的原始格式
        quote_texts = set()  # 收集 <div class="quote"> 的文本，用于去重

        # 优先从 art_detail_content 提取正文，避免海报/分享区域干扰
        content_area = article.find("div", class_="art_detail_content") if article else None
        if not content_area:
            content_area = article

        for el in content_area.find_all(["h2", "h3", "p", "img", "div"]):
            # 跳过引用语块 <div class="quote">，并记录文本用于去重
            if el.name == "div" and "quote" in (el.get("class") or []):
                q = el.get_text(" ", strip=True)
                if q:
                    quote_texts.add(q)
                continue

            if el.name == "img":
                src = el.get("src") or ""
                if src and src.startswith("http"):
                    blocks.append({"type": "img", "src": src, "alt": el.get("alt", "")})
                continue

            # 检查是否是作者/编辑信息（灰色文字）
            # 样式可能是：color: rgb(140, 140, 140) 或 color:rgb(140,140,140)
            if el.name == "p":
                text = el.get_text(" ", strip=True)
                is_gray = False

                # 检查 p 标签本身的 style 属性
                if el.get("style"):
                    style = el.get("style", "").replace(" ", "")
                    if "rgb(140" in style or "rgb(140,140,140)" in style:
                        is_gray = True

                # 检查内部 span 元素的 style 属性
                if not is_gray:
                    for span in el.find_all("span"):
                        if span.get("style"):
                            style = span.get("style", "").replace(" ", "")
                            if "rgb(140" in style or "rgb(140,140,140)" in style:
                                is_gray = True
                                break

                # 灰色文字统一视为作者/编辑信息，提取并跳过
                if is_gray and text:
                    author_parts.append(text)
                    continue

            if el.name in ["h2", "h3"]:
                text = el.get_text(" ", strip=True)
                if not text or text in {title, "TechFlow Selected 深潮精选"} or self._is_leadin_text(text):
                    continue
                if self._is_hook_text(text):
                    break
                blocks.append({"type": el.name, "text": text})
            elif el.name == "p":
                text = el.get_text(" ", strip=True)
                if not text or text in {title, "TechFlow Selected 深潮精选"} or self._is_leadin_text(text):
                    continue
                # 去重：跳过与 <div class="quote"> 相同的段落
                if text in quote_texts:
                    continue
                if self._is_hook_text(text):
                    break
                blocks.append({"type": "p", "text": text})

        while blocks and blocks[-1].get("type") != "img" and self._is_hook_text(blocks[-1].get("text", "")):
            blocks.pop()

        # 构建作者字符串：保留 "作者｜"、"编辑｜" 格式，用 "、" 分隔
        author_str = "、".join(author_parts) if author_parts else ""

        return {
            "source_key": "techflow",
            "article_id_full": f"techflow:{item['article_id']}",
            "article_id": item["article_id"],
            "raw_id": item["article_id"],
            "title": title,
            "author": author_str,
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
