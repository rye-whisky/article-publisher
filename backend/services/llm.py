# -*- coding: utf-8 -*-
"""LLM tasks: AI-powered abstract generation and article editing."""

import json
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
        # Try to get custom prompt from database
        custom_prompt = db.get_setting("prompt_abstract") or ABSTRACT_SYSTEM_PROMPT
        abstract = svc.chat("abstract", custom_prompt, source_text,
                            max_tokens=512, temperature=0.3)
        if abstract:
            # Extract final summary from reasoning models
            if "摘要" in abstract and "：" in abstract:
                parts = abstract.split("：", 1)
                if len(parts) > 1:
                    abstract = parts[1].strip()
            # Clean: strip markdown bold/italic markers, normalize quotes
            abstract = re.sub(r'\*+', '', abstract)
            abstract = abstract.replace('"', '\u201c').replace('"', '\u201d')
            abstract = abstract.strip()
            log.info("[LLM] 摘要生成成功: aid=%s, 字数=%d", aid, len(abstract))
            return abstract
    except Exception as e:
        log.error("[LLM] 摘要生成异常: aid=%s, error=%s", aid, e)

    fallback = _naive_abstract(article)
    log.warning("[LLM] 回退到朴素截断: aid=%s, 截断摘要=%s", aid, fallback[:60])
    return fallback


def semantic_dedup(title: str, recent_titles: list[str], db: ArticleDatabase) -> bool:
    """Use LLM to check if *title* is semantically duplicate of any *recent_titles*.

    Returns True if the title is considered a duplicate.
    """
    if not recent_titles:
        return False

    titles_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(recent_titles))
    prompt = (
        "你是一个新闻去重判断助手。判断以下新标题是否与已有标题列表中的任何一篇语义重复"
        "（指同一事件、同一话题，不只是关键词相似）。\n\n"
        f"已有标题：\n{titles_list}\n\n"
        f"新标题：{title}\n\n"
        "请只回答 JSON：{\"duplicate\": true} 或 {\"duplicate\": false}"
    )

    try:
        svc = _get_llm_service(db)
        # Use score task's LLM for dedup
        response = svc.chat("score", prompt, title, max_tokens=256, temperature=0.1)
        if response:
            import re as _re
            match = _re.search(r'\{[^}]+\}', response)
            if match:
                data = json.loads(match.group(0))
                return bool(data.get("duplicate", False))
    except Exception as e:
        log.warning("[LLM] semantic_dedup failed, assuming not duplicate: %s", e)

    return False


# ───────────────────────── Article editing ─────────────────────────

EDIT_SYSTEM_PROMPT = """你是专业的区块链与加密货币领域内容编辑。对用户提供的文章正文进行编辑润色。

【重要】输出格式要求：
- 必须输出纯 HTML 格式，不能使用 Markdown 语法
- 保留所有 HTML 标签：<h2>、<h3>、<h4>、<p>、<strong>、<em>、<a> 等
- 禁止使用 Markdown 标记：### 标题、**粗体**、*斜体*、[链接](url) 等
- 输出应该是可以直接嵌入网页的 HTML 代码

编辑规则：
1. 修正语法和拼写错误
2. 优化段落结构和语句流畅度，使表达更专业
3. 保持原文意思和信息完全不变
4. 不添加任何原文没有的内容，不删减任何信息
5. 仅返回编辑后的 HTML 代码，不要添加任何解释或前言

示例：
输入: <h2>预测市场</h2><p>预测市场是<strong>未来</strong>的方向。</p>
输出: <h2>预测市场的发展前景</h2><p>预测市场代表了金融科技发展的<strong>重要趋势</strong>。</p>"""


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
    # Try custom prompt first, then database setting, then default
    if not custom_prompt:
        custom_prompt = db.get_setting("prompt_edit")
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

    # Try system prompt first, then database setting, then default
    if not system_prompt:
        system_prompt = db.get_setting("prompt_edit")
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
