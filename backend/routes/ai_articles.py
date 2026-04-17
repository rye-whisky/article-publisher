# -*- coding: utf-8 -*-
"""AI Articles API routes: list, get, ingest, publish, tags, stats, scheduling."""

import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from routes.auth import require_admin

router = APIRouter(prefix="/api/ai", tags=["ai-articles"])


@router.get("/articles")
def list_ai_articles(
    request: Request,
    source: str = Query("all"),
    category: str = Query(None),
    min_score: int = Query(None, ge=0, le=100),
    tag: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("time", pattern="^(time|score)$"),
):
    """List AI articles with filters and pagination."""
    svc = request.app.state.ai_pipeline_service
    total, articles = svc.list_articles(
        source=source, category=category, min_score=min_score,
        tag=tag, page=page, page_size=page_size, sort_by=sort_by,
    )

    # Strip blocks for list view
    for a in articles:
        a.pop("blocks", None)

    return {"total": total, "page": page, "page_size": page_size, "articles": articles}


@router.get("/articles/{article_id}")
def get_ai_article(request: Request, article_id: str):
    """Get full AI article detail."""
    svc = request.app.state.ai_pipeline_service
    article = svc.get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    return article


@router.put("/articles/{article_id}")
def update_ai_article(request: Request, article_id: str, body: dict = None, _admin=Depends(require_admin)):
    """Update an AI article."""
    if not body:
        raise HTTPException(400, "Request body required")
    svc = request.app.state.ai_pipeline_service
    article = svc.get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")

    updatable = ["title", "abstract", "blocks", "cover_src", "author", "tags", "category", "score", "one_sentence_summary"]
    for field in updatable:
        if field in body:
            article[field] = body[field]
    svc.database.insert_or_update(article)
    return {"ok": True, "article": svc.get_article(article_id)}


@router.delete("/articles/{article_id}")
def delete_ai_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Delete an AI article."""
    svc = request.app.state.ai_pipeline_service
    article = svc.get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    svc.database.delete(article_id)
    return {"ok": True, "deleted": article_id}


@router.post("/articles/{article_id}/ai-edit")
async def ai_edit_ai_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """AI-edit an AI article's body text. Returns edited text without saving."""
    raw = await request.json()
    system_prompt = raw.get("system_prompt", "")
    user_prompt = raw.get("user_prompt", "")

    svc = request.app.state.ai_pipeline_service
    article = svc.get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")

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

    pipeline_svc = request.app.state.pipeline_service
    from services.llm import ai_edit_text
    edited = ai_edit_text(body_text, pipeline_svc.database, system_prompt=final_prompt or None)
    if not edited:
        raise HTTPException(502, "AI 编辑失败，请检查 LLM 配置")

    return {"ok": True, "edited_text": edited}


@router.post("/articles/batch-delete")
async def batch_delete_ai_articles(request: Request, _admin=Depends(require_admin)):
    """Batch delete AI articles."""
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(400, "No ids provided")
    svc = request.app.state.ai_pipeline_service
    deleted = []
    for aid in ids:
        article = svc.get_article(aid)
        if article:
            svc.database.delete(aid)
            deleted.append(aid)
    return {"ok": True, "deleted": deleted}


@router.get("/status")
def get_ai_status(request: Request):
    """Get AI pipeline status (running state + schedules + stats)."""
    svc = request.app.state.ai_pipeline_service
    status = svc.get_status()
    schedules = svc.get_source_schedules()
    return {**status, "schedules": schedules}


@router.post("/run")
async def run_ai_ingest(request: Request, _admin=Depends(require_admin)):
    """Trigger AI article ingestion in a background thread (admin only)."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    source = body.get("source", "all")
    svc = request.app.state.ai_pipeline_service

    if not svc.run_state.start():
        raise HTTPException(409, "AI pipeline is already running")

    def _bg():
        try:
            summary = svc.ingest(source=source)
            svc.run_state.finish({"ok": True, "summary": summary})
        except Exception as e:
            svc.run_state.finish({"ok": False, "error": str(e)})

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    return {"status": "started", "message": f"AI ingest started for source={source}"}


@router.post("/cancel")
def cancel_ai_run(request: Request, _admin=Depends(require_admin)):
    """Cancel running AI ingest (admin only)."""
    svc = request.app.state.ai_pipeline_service
    if svc.run_state.cancel():
        return {"message": "AI ingest cancellation requested"}
    raise HTTPException(400, "No AI ingest is running")


@router.get("/schedules")
def get_ai_schedules(request: Request):
    """Get AI source schedules."""
    svc = request.app.state.ai_pipeline_service
    return {"schedules": svc.get_source_schedules()}


@router.put("/schedules/{source_key}")
def update_ai_schedule(request: Request, source_key: str, body: dict = None, _admin=Depends(require_admin)):
    """Update AI source schedule (admin only)."""
    if not body:
        raise HTTPException(400, "Request body required")
    enabled = body.get("enabled", False)
    interval_minutes = body.get("interval_minutes", 60)
    svc = request.app.state.ai_pipeline_service
    try:
        svc.set_source_schedule(source_key, enabled, interval_minutes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"schedules": svc.get_source_schedules()}


@router.post("/ingest")
def ingest_ai_articles(request: Request, _admin=Depends(require_admin)):
    """Trigger AI article ingestion synchronously (admin only). Legacy endpoint."""
    svc = request.app.state.ai_pipeline_service
    summary = svc.ingest()
    return {"status": "ok", "summary": summary}


@router.post("/articles/{article_id}/draft")
def save_ai_article_draft(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Save an AI article to the CMS draft box (admin only)."""
    svc = request.app.state.ai_pipeline_service
    article = svc.get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    if article.get("publish_stage") in {"published", "broadcasted"}:
        raise HTTPException(400, "Article is already published; use publish to update it")

    pipeline_svc = request.app.state.pipeline_service
    try:
        result = pipeline_svc.save_article_draft(article, strategy="manual")
        if result:
            return {"status": "ok", "cms_id": result.get("cms_id", ""), "publish_stage": "draft"}
        raise HTTPException(500, "Publish returned no result")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/articles/{article_id}/publish")
def publish_ai_article(request: Request, article_id: str, _admin=Depends(require_admin)):
    """Publish an AI article to CMS (admin only)."""
    svc = request.app.state.ai_pipeline_service
    article = svc.get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")

    pipeline_svc = request.app.state.pipeline_service
    try:
        result = pipeline_svc.publish_article(article, strategy="manual")
        if result:
            return {"status": "ok", "cms_id": result.get("cms_id", ""), "publish_stage": "published"}
        raise HTTPException(500, "Publish returned no result")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/tags")
def get_ai_tags(request: Request):
    """Get all unique tags from AI articles."""
    svc = request.app.state.ai_pipeline_service
    return {"tags": svc.get_tags()}


@router.get("/stats")
def get_ai_stats(request: Request):
    """Get AI article statistics."""
    svc = request.app.state.ai_pipeline_service
    return svc.get_stats()
