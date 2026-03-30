# -*- coding: utf-8 -*-
"""Article API routes."""

from fastapi import APIRouter, HTTPException, Query

from services.pipeline_service import get_pipeline, remove_published_id

router = APIRouter(prefix="/api", tags=["articles"])


@router.get("/articles")
def list_articles(
    source: str = Query("all", pattern="^(stcn|techflow|all)$"),
    limit: int = Query(50, ge=1, le=200),
):
    """List articles from local storage."""
    p = get_pipeline()
    state = p.load_state()
    published_ids = set(state.get("published_ids", []))
    articles = p.load_articles(source)[:limit]
    for a in articles:
        a["published"] = a["article_id"] in published_ids
    return {"total": len(articles), "articles": articles}


@router.get("/articles/{article_id}")
def get_article(article_id: str):
    """Get a single article's detail."""
    p = get_pipeline()
    state = p.load_state()
    published_ids = set(state.get("published_ids", []))
    for source in ("stcn", "techflow"):
        for a in p.load_articles(source):
            if a["article_id"] == article_id:
                a["published"] = a["article_id"] in published_ids
                return a
    raise HTTPException(404, f"Article {article_id} not found")


@router.delete("/api/state/{article_id}")
def delete_from_state(article_id: str):
    """Remove an article ID from the dedup state (allow republish)."""
    if remove_published_id(article_id):
        return {"ok": True, "removed": article_id}
    raise HTTPException(404, f"{article_id} not in state")
