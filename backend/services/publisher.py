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

    def __init__(self, api_url: str, api_headers: dict, cos_uploader: COSUploader):
        self.api_url = api_url
        self.api_headers = api_headers
        self.cos = cos_uploader

    # -- HTML helpers --

    @staticmethod
    def html_escape(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def build_html(self, article):
        parts = [f"<p><strong>来源：</strong>{self.html_escape(article['source'] or '')}</p>"]
        cover_src = article.get("cover_src", "") or ""
        for block in article["blocks"]:
            if block.get("type") == "img" and block.get("src"):
                if cover_src and block["src"] == cover_src:
                    continue
                parts.append(
                    f'<p><img src="{self.html_escape(block["src"])}" '
                    f'alt="{self.html_escape(block.get("alt", ""))}" /></p>'
                )
                continue
            # raw HTML — 不做转义，直接插入
            raw = (block.get("html") or "").strip()
            if raw:
                tag = block.get("type", "p") if block.get("type") in ["p", "h2", "h3", "h4"] else "p"
                parts.append(f"<{tag}>{raw}</{tag}>")
                continue
            text = (block.get("text") or "").strip()
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
        return "".join(parts)

    @staticmethod
    def build_abstract(article):
        if article.get("abstract"):
            return article["abstract"]
        texts = [b["text"].strip() for b in article["blocks"] if b.get("type") != "img" and b.get("text")]
        return re.sub(r"\s+", " ", " ".join(texts))[:180]

    # -- Publish --

    def publish(self, article):
        cover_image, cover_error = "", ""
        if article.get("cover_src"):
            try:
                cover_image = self.cos.upload_cover_from_url(article["cover_src"])
            except Exception as exc:
                cover_error = str(exc)
                log.warning("Cover upload failed for %s: %s", article.get("article_id_full", ""), exc)
        payload = {
            "id": "0",
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
            "is_public": False,
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
            return {"cms_id": data["data"]["id"], "cover_image": cover_image, "cover_error": cover_error}
        raise RuntimeError(f"publish failed: {r.status_code} {data}")
