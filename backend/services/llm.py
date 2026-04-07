# -*- coding: utf-8 -*-
"""LLM service: AI-powered abstract generation."""

import logging
import re

import httpx

from services.database import ArticleDatabase

log = logging.getLogger("pipeline")

SYSTEM_PROMPT = (
    "你是深耕区块链与加密货币领域的专业摘要专家，针对我提供的原文，生成合规摘要。"
    "硬性要求：1. 字数严格控制在 40 字左右，误差不超过 3 个字；"
    "2. 精准提炼原文核心观点、关键事件/数据、核心结论，无信息偏差；"
    "3. 行业术语使用规范准确，语句通顺完整，不添加原文以外的任何信息，不做主观解读。"
)


def _extract_text(article: dict) -> str:
    """Concatenate non-image block texts as source material."""
    texts = [
        b.get("text", "").strip()
        for b in article.get("blocks", [])
        if b.get("type") != "img" and b.get("text")
    ]
    return "\n".join(texts)


def _naive_abstract(article: dict) -> str:
    """Fallback: simple truncation."""
    texts = [
        b.get("text", "").strip()
        for b in article.get("blocks", [])
        if b.get("type") != "img" and b.get("text")
    ]
    return re.sub(r"\s+", " ", " ".join(texts))[:180]


def generate_abstract(article: dict, db: ArticleDatabase) -> str:
    """Generate AI abstract for an article.

    Returns the AI-generated abstract (~40 chars), or falls back to
    naive truncation if LLM is not configured or the call fails.
    """
    aid = article.get("article_id", "unknown")

    api_url = (db.get_setting("llm_api_url") or "").strip()
    api_key = (db.get_setting("llm_api_key") or "").strip()
    model = (db.get_setting("llm_model") or "").strip()

    if not api_url or not api_key or not model:
        missing = []
        if not api_url:
            missing.append("api_url")
        if not api_key:
            missing.append("api_key")
        if not model:
            missing.append("model")
        log.warning("[LLM] 未配置 %s，跳过 AI 摘要生成，使用朴素截断: %s", "、".join(missing), aid)
        return _naive_abstract(article)

    # Normalize URL
    url = api_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    source_text = _extract_text(article)
    if not source_text.strip():
        log.warning("[LLM] 文章无文本内容，跳过 AI 摘要: %s", aid)
        return _naive_abstract(article)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": source_text},
        ],
        "max_tokens": 512,
        "temperature": 0.3,
        "thinking": {"type": "disabled"},  # 关闭思考模式，加快响应
    }
    headers = {
        "Authorization": f"Bearer {api_key[:6]}...{api_key[-4:]}",
        "Content-Type": "application/json",
    }

    log.info("[LLM] 开始生成摘要: aid=%s, model=%s, url=%s, 正文长度=%d字",
             aid, model, url, len(source_text))

    try:
        with httpx.Client(timeout=180.0) as client:  # 推理模型需要更长超时
            resp = client.post(url, json=payload, headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            })
        log.info("[LLM] API 响应: status=%d, aid=%s", resp.status_code, aid)
        resp.raise_for_status()
        body = resp.json()
        choices = body.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            # Try content first, then reasoning_content (for reasoning models like glm-4.7)
            abstract = msg.get("content", "").strip() or msg.get("reasoning_content", "").strip()
            if abstract:
                # For reasoning models, the reasoning content may include verbose thoughts
                # Extract only the final summary if it contains markers like "摘要：" or similar
                if "摘要" in abstract and "：" in abstract:
                    parts = abstract.split("：", 1)
                    if len(parts) > 1:
                        abstract = parts[1].strip()
                log.info("[LLM] 摘要生成成功: aid=%s, 字数=%d, 内容=%s",
                         aid, len(abstract), abstract)
                return abstract
        log.warning("[LLM] API 返回空内容: aid=%s, response=%s", aid, str(body)[:200])
    except httpx.HTTPStatusError as e:
        log.error("[LLM] API 请求失败: aid=%s, status=%d, body=%s",
                  aid, e.response.status_code, e.response.text[:200])
    except httpx.ConnectError:
        log.error("[LLM] 无法连接到 API: aid=%s, url=%s", aid, url)
    except httpx.TimeoutException:
        log.error("[LLM] API 请求超时: aid=%s", aid)
    except Exception as e:
        log.error("[LLM] 摘要生成异常: aid=%s, error=%s", aid, e)

    fallback = _naive_abstract(article)
    log.warning("[LLM] 回退到朴素截断: aid=%s, 截断摘要=%s", aid, fallback[:60])
    return fallback
