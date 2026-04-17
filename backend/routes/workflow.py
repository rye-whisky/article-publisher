# -*- coding: utf-8 -*-
"""Workflow routes: blocklist CRUD and auto-publish status."""

from fastapi import APIRouter, Depends, HTTPException, Request

from routes.auth import require_admin

router = APIRouter(prefix="/api", tags=["workflow"])


@router.get("/blocklist")
def list_blocklist(request: Request):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    return {"rules": svc.database.list_blocklist_rules()}


@router.post("/blocklist")
async def create_blocklist_rule(request: Request, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    body = await request.json()
    rule_id = svc.database.create_blocklist_rule(body)
    return {"ok": True, "id": rule_id}


@router.put("/blocklist/{rule_id}")
async def update_blocklist_rule(request: Request, rule_id: int, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    body = await request.json()
    ok = svc.database.update_blocklist_rule(rule_id, body)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


@router.delete("/blocklist/{rule_id}")
def delete_blocklist_rule(request: Request, rule_id: int, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.database:
        raise HTTPException(501, "Database not configured")
    ok = svc.database.delete_blocklist_rule(rule_id)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


@router.get("/workflow/status")
def get_workflow_status(request: Request):
    svc = request.app.state.pipeline_service
    return svc.get_workflow_status()


@router.post("/workflow/push-check")
def run_push_check(request: Request, _admin=Depends(require_admin)):
    svc = request.app.state.pipeline_service
    if not svc.auto_publish_scheduler:
        raise HTTPException(501, "Auto-publish scheduler not configured")
    return svc.auto_publish_scheduler.run_once()


@router.post("/workflow/broadcast-check")
def run_broadcast_check(request: Request, _admin=Depends(require_admin)):
    """Trigger a single auto-publish + broadcast cycle manually (alias for push-check)."""
    svc = request.app.state.pipeline_service
    if not svc.auto_publish_scheduler:
        raise HTTPException(501, "Auto-publish scheduler not configured")
    return svc.auto_publish_scheduler.run_once()


@router.post("/workflow/rescore-unscored")
def rescore_unscored_articles(request: Request, _admin=Depends(require_admin)):
    """Rescore all articles that don't have a score yet.

    Query params:
        since_date: ISO date string (e.g., "2026-04-17"). Only articles created ON or AFTER this date.
                  Default: "2026-04-17" (fix date for LLM config issue).
    """
    svc = request.app.state.pipeline_service
    if not svc.database or not svc.scorer:
        raise HTTPException(501, "Database or scorer not configured")

    # Default to 2026-04-17 (LLM fix date) if not specified
    since_date = request.query_params.get("since_date", "2026-04-17")

    unscored = svc.database.list_unscored_articles(since_date=since_date, limit=500)
    if not unscored:
        return {"ok": True, "count": 0, "message": f"没有未评分的文章 (自 {since_date})"}

    results = {"processed": 0, "drafts_saved": 0, "failed": 0, "since_date": since_date}
    for article in unscored:
        try:
            score_result = svc.scorer.score_article(article)
            svc.database.update_scoring(
                article_id=article["article_id"],
                score=score_result["score"],
                score_reason=score_result["reason"],
                tags=score_result["tags"],
                review_status=score_result["review_status"],
                auto_publish_enabled=score_result["auto_publish_enabled"],
                score_status="done",
            )

            # Auto-save CMS draft for 70-74 scored articles
            score = score_result["score"]
            if score is not None and 70 <= score < 75:
                try:
                    svc.save_article_draft(article, strategy="auto_score")
                    results["drafts_saved"] += 1
                except Exception as exc:
                    log.warning("Auto-draft save failed for %s: %s", article["article_id"], exc)

            results["processed"] += 1
        except Exception as exc:
            log.error("Rescore failed for %s: %s", article["article_id"], exc)
            results["failed"] += 1

    log.info("Rescored %d articles (since %s), %d drafts saved, %d failed",
             results["processed"], since_date, results["drafts_saved"], results["failed"])
    return {"ok": True, **results}
