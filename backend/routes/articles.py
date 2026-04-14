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
}


@router.get("/articles")
def list_articles(
    request: Request,
    source: str = Query("all", pattern="^(stcn|techflow|blockbeats|chaincatcher|odaily|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List articles (summary only, no blocks) with pagination."""
    svc = request.app.state.pipeline_service
    state = svc.load_state()
    published_ids = set(state.get("published_ids", []))
    total, articles = svc.article_store.list_articles_paged(source, page, page_size)

    result = []
    for a in articles:
        a["published"] = a["article_id"] in published_ids
        ArticleStore.enrich_article(a)
        result.append({k: a[k] for k in _LIST_FIELDS if k in a})

    return {"total": total, "page": page, "page_size": page_size, "articles": result}


@router.get("/articles/{article_id}")
def get_article(request: Request, article_id: str):
    """Get a single article's full detail (including blocks)."""
    svc = request.app.state.pipeline_service
    state = svc.load_state()
    published_ids = set(state.get("published_ids", []))
    article = svc.article_store.get_article(article_id)
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")
    article["published"] = article["article_id"] in published_ids
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
        db_id = article_id.split(":")[-1] if ":" in article_id else article_id
        svc.database.delete(db_id)

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
                db_id = article_id.split(":")[-1] if ":" in article_id else article_id
                svc.database.delete(db_id)
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


@router.post("/articles/{article_id}/republish")
def republish_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Republish an article to CMS."""
    svc = request.app.state.pipeline_service
    article = svc.article_store.get_article(article_id)
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")

    # Enrich abstract from DB if available
    if svc.database:
        db_lookup_id = article_id.split(":")[-1] if ":" in article_id else article_id
        db_art = svc.database.get_by_article_id(db_lookup_id)
        if db_art and db_art.get("abstract"):
            article["abstract"] = db_art["abstract"]

    try:
        result = svc.publisher.publish(article)
        # Mark as published
        state = svc.load_state()
        ids = set(state.get("published_ids", []))
        ids.add(article_id)
        state["published_ids"] = sorted(ids)
        svc.save_state(state)
        if svc.database:
            svc.database.mark_published(
                article_id.split(":")[-1] if ":" in article_id else article_id,
                result["cms_id"],
            )
        return {"ok": True, "cms_id": result["cms_id"], "title": article.get("title", "")}
    except Exception as e:
        raise HTTPException(502, f"推送失败: {str(e)[:200]}")
