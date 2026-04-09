# -*- coding: utf-8 -*-
"""LLM tasks: AI-powered abstract generation and article editing."""

import logging
import re
from typing import Optional

from services.database import ArticleDatabase

log = logging.getLogger("pipeline")

# ───────────────────────── Shared helpers ─────────────────────────

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


def _get_llm_service(db: ArticleDatabase):
    """Lazy-import and instantiate LLMService."""
    from services.llm_service import LLMService
    return LLMService(db)


# ───────────────────────── Abstract generation ─────────────────────────

ABSTRACT_SYSTEM_PROMPT = (
    "你是深耕区块链与加密货币领域的专业摘要专家，针对我提供的原文，生成合规摘要。"
    "硬性要求：1. 字数严格控制在 40 字左右，误差不超过 3 个字；"
    "2. 精准提炼原文核心观点、关键事件/数据、核心结论，无信息偏差；"
    "3. 行业术语使用规范准确，语句通顺完整，不添加原文以外的任何信息，不做主观解读。"
)


def generate_abstract(article: dict, db: ArticleDatabase) -> str:
    """Generate AI abstract for an article.

    Returns the AI-generated abstract (~40 chars), or falls back to
    naive truncation if LLM is not configured or the call fails.
    """
    aid = article.get("article_id", "unknown")
    source_text = _extract_text(article)

    if not source_text.strip():
        log.warning("[LLM] 文章无文本内容，跳过 AI 摘要: %s", aid)
        return _naive_abstract(article)

    try:
        svc = _get_llm_service(db)
        abstract = svc.chat("abstract", ABSTRACT_SYSTEM_PROMPT, source_text,
                            max_tokens=512, temperature=0.3)
        if abstract:
            # Extract final summary from reasoning models
            if "摘要" in abstract and "：" in abstract:
                parts = abstract.split("：", 1)
                if len(parts) > 1:
                    abstract = parts[1].strip()
            log.info("[LLM] 摘要生成成功: aid=%s, 字数=%d", aid, len(abstract))
            return abstract
    except Exception as e:
        log.error("[LLM] 摘要生成异常: aid=%s, error=%s", aid, e)

    fallback = _naive_abstract(article)
    log.warning("[LLM] 回退到朴素截断: aid=%s, 截断摘要=%s", aid, fallback[:60])
    return fallback


# ───────────────────────── Article editing ─────────────────────────

EDIT_SYSTEM_PROMPT = (
    "你是专业的区块链与加密货币领域编辑。对用户提供的文章正文进行编辑润色：\n"
    "1. 修正语法和拼写错误\n"
    "2. 优化段落结构和语句流畅度\n"
    "3. 保持原文意思和信息完全不变\n"
    "4. 保留原文 HTML 标签结构（如 <h2>、<p>、<strong> 等）\n"
    "5. 不添加任何原文没有的内容，不删减任何信息\n"
    "6. 仅返回编辑后的正文 HTML，不要添加解释说明\n"
)


def edit_article(article: dict, db: ArticleDatabase,
                 custom_prompt: str = None) -> Optional[dict]:
    """Use AI to edit/polish an article's body text.

    Returns the edited article dict with updated blocks, or None on failure.
    """
    aid = article.get("article_id", "unknown")

    # Collect text blocks (non-image)
    text_blocks = []
    for b in article.get("blocks", []):
        if b.get("type") != "img" and b.get("text"):
            tag = b.get("tag", "p")
            text = b.get("text", "").strip()
            if text:
                text_blocks.append(f"<{tag}>{text}</{tag}>")

    if not text_blocks:
        log.warning("[LLM] 文章无文本内容，跳过 AI 编辑: %s", aid)
        return None

    source_html = "\n".join(text_blocks)
    system_prompt = custom_prompt or EDIT_SYSTEM_PROMPT

    try:
        svc = _get_llm_service(db)
        edited = svc.chat("edit", system_prompt, source_html,
                          max_tokens=8192, temperature=0.3)
        if not edited:
            log.warning("[LLM] AI 编辑返回空: aid=%s", aid)
            return None

        log.info("[LLM] AI 编辑成功: aid=%s, 原文长度=%d, 编辑后长度=%d",
                 aid, len(source_html), len(edited))
        return _parse_edited_blocks(edited, article)
    except Exception as e:
        log.error("[LLM] AI 编辑异常: aid=%s, error=%s", aid, e)
        return None


def ai_edit_text(body_text: str, db: ArticleDatabase,
                 system_prompt: str = None) -> Optional[str]:
    """AI-edit raw body text (plain text or HTML). Returns edited text or None.

    Used by the API endpoint for the editor's AI edit panel.
    """
    if not body_text or not body_text.strip():
        return None

    prompt = system_prompt or EDIT_SYSTEM_PROMPT
    try:
        svc = _get_llm_service(db)
        edited = svc.chat("edit", prompt, body_text,
                          max_tokens=8192, temperature=0.3)
        if edited:
            log.info("[LLM] AI 文本编辑成功: 原文长度=%d, 编辑后长度=%d",
                     len(body_text), len(edited))
        return edited
    except Exception as e:
        log.error("[LLM] AI 文本编辑异常: error=%s", e)
        return None


def _parse_edited_blocks(edited_html: str, original: dict) -> dict:
    """Parse edited HTML back into blocks format, preserving images."""
    from lxml import html as lxml_html

    # Build a new article dict based on the original
    result = {k: v for k, v in original.items()}
    new_blocks = []

    # Collect original image blocks (preserve their position)
    img_blocks = [b for b in original.get("blocks", []) if b.get("type") == "img"]

    # Parse edited HTML into blocks
    try:
        frag = lxml_html.fragment_fromstring(edited_html, create_parent="div")
        img_idx = 0
        for el in frag:
            tag = el.tag if isinstance(el.tag, str) else "p"
            text = (el.text_content() or "").strip()
            if not text:
                continue
            # Check if this position should have an image (heuristic: match tag order)
            # Insert image blocks that appeared before this text block in original
            while img_idx < len(img_blocks):
                new_blocks.append(img_blocks[img_idx])
                img_idx += 1
            new_blocks.append({"type": tag, "tag": tag, "text": text})
        # Append remaining images
        while img_idx < len(img_blocks):
            new_blocks.append(img_blocks[img_idx])
            img_idx += 1
    except Exception as e:
        log.warning("[LLM] 解析编辑后 HTML 失败，返回原文: %s", e)
        return original

    result["blocks"] = new_blocks
    return result
