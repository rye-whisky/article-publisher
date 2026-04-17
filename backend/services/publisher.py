# -*- coding: utf-8 -*-
"""Publisher: COS upload + CMS publish + HTML builder."""

import json
import logging
import re
from datetime import datetime

import requests

from utils.cos import COSUploader

log = logging.getLogger("pipeline")


class Publisher:
    """Handles cover upload (via COSUploader) and CMS API publish."""

    def __init__(self, api_url: str, api_headers: dict, cos_uploader: COSUploader, push_url: str = ""):
        self.api_url = api_url
        self.api_headers = api_headers
        self.cos = cos_uploader
        self.push_url = push_url

    # -- HTML helpers --

    @staticmethod
    def html_escape(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    @staticmethod
    def _strip_punctuation(text: str) -> str:
        """Remove punctuation and whitespace for fuzzy text comparison."""
        return re.sub(r'[\s，。、；：！？""''（）\[\]【】…—\-,.:;!?()\'\"]+', '', text or '')

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
            raw = (block.get("html") or "").strip()
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
            if href:
                parts.append(
                    f'<{tag}><a href="{self.html_escape(href)}" '
                    f'target="_blank" rel="noopener">{self.html_escape(text)}</a></{tag}>'
                )
            else:
                parts.append(f"<{tag}>{self.html_escape(text)}</{tag}>")

        # Rule 2: Append source info at the end
        parts.append(f"<p><strong>来源：</strong>{self.html_escape(article['source'] or '')}</p>")
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
            "strong_content_tags": {},
            "chain_is_calendar": False,
            "chain_calendar_time": int(datetime.now().timestamp()),
            "chain_calendar_tendency": 0,
            "is_push_bian": 2,
            "content_pin_top": 0,
            "is_public": bool(is_public),
            "user_id": "3",
            "chain_fixed_publish_time": 0,
            "as_user_id": "3",
            "is_chain": True,
            "chain_airdrop_time": 0,
            "chain_airdrop_time_end": 0,
        }
        r = requests.post(
            self.api_url,
            headers=self.api_headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        data = r.json()
        if r.status_code == 200 and data.get("code") == 0:
            return {
                "cms_id": data["data"]["id"],
                "cover_image": cover_image,
                "cover_error": cover_error,
                "publish_stage": "published" if is_public else "draft",
            }
        raise RuntimeError(f"publish failed: {r.status_code} {data}")

    def save_draft(self, article):
        """Create or update a CMS draft without making it public."""
        return self._submit(article, is_public=False)

    def publish(self, article):
        """Create or update a CMS article and make it public."""
        return self._submit(article, is_public=True)

    def push_to_app(self, cms_id: str, title: str, push_label: str = "") -> dict:
        """Push a published article as an App desktop notification.

        Args:
            cms_id: The CMS content ID (required).
            title: Original article title (used as push_content).
            push_label: "热文" or "爆文" (used as push_title). Falls back to title.
        """
        if not self.push_url:
            raise RuntimeError("Push URL not configured")
        if not cms_id:
            raise RuntimeError("cms_id is required for push")

        payload = {
            "push_title": push_label or (title or "")[:120] or "资讯推送",
            "push_content": (title or "")[:400],
            "push_id": str(cms_id),
            "push_type": 5,
        }
        r = requests.post(
            self.push_url,
            headers=self.api_headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        data = r.json()
        if r.status_code == 200 and data.get("code") == 0:
            return {"ok": True, "push_id": str(cms_id)}
        raise RuntimeError(f"push failed: {r.status_code} {data}")
