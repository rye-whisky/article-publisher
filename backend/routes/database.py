# -*- coding: utf-8 -*-
"""Database API routes: list, stats, query articles from SQLite."""

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/db", tags=["database"])


@router.get("/articles")
def list_db_articles(
    request: Request,
    source: str = Query("all", pattern="^(stcn|techflow|blockbeats|chaincatcher|all)$"),
    unpublished_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List articles from database with filters."""
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")

    articles = svc.database.list_articles(
        source_key=source,
        limit=limit,
        offset=offset,
        unpublished_only=unpublished_only,
    )
    total = svc.database.count_articles(source_key=source, unpublished_only=unpublished_only)

    # Strip blocks from list view for performance
    result = []
    for a in articles:
        a_copy = {k: v for k, v in a.items() if k != "blocks"}
        a_copy["published"] = a.get("cms_id") is not None
        result.append(a_copy)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "articles": result,
    }


@router.get("/articles/{article_id}")
def get_db_article(request: Request, article_id: str):
    """Get a single article from database (including blocks)."""
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")

    article = svc.database.get_by_article_id(article_id)
    if not article:
        raise HTTPException(404, f"Article {article_id} not found")

    article["published"] = article.get("cms_id") is not None
    return article


@router.get("/stats")
def get_db_stats(request: Request):
    """Get database statistics."""
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")

    return svc.database.get_stats()


@router.get("/published")
def get_published_ids(request: Request):
    """Get all published article IDs from database."""
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")

    return {"published_ids": svc.database.get_published_ids()}
