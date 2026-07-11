# -*- coding: utf-8 -*-
"""发布前标题路由与标签注入规则。"""

from __future__ import annotations

from typing import Iterable

AI_EVENT_TAG = "AI大事件"
AI_EVENT_USER_ID = "1932036327095366202"

RWA_TAG = "RWA"
RWA_USER_ID = "1932036327095366417"

PREDICTION_MARKET_USER_ID = "1932036327095366235"
REGULATION_USER_ID = "2"

SECURITY_USER_ID = "1932036327095362909"

AI_EVENT_KEYWORDS = (
    "ai", "openai", "gpt", "anthropic", "claude", "google", "gemini",
    "deepmind", "xai", "特斯拉", "微软", "英伟达", "amd", "百度", "字节", "阿里",
    "人工智能", "openclaw", "agent", "hermes",
)
RWA_KEYWORDS = ("代币化", "ondo", "币股")
PREDICTION_MARKET_KEYWORDS = ("polymarket", "预测市场", "kalshi")
REGULATION_KEYWORDS = ("监管", "美联储", "sec", "cftc", "众议院", "参议院", "香港", "税", "法案", "白宫")
SECURITY_KEYWORDS = ("certic", "certik", "慢雾", "黑客", "漏洞", "暴雷", "危机")


def _normalized_title(title: str) -> str:
    """统一转小写，便于标题子串匹配。"""
    return (title or "").strip().lower()


def _merge_tags(tags: Iterable[str] | None, *extra_tags: str) -> list[str]:
    """合并标签并去重，保持原有顺序。"""
    merged: list[str] = []
    for tag in list(tags or []) + list(extra_tags):
        text = str(tag).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _merge_strong_content_tags(
    strong_content_tags: dict | None,
    bucket: str,
    *extra_tags: str,
) -> dict:
    """合并 ChainThink 的 strong_content_tags，保留已有分组。"""
    merged: dict[str, list[str]] = {}
    for key, values in dict(strong_content_tags or {}).items():
        merged[str(key)] = _merge_tags(values)
    merged[bucket] = _merge_tags(merged.get(bucket), *extra_tags)
    return merged


def _contains_any(title: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in title for keyword in keywords)


def _contains_all(title: str, keywords: tuple[str, ...]) -> bool:
    return all(keyword in title for keyword in keywords)


def title_matches_ai_event(title: str) -> bool:
    """判断标题是否命中 AI 大事件关键词。"""
    text = _normalized_title(title)
    return bool(text) and _contains_any(text, AI_EVENT_KEYWORDS)


def merge_ai_event_tags(tags: Iterable[str] | None, title: str) -> list[str]:
    """命中 AI 大事件时补齐标签，最多保留 5 个。"""
    merged = _merge_tags(tags)
    if not title_matches_ai_event(title):
        return merged
    if AI_EVENT_TAG in merged:
        return merged
    if len(merged) >= 5:
        merged[-1] = AI_EVENT_TAG
    else:
        merged.append(AI_EVENT_TAG)
    return merged


def apply_before_publish_rules(article: dict, strategy: str = "") -> tuple[dict, str | None]:
    """在提交到 CMS 前，按标题路由专栏并注入需要的标签。"""
    routed = dict(article)
    title = _normalized_title(routed.get("title", ""))
    strategy_key = (strategy or "").strip().lower()

    if strategy_key == "daily_report":
        return routed, None

    # 业务专栏优先级高于 AI 大事件路由。
    if _contains_any(title, RWA_KEYWORDS) or _contains_all(title, ("binance", "美股")):
        routed["tags"] = _merge_tags(routed.get("tags"), RWA_TAG)
        routed["strong_content_tags"] = _merge_strong_content_tags(
            routed.get("strong_content_tags"),
            "人工",
            RWA_TAG,
        )
        routed["user_id"] = RWA_USER_ID
        routed["as_user_id"] = RWA_USER_ID
        return routed, "rwa"

    if _contains_any(title, PREDICTION_MARKET_KEYWORDS):
        routed["user_id"] = PREDICTION_MARKET_USER_ID
        routed["as_user_id"] = PREDICTION_MARKET_USER_ID
        return routed, "prediction_market"

    if _contains_any(title, REGULATION_KEYWORDS):
        routed["user_id"] = REGULATION_USER_ID
        routed["as_user_id"] = REGULATION_USER_ID
        return routed, "regulation"

    if _contains_any(title, SECURITY_KEYWORDS):
        routed["user_id"] = SECURITY_USER_ID
        routed["as_user_id"] = SECURITY_USER_ID
        return routed, "security"

    if title_matches_ai_event(title):
        routed["tags"] = merge_ai_event_tags(routed.get("tags"), title)
        routed["strong_content_tags"] = _merge_strong_content_tags(
            routed.get("strong_content_tags"),
            "人工",
            AI_EVENT_TAG,
        )
        routed["user_id"] = AI_EVENT_USER_ID
        routed["as_user_id"] = AI_EVENT_USER_ID
        return routed, "ai_event"

    return routed, None
