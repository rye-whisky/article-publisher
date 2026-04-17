# -*- coding: utf-8 -*-
"""Workflow filtering helpers: blocklist matching, tail cuts, title dedupe."""

from __future__ import annotations

import logging
import re
from typing import Optional

from services.database import ArticleDatabase

log = logging.getLogger("pipeline")


class FilterService:
    """Apply configurable title/content rules before scoring and publishing."""

    DEFAULT_RULES = [
        {"pattern": "早报", "match_type": "keyword", "field": "title", "action": "block", "source_key": "blockbeats", "notes": "过滤律动早报"},
        {"pattern": "加密早报", "match_type": "keyword", "field": "title", "action": "block", "source_key": "techflow", "notes": "过滤深潮早报"},
        {"pattern": "Space", "match_type": "keyword", "field": "title", "action": "block", "source_key": "techflow", "notes": "过滤 Space 回顾"},
        {"pattern": "Bitget", "match_type": "keyword", "field": "title", "action": "block", "source_key": "chaincatcher", "notes": "过滤链捕手 Bitget 内容"},
        {"pattern": "BYDFi", "match_type": "keyword", "field": "title", "action": "block", "notes": "过滤交易所品牌稿"},
        {"pattern": "赞助", "match_type": "keyword", "field": "title", "action": "block", "notes": "过滤赞助稿"},
        {"pattern": "广告", "match_type": "keyword", "field": "title", "action": "block", "notes": "过滤广告稿"},
        {"pattern": "欢迎加入深潮 TechFlow官方社群", "match_type": "keyword", "field": "content", "action": "tail_cut", "source_key": "techflow"},
        {"pattern": "Telegram订阅群", "match_type": "keyword", "field": "content", "action": "tail_cut"},
        {"pattern": "Twitter官方账号", "match_type": "keyword", "field": "content", "action": "tail_cut"},
        {"pattern": "欢迎加入律动 BlockBeats 官方社群", "match_type": "keyword", "field": "content", "action": "tail_cut", "source_key": "blockbeats"},
        {"pattern": "点击了解律动BlockBeats 在招岗位", "match_type": "keyword", "field": "content", "action": "tail_cut", "source_key": "blockbeats"},
    ]

    def __init__(self, database: ArticleDatabase):
        self.database = database

    def ensure_default_rules(self):
        """Seed a useful starter blocklist for fresh installs."""
        if self.database.list_blocklist_rules():
            return
        for rule in self.DEFAULT_RULES:
            self.database.create_blocklist_rule(rule)
        log.info("FilterService: seeded %d default blocklist rules", len(self.DEFAULT_RULES))

    def get_active_rules(self) -> list[dict]:
        self.ensure_default_rules()
        return self.database.list_blocklist_rules(active_only=True)

    @staticmethod
    def build_duplicate_key(title: str) -> str:
        """Normalize title for cross-source dedupe."""
        text = (title or "").strip().lower()
        if not text:
            return ""

        # Remove source suffixes often appended to titles.
        text = re.sub(r"\s*[-|｜]\s*(chaincatcher|blockbeats|techflow).*?$", "", text, flags=re.I)
        # Keep Chinese, letters and digits only.
        text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
        return text

    def check_title(self, source_key: str, title: str) -> Optional[dict]:
        """Return matched block rule info if the title should be dropped."""
        title = (title or "").strip()
        if not title:
            return None
        for rule in self.get_active_rules():
            if rule.get("field") != "title" or rule.get("action") != "block":
                continue
            if rule.get("source_key") and rule["source_key"] != source_key:
                continue
            if self._matches(rule, title):
                return {
                    "blocked": True,
                    "reason": f"title_block:{rule.get('pattern', '')}",
                    "rule": rule,
                }
        return None

    def check_duplicate(self, source_keys: list[str], title: str, exclude_article_id: str | None = None) -> tuple[str, Optional[dict]]:
        """Return normalized duplicate key and the already-stored duplicate article if found."""
        duplicate_key = self.build_duplicate_key(title)
        if not duplicate_key:
            return "", None
        existing = self.database.find_by_duplicate_key(
            duplicate_key,
            source_keys=source_keys,
            exclude_article_id=exclude_article_id,
        )
        return duplicate_key, existing

    def clean_article(self, article: dict) -> dict:
        """Apply content-level rules like tail cutting or whole-article content block."""
        blocks = self._normalize_blocks(list(article.get("blocks", [])))
        source_key = article.get("source_key", "")
        content_texts = [b.get("text", "") for b in blocks if b.get("type") != "img"]
        joined_text = "\n".join(content_texts)

        for rule in self.get_active_rules():
            if rule.get("field") != "content" or rule.get("action") != "block":
                continue
            if rule.get("source_key") and rule["source_key"] != source_key:
                continue
            if self._matches(rule, joined_text):
                article["filter_status"] = "blocked"
                article["filter_reason"] = f"content_block:{rule.get('pattern', '')}"
                return article

        article["blocks"] = self._apply_tail_cut_rules(source_key, blocks)
        article["filter_status"] = article.get("filter_status", "passed")
        return article

    @staticmethod
    def _normalize_blocks(blocks: list[dict]) -> list[dict]:
        """Normalize article body copy before downstream filtering/scoring."""
        normalized = []
        for block in blocks:
            item = dict(block)
            text = item.get("text")
            if isinstance(text, str) and text:
                item["text"] = text.replace("编者按", "导语")
            normalized.append(item)
        return normalized

    def _apply_tail_cut_rules(self, source_key: str, blocks: list[dict]) -> list[dict]:
        rules = [
            rule for rule in self.get_active_rules()
            if rule.get("field") == "content" and rule.get("action") == "tail_cut"
            and (not rule.get("source_key") or rule.get("source_key") == source_key)
        ]
        if not rules:
            return blocks

        cut_index = None
        for index, block in enumerate(blocks):
            text = block.get("text", "")
            if not text:
                continue
            for rule in rules:
                if self._matches(rule, text):
                    cut_index = index
                    break
            if cut_index is not None:
                break

        if cut_index is None:
            return blocks
        return blocks[:cut_index]

    @staticmethod
    def _matches(rule: dict, text: str) -> bool:
        pattern = rule.get("pattern", "")
        if not pattern or not text:
            return False

        match_type = rule.get("match_type", "keyword")
        if match_type == "regex":
            try:
                return re.search(pattern, text, flags=re.I) is not None
            except re.error:
                return False
        return pattern.lower() in text.lower()
