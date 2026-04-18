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
        {"pattern": "点击了解律动 BlockBeats 在招岗位", "match_type": "keyword", "field": "content", "action": "tail_cut", "source_key": "blockbeats"},
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

    def check_duplicate(
        self,
        source_keys: list[str],
        title: str,
        exclude_article_id: str | None = None,
    ) -> tuple[str, Optional[dict]]:
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

        blocks = self._apply_tail_cut_rules(source_key, blocks)
        blocks = self._clean_preamble(blocks)
        article["blocks"] = blocks
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

    @staticmethod
    def _clean_preamble(blocks: list[dict]) -> list[dict]:
        """Remove obvious preamble noise while preserving the real opening paragraph."""
        if not blocks:
            return blocks

        leading_entries: list[tuple[int, dict, str]] = []
        for index, block in enumerate(blocks):
            if block.get("type") == "img":
                break
            text = (block.get("text") or "").strip()
            if not text:
                continue
            leading_entries.append((index, block, text))
            if len(leading_entries) >= 6:
                break

        if not leading_entries:
            return blocks

        remove_indices: set[int] = set()
        move_to_end: list[tuple[int, dict]] = []
        moved_indices: set[int] = set()

        for index, _, text in leading_entries[:4]:
            if FilterService._is_pull_quote(text):
                remove_indices.add(index)

        first_content_position = None
        for position, (index, _, text) in enumerate(leading_entries):
            if index in remove_indices:
                continue
            if FilterService._is_attribution_line(text):
                continue
            first_content_position = position
            break

        if first_content_position is not None:
            first_index, _, first_text = leading_entries[first_content_position]
            next_entry_is_attribution = False
            for _, _, next_text in leading_entries[first_content_position + 1:first_content_position + 3]:
                if FilterService._is_pull_quote(next_text):
                    continue
                next_entry_is_attribution = FilterService._is_attribution_line(next_text)
                break
            if next_entry_is_attribution and FilterService._is_leading_teaser_line(first_text):
                remove_indices.add(first_index)

        for index, block, text in leading_entries[:4]:
            if index in remove_indices:
                continue
            if not FilterService._is_attribution_line(text):
                continue
            remove_indices.add(index)
            if index not in moved_indices:
                move_to_end.append((index, block))
                moved_indices.add(index)

        if not remove_indices:
            return blocks

        remaining = [block for idx, block in enumerate(blocks) if idx not in remove_indices]
        if move_to_end:
            if remaining and remaining[-1].get("type") != "img":
                remaining.append({"type": "p", "text": ""})
            remaining.extend(block for _, block in move_to_end)
        return remaining

    @staticmethod
    def _is_pull_quote(text: str) -> bool:
        normalized = (text or "").strip()
        return bool(normalized and re.fullmatch(r'[\u201c"].+[\u201d"]', normalized))

    @staticmethod
    def _is_attribution_line(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if not normalized:
            return False
        return re.match(
            r"^(?:作者|原文作者|撰文|编译|原文编译|译者|编辑|编辑整理|文字整理|文稿整理|来源|原标题|播出日期|嘉宾|主持人|采访|整理)\s*[:：]",
            normalized,
            flags=re.IGNORECASE,
        ) is not None

    @staticmethod
    def _is_leading_teaser_line(text: str) -> bool:
        """Detect a very short teaser line before attribution metadata."""
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if not normalized:
            return False
        if len(normalized) < 8 or len(normalized) > 28:
            return False
        if not re.search(r"[。！？?!]$", normalized):
            return False
        if FilterService._is_attribution_line(normalized):
            return False
        if re.search(r"[:：]|https?://|www\.", normalized):
            return False
        if re.search(r"\d", normalized):
            return False
        if re.match(
            r"^(?:过去|目前|今日|今天|近日|近期|首先|随着|根据|由于|自|从|在|当|对于|如果|今年|本周)",
            normalized,
        ):
            return False
        return len(re.findall(r"[\u4e00-\u9fff]", normalized)) >= 8
