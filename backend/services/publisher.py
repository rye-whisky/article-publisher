# -*- coding: utf-8 -*-
"""Publisher: COS upload + CMS publish + HTML builder."""

import json
import logging
import re
from datetime import datetime
from typing import Callable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from utils.cos import COSUploader

log = logging.getLogger("pipeline")

INTERNAL_JUMP_DOMAINS = ("blockbeats", "odaily", "techflowpost", "foresightnews")


class ChainThinkAuthError(RuntimeError):
    """Raised when ChainThink rejects a request due to an expired/invalid token."""


AUTH_FAILURE_TERMS = (
    "token", "expired", "invalid", "unauthorized", "forbidden",
    "登录", "未登录", "未授权", "无效", "过期", "请登录",
)


def is_chainthink_auth_failure(status_code: int, data) -> bool:
    if status_code in (401, 403):
        return True
    text = str(data or "").lower()
    if any(term in text for term in ("登录", "未登录", "未授权", "无效", "过期", "请登录")):
        return True
    return "token" in text and any(term in text for term in ("expired", "invalid", "unauthorized", "forbidden"))


def parse_response_json(response):
    try:
        return response.json()
    except ValueError:
        return {"message": response.text[:200]}


class Publisher:
    """Handles cover upload (via COSUploader) and CMS API publish."""

    def __init__(
        self,
        api_url: str,
        api_headers: dict,
        cos_uploader: COSUploader,
        push_url: str = "",
        api_headers_provider: Callable[[], dict] | None = None,
        user_id_provider: Callable[[], str] | None = None,
    ):
        self.api_url = api_url
        self.api_headers = api_headers
        self.api_headers_provider = api_headers_provider
        self.cos = cos_uploader
        self.push_url = push_url
        self._user_id_provider = user_id_provider

    def _headers(self) -> dict:
        if self.api_headers_provider:
            return self.api_headers_provider()
        return dict(self.api_headers or {})

    # -- HTML helpers --

    @staticmethod
    def html_escape(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    @staticmethod
    def _normalize_text_for_match(text: str) -> str:
        return re.sub(r"\s+", "", (text or "")).lower()

    @classmethod
    def _author_contains_source_identity(cls, author: str, source: str) -> bool:
        """Whether author/editor line already contains source identity text."""
        author_n = cls._normalize_text_for_match(author)
        source_n = cls._normalize_text_for_match(source)
        if not author_n or not source_n:
            return False
        if source_n in author_n:
            return True
        aliases = (
            "techflow", "深潮", "blockbeats", "律动", "odaily", "odaily星球日报",
            "chaincatcher", "chainthink", "stcn", "券商中国",
        )
        return any(alias in author_n and alias in source_n for alias in aliases)

    @staticmethod
    def _strip_punctuation(text: str) -> str:
        """Remove punctuation and whitespace for fuzzy text comparison."""
        return re.sub(r'[\s，。、；：！？""''（）\[\]【】…—\-,.:;!?()\'\"]+', '', text or '')

    @staticmethod
    def _is_internal_jump_url(href: str) -> bool:
        if not href:
            return False
        lowered = href.strip().lower()
        if not lowered:
            return False
        parsed = urlparse(lowered)
        haystack = " ".join(part for part in [parsed.netloc, parsed.path, lowered] if part)
        return any(domain in haystack for domain in INTERNAL_JUMP_DOMAINS)

    @classmethod
    def _should_drop_link_block(cls, text: str, href: str) -> bool:
        if not cls._is_internal_jump_url(href):
            return False
        normalized = re.sub(r"\s+", "", (text or "").lower())
        if not normalized:
            return True
        return any(token in normalized for token in ("原文", "原链接", "阅读全文", "阅读原文", "查看原文", "点击查看"))

    @classmethod
    def _sanitize_inline_html(cls, raw_html: str) -> str:
        if not raw_html:
            return ""

        soup = BeautifulSoup(raw_html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            text = anchor.get_text(" ", strip=True)
            if not cls._is_internal_jump_url(href):
                continue
            block_parent = anchor.find_parent(["p", "div", "li"])
            block_text = block_parent.get_text(" ", strip=True) if block_parent else text
            if cls._should_drop_link_block(block_text or text, href):
                if block_parent is not None:
                    block_parent.decompose()
                else:
                    anchor.decompose()
            else:
                anchor.unwrap()

        for tag in soup.find_all(["p", "div", "span", "h2", "h3", "h4"]):
            if tag.find("img"):
                continue
            if not tag.get_text(" ", strip=True):
                tag.decompose()

        return "".join(str(node) for node in soup.contents).strip()

    def build_html(self, article):
        parts = []
        cover_src = article.get("cover_src", "") or ""
        abstract_clean = self._strip_punctuation(article.get("abstract") or "")
        abstract_skipped = False

        for block in article["blocks"]:
            if block.get("type") == "img" and block.get("src"):
                if cover_src and block["src"] == cover_src:
                    continue
                img_url = block["src"]
                cos_url = self._upload_image_to_cos(img_url)
                final_url = cos_url if cos_url else img_url
                parts.append(
                    f'<p><img src="{self.html_escape(final_url)}" '
                    f'alt="{self.html_escape(block.get("alt", ""))}" /></p>'
                )
                continue

            # Extract text for processing
            raw = self._sanitize_inline_html((block.get("html") or "").strip())
            text = (block.get("text") or "").strip()

            # Rule 3: Skip first text block if it duplicates the abstract
            if not abstract_skipped and text:
                block_clean = self._strip_punctuation(text)
                if abstract_clean and len(abstract_clean) > 10 and (
                    abstract_clean in block_clean or block_clean in abstract_clean
                ):
                    abstract_skipped = True
                    continue
                abstract_skipped = True  # Only check the first text block

            # Rule 1: Replace "编者按" with "导语"
            text = text.replace("编者按", "导语") if text else ""
            raw = raw.replace("编者按", "导语") if raw else ""

            if raw:
                tag = block.get("type", "p") if block.get("type") in ["p", "h2", "h3", "h4"] else "p"
                parts.append(f"<{tag}>{raw}</{tag}>")
                continue
            if not text:
                continue
            tag = block.get("type", "p") if block.get("type") in ["p", "h2", "h3", "h4"] else "p"
            href = block.get("href", "")
            if href and self._should_drop_link_block(text, href):
                continue
            if href and not self._is_internal_jump_url(href):
                parts.append(
                    f'<{tag}><a href="{self.html_escape(href)}" '
                    f'target="_blank" rel="noopener">{self.html_escape(text)}</a></{tag}>'
                )
            else:
                parts.append(f"<{tag}>{self.html_escape(text)}</{tag}>")
        # Rule 2: Append author/editor/source info at the end.
        author = (article.get("author") or "").strip()
        source = (article.get("source") or "").strip()
        source_key = (article.get("source_key") or "").strip().lower()
        publish_strategy = (article.get("_publish_strategy") or article.get("published_strategy") or "").strip().lower()
        is_auto_publish = publish_strategy == "auto"
        body_text_normalized = self._normalize_text_for_match("".join(parts))
        has_inline_original_author = "原文作者" in body_text_normalized

        if author and not has_inline_original_author:
            has_author_prefix = any(author.startswith(p) for p in ("作者", "编辑", "撰文", "编译"))
            if author.startswith("原文作者") or has_author_prefix or "：" in author or ":" in author:
                parts.append(f"<p>{self.html_escape(author)}</p>")
            else:
                parts.append(f"<p>作者：{self.html_escape(author)}</p>")

        # TechFlow (深潮): keep author line only, skip source line entirely.
        if source and source_key != "techflow":
            if is_auto_publish and self._author_contains_source_identity(author, source):
                pass
            else:
                source_label = "原文"
                parts.append(f"<p>{source_label}：{self.html_escape(source)}</p>")

        return "".join(parts)

    def _upload_image_to_cos(self, image_url):
        """Upload an image from URL to COS. Returns COS URL or empty string on failure."""
        if not image_url:
            return ""
        try:
            return self.cos.upload_cover_from_url(image_url)
        except Exception as exc:
            log.warning("Failed to upload image %s to COS: %s", image_url[:50], exc)
            return ""

    @staticmethod
    def build_abstract(article):
        if article.get("abstract"):
            return article["abstract"]
        texts = [b["text"].strip() for b in article["blocks"] if b.get("type") != "img" and b.get("text")]
        return re.sub(r"\s+", " ", " ".join(texts))[:180]

    # -- Publish --

    def _submit(self, article, *, is_public: bool):
        cover_image, cover_error = "", ""
        if article.get("cover_src"):
            try:
                cover_image = self.cos.upload_cover_from_url(article["cover_src"])
            except Exception as exc:
                cover_error = str(exc)
                log.warning("Cover upload failed for %s: %s", article.get("article_id_full", ""), exc)

        article_id = str(article.get("cms_id") or "0")
        payload_user_id = str(article.get("user_id") or "3")
        payload = {
            "id": article_id,
            "info": {"cover_image": cover_image} if cover_image else {},
            "is_translate": True,
            "translation": {
                "zh-CN": {
                    "title": article["title"],
                    "text": self.build_html(article),
                    "abstract": self.build_abstract(article),
                }
            },
            "type": 5,
            "admin_detail": {},
            "strong_content_tags": article.get("strong_content_tags") or {},
            "chain_is_calendar": False,
            "chain_calendar_time": int(datetime.now().timestamp()),
            "chain_calendar_tendency": 0,
            "is_push_bian": 2,
            "content_pin_top": 0,
            "is_public": bool(is_public),
            "is_good": bool(article.get("is_good", False)),
            "user_id": payload_user_id,
            "chain_fixed_publish_time": 0,
            "as_user_id": payload_user_id,
            "is_chain": True,
            "chain_airdrop_time": 0,
            "chain_airdrop_time_end": 0,
        }
        r = requests.post(
            self.api_url,
            headers=self._headers(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        data = parse_response_json(r)
        if r.status_code == 200 and data.get("code") == 0:
            return {
                "cms_id": data["data"]["id"],
                "cover_image": cover_image,
                "cover_error": cover_error,
                "publish_stage": "published" if is_public else "draft",
            }
        if is_chainthink_auth_failure(r.status_code, data):
            raise ChainThinkAuthError("ChainThink token expired or invalid")
        raise RuntimeError(f"publish failed: {r.status_code} {data}")

    def save_draft(self, article):
        """Create or update a CMS draft without making it public."""
        return self._submit(article, is_public=False)

    def publish(self, article):
        """Create or update a CMS article and make it public."""
        return self._submit(article, is_public=True)

    def push_to_app(self, cms_id: str, title: str, push_label: str = "", push_content: str = "") -> dict:
        """Push a published article as an App desktop notification.

        Args:
            cms_id: The CMS content ID (required).
            title: Original article title (used as push_content fallback).
            push_label: "热文" or "爆文" (used as push_title). Falls back to title.
            push_content: Custom push body text. Falls back to title.
        """
        if not self.push_url:
            raise RuntimeError("Push URL not configured")
        if not cms_id:
            raise RuntimeError("cms_id is required for push")

        payload = {
            "push_title": push_label or (title or "")[:120] or "资讯推送",
            "push_content": (push_content or title or "")[:400],
            "push_id": str(cms_id),
            "push_type": 5,
        }
        r = requests.post(
            self.push_url,
            headers=self._headers(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        data = parse_response_json(r)
        if r.status_code == 200 and data.get("code") == 0:
            return {"ok": True, "push_id": str(cms_id)}
        if is_chainthink_auth_failure(r.status_code, data):
            raise ChainThinkAuthError("ChainThink token expired or invalid")
        raise RuntimeError(f"push failed: {r.status_code} {data}")
