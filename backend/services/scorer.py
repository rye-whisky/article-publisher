# -*- coding: utf-8 -*-
"""AI scoring service for blockchain articles."""

from __future__ import annotations

import json
import logging
import re

from services.database import ArticleDatabase
from services.llm_service import LLMService

log = logging.getLogger("pipeline")

DEFAULT_SCORE_PROMPT = """你是区块链与加密行业的内容筛选编辑，请仅根据文章标题进行 0-100 分评分。

评分重点：
1. 信息增量：标题是否暗示新的事实、数据、观点或线索
2. 行业价值：是否值得进入后台候选池
3. 可发布性：是否像广告稿、活动回顾、品牌宣传、早报合集

请只返回 JSON：
{"score": 82, "reason": "一句中文说明", "tags": ["DeFi", "以太坊"]}
"""


class ScorerService:
    """Generate article scores and convert them into review decisions."""

    def __init__(self, database: ArticleDatabase):
        self.database = database

    def score_article(self, article: dict) -> dict:
        """Score a single article and compute the downstream decision."""
        prompt = self.database.get_setting("prompt_score") or DEFAULT_SCORE_PROMPT
        source_text = self._build_source_text(article)

        svc = LLMService(self.database)
        provider_configured = svc.get_provider("score") is not None
        response = None
        try:
            response = svc.chat(
                "score",
                prompt,
                source_text,
                max_tokens=10240,
                temperature=0.2,
            )
        except Exception as exc:
            log.warning("[Scorer] LLM score failed for %s: %s", article.get("article_id", ""), exc)

        parsed = self._parse_score_response(response)
        score = parsed["score"]
        if score is None:
            score = self._fallback_score(article)
            if not parsed["reason"]:
                if provider_configured:
                    parsed["reason"] = "评分模型未返回有效结果，已回退到人工审核默认分"
                else:
                    parsed["reason"] = "评分模型未配置，已回退到人工审核默认分"
            parsed["tags"] = parsed["tags"] or []

        score = max(0, min(100, int(score)))
        review_status, auto_publish_enabled = self.decide_review_status(article.get("source_key", ""), score)

        return {
            "score": score,
            "reason": parsed["reason"],
            "tags": parsed["tags"],
            "review_status": review_status,
            "auto_publish_enabled": auto_publish_enabled,
            "raw_response": response or "",
        }

    def decide_review_status(self, source_key: str, score: int) -> tuple[str, bool]:
        """Map score to the publishing lane.

        Returns (review_status, auto_publish_enabled) where auto_publish_enabled
        is encoded as: 0=off, 1=热文(75-84), 2=爆文(85+).

        Auto-publish is ONLY enabled for techflow and blockbeats sources with score >= 75.
        """
        # Get allowed auto-publish sources
        auto_sources = self._get_auto_sources()
        is_auto_source = source_key in auto_sources

        if score < 60:
            return "low_priority", False
        if score < 70:
            return "manual_review", False
        if score < 75:
            return "auto_candidate", False  # 存草稿，不自动发
        # Auto-publish only for techflow and blockbeats (75+ score)
        if not is_auto_source:
            return "auto_candidate", False  # 其他信源不自动发布，只存草稿
        if score < 85:
            return "auto_candidate", True   # 热文候选（仅 techflow/blockbeats）
        return "auto_candidate", True       # 爆文候选（仅 techflow/blockbeats）

    def _get_auto_sources(self) -> set[str]:
        raw = (self.database.get_setting("push_auto_sources") or "techflow,blockbeats").strip()
        if raw.startswith("["):
            try:
                return {str(item).strip() for item in json.loads(raw) if str(item).strip()}
            except json.JSONDecodeError:
                pass
        return {item.strip() for item in raw.split(",") if item.strip()}

    def _get_int_setting(self, key: str, default: int) -> int:
        raw = (self.database.get_setting(key) or "").strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _build_source_text(article: dict) -> str:
        """Build scoring input from title only."""
        return f"标题：{article.get('title', '')}\n来源：{article.get('source', '')}"

    @staticmethod
    def _parse_score_response(response: str | None) -> dict:
        if not response:
            return {"score": None, "reason": "", "tags": []}

        content = response.strip()
        match = re.search(r"\{.*\}", content, flags=re.S)
        if match:
            content = match.group(0)
        try:
            data = json.loads(content)
            tags = data.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            return {
                "score": data.get("score"),
                "reason": str(data.get("reason", ""))[:300],
                "tags": [str(tag) for tag in tags[:8]],
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        score_match = re.search(r"(\d{2,3})", content)
        return {
            "score": int(score_match.group(1)) if score_match else None,
            "reason": content[:300],
            "tags": [],
        }

    @staticmethod
    def _fallback_score(article: dict) -> int:
        """Very conservative fallback when no score model is configured."""
        text_len = sum(len(block.get("text", "")) for block in article.get("blocks", []))
        if text_len > 3000:
            return 68
        if text_len > 1500:
            return 64
        return 58
