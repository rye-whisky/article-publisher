# -*- coding: utf-8 -*-
"""STCN (证券中国) scraper."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger("pipeline")


class StcnScraper(BaseScraper):
    """Scraper for STCN (券商中国) articles."""

    source_key = "stcn"

    def __init__(self, cfg: dict, session, output_dir: Path):
        super().__init__(cfg, session, output_dir)
        self.allowed_authors = set(cfg.get("allowed_authors", []))

    # -- List parsing --

    def parse_list(self) -> list[dict]:
        list_url = self.cfg["list_url"]
        html = self.fetch_html(list_url)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        items = []
        seen = set()
        for a in soup.select('a[href*="/article/detail/"]'):
            href = a.get("href") or ""
            if "/article/detail/" not in href:
                continue
            full_url = urljoin(list_url, href)
            m = re.search(r"/detail/(\d+)\.html", full_url)
            if not m:
                continue
            article_id = m.group(1)
            if article_id in seen:
                continue
            seen.add(article_id)
            title = (a.get_text(" ", strip=True) or "").strip()
            if not title or len(title) > 120:
                continue
            article_block = a.find_parent("li") or a.find_parent(class_="content") or a.parent
            block = (
                article_block.get_text(" ", strip=True)
                if article_block
                else (a.parent.get_text(" ", strip=True) if a.parent else text)
            )
            author_match = re.search(
                r"券商中国\s+(沐阳|周乐)\s+(\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2})", block
            )
            if not author_match:
                continue
            author = author_match.group(1)
            time_text = author_match.group(2)
            publish_time = (
                f"{self._today_str()} {time_text}"
                if re.fullmatch(r"\d{2}:\d{2}", time_text)
                else f"2026-{time_text.replace(' ', ' ')}"
            )
            items.append(
                {
                    "article_id": article_id,
                    "title": title,
                    "author": author,
                    "publish_time": publish_time,
                    "original_url": full_url,
                    "source": "券商中国",
                }
            )
        # Filter by allowed authors
        if self.allowed_authors:
            items = [it for it in items if it.get("author") in self.allowed_authors]
        log.info("STCN list: found %d articles", len(items))
        return items

    # -- Detail fetching --

    def fetch_detail(self, item: dict) -> dict:
        html = self.fetch_html(item["original_url"])
        soup = BeautifulSoup(html, "html.parser")
        text = self._extract_body_from_soup(soup)
        body = self._clean_body(text)
        return {
            **item,
            "source_key": "stcn",
            "article_id_full": f"stcn:{item['article_id']}",
            "blocks": self._blocks_from_plain_text(body),
        }

    # -- Save --

    def save(self, article: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{self._today_str()}_{article['article_id']}_{self._sanitize(article['title'])}.md"
        path = self.output_dir / fname
        body = "\n\n".join([b["text"] for b in article["blocks"] if b["type"] != "img"])
        content = (
            f"# {article['title']}\n\n"
            f"**来源**：{article['source']}\n"
            f"**作者**：{article['author']}\n"
            f"**发布时间**：{article['publish_time']}\n"
            f"**原文链接**：{article['original_url']}\n\n---\n\n{body}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path

    # -- File parsing --

    def parse_article_file(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        author = re.search(r"\*\*作者\*\*：(.+)", text)
        source = re.search(r"\*\*来源\*\*：(.+)", text)
        publish_time = re.search(r"\*\*发布时间\*\*：(.+)", text)
        original_url = re.search(r"\*\*原文链接\*\*：(.+)", text)
        aid = re.search(r"/detail/(\d+)\.html", text)
        body = text.split("---", 1)[1] if "---" in text else text
        return {
            "source_key": "stcn",
            "article_id": f"stcn:{aid.group(1) if aid else path.stem}",
            "raw_id": aid.group(1) if aid else path.stem,
            "title": title.group(1).strip() if title else path.stem,
            "author": author.group(1).strip() if author else "",
            "source": source.group(1).strip() if source else "券商中国",
            "publish_time": publish_time.group(1).strip() if publish_time else "",
            "original_url": original_url.group(1).strip() if original_url else "",
            "blocks": self._blocks_from_plain_text(self._clean_body(body)),
            "path": str(path),
        }

    # -- Load all --

    def load_articles(self) -> list[dict]:
        articles = []
        if not self.output_dir.exists():
            return articles
        for f in sorted(self.output_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.suffix == ".json":
                articles.append(self._parse_json_article_file(f))
            elif f.suffix == ".md":
                articles.append(self.parse_article_file(f))
        return articles

    # -- URL-based refetch --

    def build_item_from_url(self, url: str, author: str = "", publish_time: str = "") -> dict:
        m = re.search(r"/detail/(\d+)\.html", url)
        if not m:
            raise ValueError(f"invalid stcn detail url: {url}")
        article_id = m.group(1)
        try:
            for item in self.parse_list():
                if item.get("article_id") == article_id:
                    return {
                        "article_id": article_id,
                        "title": item.get("title") or article_id,
                        "author": author or item.get("author", ""),
                        "publish_time": publish_time or item.get("publish_time", ""),
                        "original_url": url,
                        "source": item.get("source", "券商中国"),
                    }
        except Exception:
            pass
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        title_node = soup.find("h1")
        title = (
            title_node.get_text(" ", strip=True)
            if title_node
            else (soup.title.get_text(" ", strip=True) if soup.title else article_id)
        )
        text = soup.get_text("\n", strip=True)
        if not author:
            ma = re.search(r"券商中国\s*(沐阳|周乐)", text)
            if ma:
                author = ma.group(1)
        if not publish_time:
            mt = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}:\d{2})", text)
            if mt:
                t = mt.group(1)
                publish_time = (
                    f"{self._today_str()} {t}"
                    if re.fullmatch(r"\d{2}:\d{2}", t)
                    else (f"2026-{t}" if re.fullmatch(r"\d{2}-\d{2}\s+\d{2}:\d{2}", t) else t)
                )
        return {
            "article_id": article_id,
            "title": title,
            "author": author,
            "publish_time": publish_time,
            "original_url": url,
            "source": "券商中国",
        }

    # -- Static helpers --

    @staticmethod
    def _clean_body(md_text):
        body = md_text.replace("\r\n", "\n")
        if "---" in body:
            body = body.split("---", 1)[-1]
        body = re.sub(
            r"^SECURITY NOTICE:[\s\S]*?<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\nSource: Web Fetch\n---\n",
            "",
            body,
        )
        body = re.sub(r"\n<<<END_EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\s*$", "", body)

        stop_patterns = [
            r'\n\s*排版[：:]', r'\n\s*校对[：:]', r'\n\s*责编[：:]', r'\n\s*责任编辑[：:]',
            r'\n\s*声明[：:]', r'\n\s*版权声明[：:]', r'\n\s*转载声明[：:]', r'\n\s*风险提示[：:]',
            r'\n\s*下载["""]?证券时报["""]?官方APP',
            r'\n\s*(?:或)?关注官方微信(?:公众)?号', r'\n\s*微信编辑器',
        ]
        cut_positions = [m.start() for p in stop_patterns for m in [re.search(p, body, flags=re.IGNORECASE)] if m]
        if cut_positions:
            body = body[: min(cut_positions)]

        lines = []
        for line in body.split("\n"):
            line = line.strip()
            if not line:
                lines.append("")
                continue
            if re.match(r"^(来源|作者|原标题)\s*[：:]", line):
                continue
            if re.search(
                r'下载["""]?证券时报["""]?官方APP|关注官方微信(?:公众)?号|不构成实质性投资建议|据此操作风险自担',
                line,
            ):
                continue
            lines.append(line)
        body = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", body).strip()

    @staticmethod
    def _extract_body_from_soup(soup):
        candidates = []
        selectors = [
            "div.detail-content", "div.detail-content-wrapper",
            "article", 'div[class*="detail"]', 'div[class*="article"]',
        ]
        seen = set()
        for selector in selectors:
            for node in soup.select(selector):
                nid = id(node)
                if nid in seen:
                    continue
                seen.add(nid)
                paragraphs = [
                    re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
                    for el in node.find_all(["p", "h2", "h3", "li"])
                    if el.get_text(" ", strip=True).strip()
                ]
                if not paragraphs:
                    continue
                score = len(paragraphs)
                joined = "\n\n".join(paragraphs)
                if re.search(r"排版[：:]|校对[：:]|声明[：:]", joined):
                    score += 3
                if len(joined) > 500:
                    score += 3
                candidates.append((score, joined))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        return soup.get_text("\n", strip=True)

    @staticmethod
    def _blocks_from_plain_text(body):
        blocks = []
        for p in re.split(r"\n\s*\n", body):
            p = p.strip()
            if not p:
                continue
            if len(p) <= 24 and all(m not in p for m in ["。", "！", "？"]):
                blocks.append({"type": "h2", "text": p})
            else:
                blocks.append({"type": "p", "text": p})
        return blocks

    @staticmethod
    def _parse_json_article_file(path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {
            "source_key": "stcn",
            "article_id": f"stcn:{data.get('raw_id', data.get('article_id', path.stem))}",
            "raw_id": str(data.get("raw_id", data.get("article_id", path.stem))),
            "title": data.get("title", path.stem),
            "author": data.get("author", ""),
            "source": data.get("source", "券商中国"),
            "publish_time": data.get("publish_time", ""),
            "original_url": data.get("original_url", ""),
            "cover_src": data.get("cover_src", ""),
            "blocks": data.get("blocks", []),
            "path": str(path),
        }

    @staticmethod
    def _today_str():
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _sanitize(name):
        return re.sub(r'[\\/:*?"<>|]', "_", name)
