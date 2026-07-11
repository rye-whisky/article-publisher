# -*- coding: utf-8 -*-
"""LLM tasks: AI-powered abstract generation and article editing."""

import json
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

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

REFUSAL_ABSTRACT_KEYWORDS = (
    # Chinese refusal-style outputs
    "无法回答",
    "不能回答",
    "无法提供",
    "无法给到",
    "无法协助",
    "无法帮助",
    "不能帮助",
    "抱歉",
    "很遗憾",
    "未找到相关",
    "没有找到相关",
    "无法识别",
    "不便回答",
    "不予回答",
    "内容不予展示",
    "由于安全原因",
    "违反政策",
    "请求被拒绝",
    "请提供更多信息",
    "无法处理该请求",
    # English refusal-style outputs
    "i can't",
    "i cannot",
    "i'm sorry",
    "sorry",
    "cannot assist",
    "can't assist",
    "not able to help",
    "unable to provide",
    "content policy",
    "safety policy",
    "request cannot be fulfilled",
    "no relevant result",
)


def detect_refusal_abstract(text: str) -> tuple[bool, str]:
    """Detect refusal-style summary text and return (is_refusal, matched_keyword)."""
    if not text:
        return False, ""
    normalized = " ".join(str(text).lower().split())
    for keyword in REFUSAL_ABSTRACT_KEYWORDS:
        if keyword.lower() in normalized:
            return True, keyword
    return False, ""


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


def _parse_website_article_time(raw: str) -> datetime | None:
    """Parse common ChainThink article time strings."""
    raw = (raw or "").strip()
    if not raw:
        return None

    text = re.sub(r"\s+", " ", raw)
    text = text.replace("年", "-").replace("月", "-").replace("日", " ").strip()
    text = text.replace("/", "-")
    text = re.sub(r"(\d{4}-\d{1,2}-\d{1,2})T", r"\1 ", text)
    text = re.sub(r"([+-]\d{2}:?\d{2}|Z)$", "", text).strip()

    # Try full date format first: YYYY-MM-DD HH:MM:SS
    match = re.search(r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
    if match:
        value = match.group(0)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue

    # Try MM-DD HH:MM format (without year), use current year
    match = re.search(r"(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        hour, minute = 0, 0
        if match.group(3):
            hour, minute = int(match.group(3)), int(match.group(4))
        try:
            current_year = datetime.now().year
            return datetime(current_year, month, day, hour, minute)
        except ValueError:
            pass

    return None


def _extract_website_time(container) -> tuple[str, datetime | None]:
    candidates: list[str] = []

    if not container:
        return "", None

    # Method 1: Try old <time> tag format
    time_tag = container.find("time")
    if time_tag:
        candidates.extend([
            time_tag.get("datetime", ""),
            time_tag.get("title", ""),
            time_tag.get_text(" ", strip=True),
        ])

    # Method 2: Look for <p> tags with time-like text (new Next.js format)
    # Format: <p class="text-[12px]">04-30 16:36</p>
    for p in container.find_all("p"):
        p_text = p.get_text(" ", strip=True)
        # Match MM-DD HH:MM format
        if re.match(r"^\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}$", p_text):
            candidates.append(p_text)

    # Method 3: Scan all text for date patterns
    text = container.get_text(" ", strip=True)
    candidates.extend(re.findall(r"\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?\s*\d{1,2}:\d{2}(?::\d{2})?", text))
    candidates.extend(re.findall(r"\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}", text))

    for raw in candidates:
        parsed = _parse_website_article_time(raw)
        if parsed:
            return raw.strip(), parsed
    return "", None


def _extract_website_article_url(h3, base_url: str) -> str:
    link = h3.find_parent("a", href=True)
    if not link:
        parent = h3.find_parent()
        for _ in range(4):
            if not parent:
                break
            link = parent.find("a", href=True)
            if link and link.get("href"):
                break
            parent = parent.find_parent()

    href = link.get("href", "") if link else ""
    if not href:
        return ""
    return urljoin(base_url, href)


def fetch_website_articles(limit: int = 10, url: str = "https://chainthink.cn/zh-CN/article") -> list[dict]:
    """Fetch recent published article metadata from chainthink.cn website."""
    import requests
    from bs4 import BeautifulSoup

    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        articles = []
        seen_titles = set()
        for h3 in soup.find_all("h3"):
            title = h3.get_text(strip=True)
            if not title or len(title) <= 5 or title in seen_titles:
                continue

            # Find container that has both title and time
            # Walk up from h3 until we find a container with a time element
            container = None
            current = h3
            for _ in range(6):
                current = current.find_parent()
                if not current:
                    break
                # Check if this container has a time element
                raw_time, parsed_time = _extract_website_time(current)
                if raw_time:
                    container = current
                    break

            # Fallback: use closest parent if no time found
            if not container:
                container = h3.find_parent("article") or h3.find_parent("li") or h3.find_parent("div") or h3

            raw_time, parsed_time = _extract_website_time(container)
            articles.append({
                "title": title,
                "url": _extract_website_article_url(h3, url),
                "published_at": parsed_time.isoformat() if parsed_time else "",
                "raw_time": raw_time,
            })
            seen_titles.add(title)
            if len(articles) >= limit:
                break

        log.info("[LLM] Fetched %d articles from chainthink.cn", len(articles))
        return articles
    except Exception as e:
        log.warning("[LLM] Failed to fetch website articles: %s", e)
        return []


def fetch_website_titles(limit: int = 6) -> list[str]:
    """Fetch recent published article titles from chainthink.cn website.

    Parses the HTML article list page to extract titles from h3 tags.
    """
    articles = fetch_website_articles(limit=limit)
    titles = [item.get("title", "") for item in articles if item.get("title")]
    if titles:
        return titles

    import requests
    from bs4 import BeautifulSoup

    url = "https://chainthink.cn/zh-CN/article"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        titles = []
        for h3 in soup.find_all('h3'):
            title = h3.get_text(strip=True)
            if title and len(title) > 5:
                titles.append(title)
                if len(titles) >= limit:
                    break

        log.info("[LLM] Fetched %d titles from chainthink.cn", len(titles))
        return titles
    except Exception as e:
        log.warning("[LLM] Failed to fetch website titles: %s", e)
        return []


def semantic_dedup(
    title: str,
    recent_titles: list[str],
    db: ArticleDatabase,
    website_titles: list[str] | None = None,
    abstract: str | None = None,
    candidate_articles: list[dict] | None = None,
) -> bool:
    """Use LLM to check if *title* is semantically duplicate of any titles.

    Args:
        title: New article title to check
        recent_titles: Recently published/broadcast titles from local DB
        db: ArticleDatabase instance
        website_titles: Optional titles from chainthink.cn website
        abstract: Optional abstract of the new article for content comparison
        candidate_articles: Optional list of dicts with 'title' and 'abstract'
            for deeper content comparison. When provided, takes priority over
            title-only comparison.

    Returns True if the title is considered a duplicate.
    """
    # Enhanced path: compare with both title and abstract
    if candidate_articles:
        return _semantic_dedup_with_content(title, abstract, candidate_articles, db)

    # Legacy path: title-only comparison
    all_titles = list(recent_titles)
    if website_titles:
        all_titles.extend(website_titles)

    if not all_titles:
        return False

    # Limit to recent 12 titles total (6 local + 6 website)
    all_titles = all_titles[:12]

    titles_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(all_titles))
    prompt = (
        "你是一个新闻去重判断助手。判断以下新标题是否与已有标题列表中的任何一篇是"
        "「同一篇报道的不同来源转载」或「高度雷同的改写」。\n"
        "注意：只是同一话题/事件但角度、内容不同的文章不算重复，"
        "不同媒体对同一事件的独立报道也不算重复。\n\n"
        f"已有标题：\n{titles_list}\n\n"
        f"新标题：{title}\n\n"
        "请只回答 JSON：{\"duplicate\": true} 或 {\"duplicate\": false}"
    )

    try:
        svc = _get_llm_service(db)
        response = svc.chat("score", prompt, title, max_tokens=256, temperature=0.1)
        if response:
            import re as _re
            match = _re.search(r'\{[^}]+\}', response)
            if match:
                data = json.loads(match.group(0))
                is_dup = bool(data.get("duplicate", False))
                if is_dup:
                    log.info("[LLM] semantic dedup: '%s' is duplicate of website titles", title[:50])
                return is_dup
    except Exception as e:
        log.warning("[LLM] semantic_dedup failed, assuming not duplicate: %s", e)

    return False


def _semantic_dedup_with_content(
    title: str,
    abstract: str | None,
    candidate_articles: list[dict],
    db: ArticleDatabase,
) -> bool:
    """Compare article title+abstract against candidates using LLM."""
    # Limit to top 6 candidates
    candidates = candidate_articles[:6]
    if not candidates:
        return False

    candidates_text = ""
    for i, c in enumerate(candidates, 1):
        c_title = c.get("title", "")
        c_abstract = c.get("abstract", "") or ""
        c_abstract_short = c_abstract[:150] if c_abstract else ""
        overlap_info = ""
        if c.get("overlap_keywords"):
            overlap_info = f"  [重叠关键字: {', '.join(c['overlap_keywords'])}]"
        candidates_text += f"{i}. 标题：{c_title}"
        if c_abstract_short:
            candidates_text += f"\n   摘要：{c_abstract_short}"
        if overlap_info:
            candidates_text += f"\n   {overlap_info}"
        candidates_text += "\n"

    new_article_text = f"新标题：{title}"
    if abstract:
        new_article_text += f"\n新摘要：{abstract[:150]}"

    prompt = (
        "你是一个新闻去重判断助手。判断以下新文章是否与已有文章中的任何一篇是"
        "「同一篇报道的不同来源转载」或「高度雷同的改写」。\n"
        "判断标准：\n"
        "- 两篇标题不同但描述的是完全相同的事件且核心内容一致 → 重复\n"
        "- 同一事件的转载、翻译、改写（仅换词不换意） → 重复\n"
        "- 同一话题/事件但角度不同、内容有实质差异的独立报道 → 不重复\n"
        "- 同一事件的不同阶段进展 → 不重复\n\n"
        f"已有文章：\n{candidates_text}\n"
        f"{new_article_text}\n\n"
        "请只回答 JSON：{\"duplicate\": true, \"reason\": \"<简短说明>\"} 或 {\"duplicate\": false, \"reason\": \"<简短说明>\"}"
    )

    try:
        svc = _get_llm_service(db)
        response = svc.chat("score", prompt, title, max_tokens=256, temperature=0.1)
        if response:
            import re as _re
            match = _re.search(r'\{[^}]+\}', response)
            if match:
                data = json.loads(match.group(0))
                is_dup = bool(data.get("duplicate", False))
                reason = data.get("reason", "")
                if is_dup:
                    log.info("[LLM] semantic dedup (content): '%s' is duplicate — %s", title[:50], reason)
                else:
                    log.info("[LLM] semantic dedup (content): '%s' is NOT duplicate — %s", title[:50], reason)
                return is_dup
    except Exception as e:
        log.warning("[LLM] semantic_dedup_with_content failed, assuming not duplicate: %s", e)

    return False


# ───────────────────────── Author info extraction ─────────────────────────

AUTHOR_INFO_SYSTEM_PROMPT = """你是一个文章内容分析助手。我会给你文章头部的内容片段，你需要：

1. 识别属于"作者/编辑/译者信息"的内容行
2. 这些信息可能包含：原文作者、作者、撰文、编译者、译者、编辑等关键词
3. 提取这些信息行的完整文本
4. 返回 JSON 格式结果

【输出格式】
只返回纯 JSON，不要有任何其他文字：
{
  "to_remove": ["要移除的行1", "要移除的行2"],
  "author_info": ["整理后的作者信息1", "整理后的作者信息2"]
}

【规则】
- 如果没有找到作者/编辑信息，返回 {"to_remove": [], "author_info": []}
- author_info 中保留原始信息，但要去除 markdown 格式（如 _ * 等）
- 只处理明确属于作者信息的内容，不要误删正文
"""

AUTHOR_INFO_FALLBACK_PATTERNS = [
    r'^_?原文作者[：:]\s*(.+?)_?$',
    r'^_?原文编译[：:]\s*(.+?)_?$',
    r'^_?作者[：:]\s*(.+?)_?$',
    r'^_?撰文[：:]\s*(.+?)_?$',
    r'^_?编译[：:]\s*(.+?)_?$',
    r'^_?译者[：:]\s*(.+?)_?$',
    r'^_?编辑[：:]\s*(.+?)_?$',
]


def extract_author_info(article: dict, db: ArticleDatabase,
                        use_llm: bool = True) -> dict:
    """识别并提取文章头部的作者/编辑信息，移到文章末尾。

    Args:
        article: 文章 dict，包含 blocks
        db: 数据库实例
        use_llm: 是否使用 LLM（False 则使用正则表达式回退方案）

    Returns:
        更新后的 article dict
    """
    blocks = article.get("blocks", [])
    if not blocks:
        return article

    # 检查文章头部（前 15 个非图片 block）
    header_blocks = []
    header_indices = []
    for i, block in enumerate(blocks):
        if block.get("type") == "img":
            continue
        text = block.get("text", "").strip()
        if text:
            header_blocks.append(text)
            header_indices.append(i)
        if len(header_blocks) >= 15:
            break

    if not header_blocks:
        return article

    to_remove = set()
    author_info = []

    if use_llm and db is not None:
        # 使用 LLM 识别
        try:
            svc = _get_llm_service(db)
            header_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(header_blocks))
            response = svc.chat(
                "abstract",  # 使用 abstract 任务的模型配置
                AUTHOR_INFO_SYSTEM_PROMPT,
                header_text,
                max_tokens=512,
                temperature=0.1
            )

            if response:
                # 提取 JSON
                match = re.search(r'\{[\s\S]*\}', response)
                if match:
                    data = json.loads(match.group(0))
                    to_remove = set(data.get("to_remove", []))
                    author_info = data.get("author_info", [])

                    # 映射回原始索引
                    indices_to_remove = set()
                    for remove_text in to_remove:
                        # 去除可能的编号前缀
                        clean_text = re.sub(r'^\d+\.\s*', '', remove_text).strip()
                        for idx, original_text in enumerate(header_blocks):
                            if clean_text in original_text or original_text in clean_text:
                                indices_to_remove.add(header_indices[idx])
                                break
                    to_remove = indices_to_remove

                    log.info("[LLM] 作者信息识别成功: 移除 %d 行, 提取 %d 条信息",
                             len(to_remove), len(author_info))
        except Exception as e:
            log.warning("[LLM] 作者信息识别失败，使用回退方案: %s", e)

    # 如果 LLM 失败或未启用，使用正则表达式回退
    if not to_remove and not author_info:
        for i, idx in enumerate(header_indices):
            text = header_blocks[i]
            for pattern in AUTHOR_INFO_FALLBACK_PATTERNS:
                match = re.match(pattern, text, re.IGNORECASE)
                if match:
                    to_remove.add(idx)
                    # 提取信息
                    info_text = re.sub(r'^[_\*]+|[_\*]+$', '', match.group(0)).strip()
                    # 标准化冒号
                    info_text = re.sub(r'[：:]', '：', info_text)
                    author_info.append(info_text)
                    break

    if to_remove or author_info:
        # 构建新的 blocks
        new_blocks = []
        for i, block in enumerate(blocks):
            if i in to_remove:
                continue
            new_blocks.append(block)

        # 在文章末尾添加作者信息
        if author_info:
            new_blocks.append({"type": "p", "text": ""})  # 空行分隔
            for info in author_info:
                new_blocks.append({"type": "p", "text": info})

        result = {k: v for k, v in article.items()}
        result["blocks"] = new_blocks
        return result

    return article


# ───────────────────────── Article optimization workflow ─────────────────────────

OPTIMIZE_SYSTEM_PROMPT = """你是专业的区块链与加密货币领域内容编辑。对文章进行发布前的优化处理。

【任务】
1. 识别并提取文章头部的作者、编者、译者、编译者等信息
2. 将这些信息移到文章末尾
3. 将"编者按"统一替换为"导语"
4. 保持原文意思和信息完全不变

【输出格式】
返回 JSON 格式：
{
  "blocks": ["block1内容", "block2内容", ...],
  "author_info": ["作者信息1", "作者信息2"]
}

【规则】
- 只处理明确属于作者/编者/译者信息的内容
- 保留原文的所有 HTML 标签
- 不要添加任何原文没有的内容
- 如果没有找到作者信息，author_info 为空数组
"""


def optimize_article_for_publishing(article: dict, db: ArticleDatabase,
                                    enable_author_info: bool = True,
                                    custom_prompt: str = None) -> dict:
    """对文章进行发布前的 LLM 优化。

    在评分 >= 70 时调用，优化包括：
    1. 提取作者/编辑信息到文章末尾
    2. 替换"编者按"为"导语"

    Args:
        article: 文章 dict，包含 blocks
        db: 数据库实例
        enable_author_info: 是否启用作者信息提取
        custom_prompt: 自定义优化 prompt

    Returns:
        优化后的 article dict
    """
    if not article.get("blocks"):
        return article

    log.info("[LLM] 开始优化文章: aid=%s, 作者信息提取=%s",
             article.get("article_id", ""), enable_author_info)

    # 1. 替换"编者按"为"导语"（适用于律动等信源）
    blocks = article.get("blocks", [])
    for block in blocks:
        if block.get("type") in ("h2", "h3", "h4", "p"):
            text = block.get("text", "")
            # 匹配"编者按"（可能有各种标点符号）
            if re.search(r'编者按[：:：]?', text):
                new_text = re.sub(r'编者按([：:：]?)', r'导语\1', text)
                if new_text != text:
                    block["text"] = new_text
                    log.info("[LLM] 替换'编者按'为'导语': aid=%s", article.get("article_id", ""))

    # 2. 提取作者信息
    if enable_author_info:
        try:
            # 尝试使用自定义 prompt
            if custom_prompt:
                article = extract_author_info_with_prompt(article, db, custom_prompt)
            else:
                article = extract_author_info(article, db, use_llm=True)
        except Exception as e:
            log.warning("[LLM] 作者信息提取失败: aid=%s, error=%s",
                        article.get("article_id", ""), e)

    log.info("[LLM] 文章优化完成: aid=%s", article.get("article_id", ""))
    return article


def extract_author_info_with_prompt(article: dict, db: ArticleDatabase,
                                    prompt: str) -> dict:
    """使用自定义 prompt 提取作者信息。

    Args:
        article: 文章 dict
        db: 数据库实例
        prompt: 自定义 prompt

    Returns:
        优化后的 article dict
    """
    blocks = article.get("blocks", [])
    if not blocks:
        return article

    # 检查文章头部（前 15 个非图片 block）
    header_blocks = []
    header_indices = []
    for i, block in enumerate(blocks):
        if block.get("type") == "img":
            continue
        text = block.get("text", "").strip()
        if text:
            header_blocks.append(text)
            header_indices.append(i)
        if len(header_blocks) >= 15:
            break

    if not header_blocks:
        return article

    try:
        svc = _get_llm_service(db)
        header_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(header_blocks))
        response = svc.chat(
            "abstract",
            prompt,
            header_text,
            max_tokens=2048,
            temperature=0.1
        )

        if response:
            # 提取 JSON
            match = re.search(r'\{[\s\S]*\}', response)
            if match:
                data = json.loads(match.group(0))
                author_info = data.get("author_info", [])

                # 移除原文中的作者信息行
                to_remove = set(data.get("to_remove", []))
                if to_remove:
                    indices_to_remove = set()
                    for remove_text in to_remove:
                        clean_text = re.sub(r'^\d+\.\s*', '', remove_text).strip()
                        for idx, original_text in enumerate(header_blocks):
                            if clean_text in original_text or original_text in clean_text:
                                indices_to_remove.add(header_indices[idx])
                                break

                    # 构建新的 blocks
                    new_blocks = []
                    for i, block in enumerate(blocks):
                        if i in indices_to_remove:
                            continue
                        new_blocks.append(block)

                    # 在文章末尾添加作者信息
                    if author_info:
                        new_blocks.append({"type": "p", "text": ""})
                        for info in author_info:
                            new_blocks.append({"type": "p", "text": info})

                    result = {k: v for k, v in article.items()}
                    result["blocks"] = new_blocks
                    log.info("[LLM] 自定义 prompt 作者信息提取成功: aid=%s, 提取 %d 条",
                             article.get("article_id", ""), len(author_info))
                    return result
    except Exception as e:
        log.warning("[LLM] 自定义 prompt 作者信息提取失败: aid=%s, error=%s",
                    article.get("article_id", ""), e)

    return article


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
