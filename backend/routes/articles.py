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

    return {"ok": True, "deleted": article_id}


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
