# -*- coding: utf-8 -*-
"""Article API routes: list, get, create, update, delete."""

import time as _time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from routes.auth import require_admin

from models.schemas import CreateArticleRequest, UpdateArticleRequest
from services.article_store import ArticleStore

router = APIRouter(prefix="/api", tags=["articles"])

# Fields returned in list view (no blocks to save memory)
_LIST_FIELDS = {
    "article_id", "source_key", "title", "author", "source",
    "publish_time", "original_url", "published", "abstract", "cover_image",
    "score", "tags", "filter_status", "filter_reason", "score_status",
    "score_reason", "review_status", "cms_id", "published_strategy",
    "auto_publish_enabled", "publish_stage", "broadcasted_at",
}
AUTO_CANDIDATE_SOURCE = "auto_candidates"


def _is_public_stage(article: dict) -> bool:
    return (article or {}).get("publish_stage") in {"published", "broadcasted"}


def _excluded_article_sources(request: Request) -> list[str]:
    """Sources hidden from the blockchain article page."""
    excluded = {"bestblogs"}

    ai_svc = getattr(request.app.state, "ai_pipeline_service", None)
    if ai_svc and getattr(ai_svc, "scrapers", None):
        excluded.update(ai_svc.scrapers.keys())

    return sorted(excluded)


@router.get("/articles")
def list_articles(
    request: Request,
    source: str = Query("all", pattern="^(stcn|techflow|blockbeats|chaincatcher|odaily|auto_candidates|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("time", pattern="^(time|score)$"),
):
    """List articles (summary only, no blocks) with pagination."""
    svc = request.app.state.pipeline_service

    if svc.database:
        if source == AUTO_CANDIDATE_SOURCE:
            return _list_auto_candidate_articles(request, page=page, page_size=page_size, sort_by=sort_by)

        excluded_sources = _excluded_article_sources(request)
        total = svc.database.count_articles(
            source_key=source,
            exclude_source_keys=excluded_sources,
        )
        articles = svc.database.list_articles(
            source_key=source,
            limit=page_size,
            offset=(page - 1) * page_size,
            sort_by=sort_by,
            exclude_source_keys=excluded_sources,
        )
    else:
        state = svc.load_state()
        published_ids = set(state.get("published_ids", []))
        total, articles = svc.article_store.list_articles_paged(source, page, page_size, sort_by=sort_by)
        result = []
        for a in articles:
            a["published"] = a["article_id"] in published_ids
            ArticleStore.enrich_article(a)
            result.append({k: a[k] for k in _LIST_FIELDS if k in a})
        return {"total": total, "page": page, "page_size": page_size, "articles": result}

    result = []
    for a in articles:
        a["published"] = _is_public_stage(a)
        ArticleStore.enrich_article(a)
        result.append({k: a[k] for k in _LIST_FIELDS if k in a})

    return {"total": total, "page": page, "page_size": page_size, "articles": result}


def _list_auto_candidate_articles(request: Request, page: int, page_size: int, sort_by: str) -> dict:
    """List current-window auto-publish candidates for the article page."""
    svc = request.app.state.pipeline_service
    scheduler = getattr(svc, "auto_publish_scheduler", None)

    if not svc.database or not scheduler:
        return {
            "total": 0,
            "page": page,
            "page_size": page_size,
            "articles": [],
            "auto_candidate_window": None,
        }

    context = scheduler.get_window_context()
    pushed_in_window = svc.database.count_pushes_in_window(context["window_start"], strategy="auto")
    if pushed_in_window > 0:
        return {
            "total": 0,
            "page": page,
            "page_size": page_size,
            "articles": [],
            "auto_candidate_window": {
                "window_start": context["window_start"].isoformat(),
                "window_end": context["window_end"].isoformat(),
                "min_score": context["min_score"],
                "auto_sources": context["auto_sources"],
                "is_morning": context["is_morning"],
                "window_full": True,
                "pushed_in_window": pushed_in_window,
            },
        }

    candidates = svc.database.get_auto_publish_broadcast_candidates(
        min_score=context["min_score"],
        limit=None,
        source_keys=context["auto_sources"],
        window_start=context["window_start"],
        window_end=context["window_end"],
        sort_by=sort_by,
    )

    total = len(candidates)
    start = max(0, (page - 1) * page_size)
    page_items = candidates[start:start + page_size]

    result = []
    for article in page_items:
        article["published"] = _is_public_stage(article)
        ArticleStore.enrich_article(article)
        result.append({k: article[k] for k in _LIST_FIELDS if k in article})

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "articles": result,
        "auto_candidate_window": {
            "window_start": context["window_start"].isoformat(),
            "window_end": context["window_end"].isoformat(),
            "min_score": context["min_score"],
            "auto_sources": context["auto_sources"],
            "is_morning": context["is_morning"],
            "window_full": False,
            "pushed_in_window": pushed_in_window,
        },
    }


@router.get("/articles/{article_id}")
def get_article(request: Request, article_id: str):
    """Get a single article's full detail (including blocks)."""
    svc = request.app.state.pipeline_service
    state = svc.load_state()
    published_ids = set(state.get("published_ids", []))
    article = svc.database.get_by_article_id(article_id) if svc.database else None
    if not article:
        article = svc.article_store.get_article(article_id)
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")
    article["published"] = _is_public_stage(article) or article["article_id"] in published_ids
    ArticleStore.enrich_article(article)
    return article


@router.post("/articles")
def create_article(request: Request, req: CreateArticleRequest, _admin=Depends(require_admin)):
    """Create a new article manually."""
    svc = request.app.state.pipeline_service
    raw_id = f"manual_{int(_time.time() * 1000)}"
    article = {
        "source_key": req.source_key,
        "article_id": f"{req.source_key}:{raw_id}",
        "raw_id": raw_id,
        "title": req.title,
        "author": req.author or "",
        "source": req.source or {
            "stcn": "券商中国",
            "techflow": "深潮 TechFlow",
            "blockbeats": "律动 BlockBeats",
            "chaincatcher": "链捕手",
            "odaily": "Odaily星球日报",
        }.get(req.source_key, req.source_key),
        "publish_time": _time.strftime("%Y-%m-%d %H:%M"),
        "original_url": req.original_url or "",
        "cover_src": req.cover_src or "",
        "blocks": req.blocks or [],
    }
    path = svc.article_store.create_article(article)
    if svc.database:
        svc.database.insert_or_update(article)
    ArticleStore.enrich_article(article)
    return {"ok": True, "article": article, "path": path}


@router.put("/articles/{article_id}")
def update_article(request: Request, article_id: str, req: UpdateArticleRequest, _admin=Depends(require_admin)):
    """Update an existing article."""
    svc = request.app.state.pipeline_service
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    article = svc.article_store.update_article(article_id, updates)
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")
    if svc.database:
        svc.database.insert_or_update(article)
    ArticleStore.enrich_article(article)
    return {"ok": True, "article": article}


@router.delete("/articles/{article_id}")
def delete_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Delete an article file and remove from published state."""
    svc = request.app.state.pipeline_service
    deleted = svc.article_store.delete_article(article_id)
    if not deleted:
        raise HTTPException(404, f"Article {article_id} not found")

    # Remove from published state if present
    state = svc.load_state()
    ids = state.get("published_ids", [])
    if article_id in ids:
        ids.remove(article_id)
        state["published_ids"] = ids
        svc.save_state(state)

    # Remove from database so pipeline can re-fetch
    if svc.database:
        svc.database.delete(article_id)

    return {"ok": True, "deleted": article_id}


@router.post("/articles/batch-delete")
async def batch_delete_articles(request: Request, _admin=Depends(require_admin)):
    """Batch delete articles (file + state + DB)."""
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No ids provided")
    svc = request.app.state.pipeline_service
    state = svc.load_state()
    published_ids = state.get("published_ids", [])
    changed = False
    deleted = []
    for article_id in ids:
        ok = svc.article_store.delete_article(article_id)
        if ok:
            deleted.append(article_id)
            if article_id in published_ids:
                published_ids.remove(article_id)
                changed = True
            if svc.database:
                svc.database.delete(article_id)
    if changed:
        state["published_ids"] = published_ids
        svc.save_state(state)
    return {"ok": True, "deleted": deleted}


@router.delete("/state/{article_id}")
def delete_from_state(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Remove an article ID from the dedup state (allow republish)."""
    svc = request.app.state.pipeline_service
    state = svc.load_state()
    ids = state.get("published_ids", [])
    if article_id in ids:
        ids.remove(article_id)
        state["published_ids"] = ids
        svc.save_state(state)
        return {"ok": True, "removed": article_id}
    raise HTTPException(404, f"{article_id} not in state")


@router.post("/articles/{article_id}/ai-edit")
async def ai_edit_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """AI-edit an article's body text. Returns edited text without saving."""
    from pydantic import BaseModel

    class AiEditRequest(BaseModel):
        system_prompt: str = ""
        user_prompt: str = ""

    raw = await request.json()
    system_prompt = raw.get("system_prompt", "")
    user_prompt = raw.get("user_prompt", "")

    svc = request.app.state.pipeline_service
    article = svc.article_store.get_article(article_id)
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")

    # Build body text from blocks
    text_parts = []
    for b in article.get("blocks", []):
        if b.get("type") != "img" and b.get("text"):
            tag = b.get("tag", b.get("type", "p"))
            text = b.get("text", "").strip()
            if text:
                text_parts.append(f"<{tag}>{text}</{tag}>")
    body_text = "\n".join(text_parts)
    if not body_text.strip():
        raise HTTPException(400, "文章正文为空")

    # Merge prompts
    final_prompt = system_prompt or ""
    if user_prompt:
        final_prompt = f"{final_prompt}\n\n{user_prompt}" if final_prompt else user_prompt

    from services.llm import ai_edit_text
    edited = ai_edit_text(body_text, svc.database, system_prompt=final_prompt or None)
    if not edited:
        raise HTTPException(502, "AI 编辑失败，请检查 LLM 配置")

    return {"ok": True, "edited_text": edited}


def _load_article_for_cms(request: Request, article_id: str) -> dict:
    svc = request.app.state.pipeline_service
    article = svc.article_store.get_article(article_id)
    if not article:
        article = svc.database.get_by_article_id(article_id) if svc.database else None
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")

    # Enrich abstract from DB if available
    if svc.database:
        db_art = svc.database.get_by_article_id(article_id)
        if db_art:
            if db_art.get("abstract"):
                article["abstract"] = db_art["abstract"]
            if db_art.get("cms_id"):
                article["cms_id"] = db_art["cms_id"]
            if db_art.get("publish_stage"):
                article["publish_stage"] = db_art["publish_stage"]
    return article


@router.post("/articles/{article_id}/draft")
def save_article_draft(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Save an article to the CMS draft box."""
    svc = request.app.state.pipeline_service
    article = _load_article_for_cms(request, article_id)
    if article.get("publish_stage") in {"published", "broadcasted"}:
        raise HTTPException(400, "Article is already published; use the publish action to update it")

    try:
        result = svc.save_article_draft(article, strategy="manual")
        return {"ok": True, "cms_id": result["cms_id"], "title": article.get("title", ""), "publish_stage": "draft"}
    except Exception as e:
        raise HTTPException(502, f"保存后台草稿失败: {str(e)[:200]}")


@router.post("/articles/{article_id}/publish")
def publish_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Publish an article publicly to CMS."""
    svc = request.app.state.pipeline_service
    article = _load_article_for_cms(request, article_id)

    try:
        result = svc.publish_article(article, strategy="manual")
        return {"ok": True, "cms_id": result["cms_id"], "title": article.get("title", ""), "publish_stage": "published"}
    except Exception as e:
        raise HTTPException(502, f"发布失败: {str(e)[:200]}")


@router.post("/articles/{article_id}/broadcast")
def broadcast_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Push a published article to App desktop notification."""
    svc = request.app.state.pipeline_service
    article = _load_article_for_cms(request, article_id)

    stage = article.get("publish_stage", "local")
    if stage not in ("published", "broadcasted"):
        raise HTTPException(400, "文章必须先发布才能推送")
    if not article.get("cms_id"):
        raise HTTPException(400, "文章缺少 cms_id，无法推送")

    if svc.database and svc.database.has_broadcast_history(article_id):
        raise HTTPException(409, "文章已经推送过，不可重复推送")

    try:
        result = svc.broadcast_article(article, strategy="manual")
        push_label = svc.get_push_label(article.get("score"))
        return {
            "ok": True,
            "article_id": article_id,
            "cms_id": article.get("cms_id"),
            "title": article.get("title", ""),
            "push_label": push_label or (article.get("title", "") or "")[:120],
            "publish_stage": "broadcasted",
        }
    except Exception as e:
        raise HTTPException(502, f"推送失败: {str(e)[:200]}")


@router.post("/articles/{article_id}/republish")
def republish_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Backward-compatible alias for public publish."""
    return publish_article(request, article_id, _admin)
