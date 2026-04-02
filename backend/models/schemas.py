# -*- coding: utf-8 -*-
"""Pydantic data models for API request/response validation."""

from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    """Request to trigger a pipeline run."""
    source: str = Field(default="all", pattern="^(stcn|techflow|blockbeats|chaincatcher|all)$",
                        description="Article source to process")
    dry_run: bool = Field(default=False, description="If true, don't publish")
    skip_fetch: bool = Field(default=False, description="If true, only publish from local files")
    since_today_0700: bool = Field(default=False, description="Only STCN articles after 07:00 today")


class RefetchRequest(BaseModel):
    """Request to refetch specific articles."""
    source: str = Field(default="stcn", pattern="^(stcn|techflow|blockbeats|chaincatcher|all)$")
    stcn_urls: list[str] = Field(default_factory=list)
    techflow_ids: list[str] = Field(default_factory=list)
    blockbeats_urls: list[str] = Field(default_factory=list)
    chaincatcher_urls: list[str] = Field(default_factory=list)
    republish: bool = Field(default=False, description="Also republish the refetched articles")


class SourceScheduleRequest(BaseModel):
    """Request to update a source's schedule config."""
    enabled: bool
    interval_minutes: int = Field(default=60, ge=1, le=1440)


class CreateArticleRequest(BaseModel):
    """Request to create a new article manually."""
    title: str = Field(..., min_length=1)
    source_key: str = Field(pattern="^(stcn|techflow|blockbeats|chaincatcher)$")
    blocks: list[dict] = Field(default_factory=list)
    cover_src: str = ""
    abstract: str = ""
    author: str = ""
    source: str = ""
    original_url: str = ""


class UpdateArticleRequest(BaseModel):
    """Request to update an existing article."""
    title: str | None = None
    blocks: list[dict] | None = None
    cover_src: str | None = None
    abstract: str | None = None
    author: str | None = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ArticleResponse(BaseModel):
    """Article summary for list views."""
    article_id: str
    source_key: str
    title: str
    author: Optional[str] = None
    source: str
    publish_time: Optional[str] = None
    original_url: Optional[str] = None
    published: bool = False


class PublishedArticle(BaseModel):
    """Summary of a published article."""
    article_id: str
    cms_id: str
    title: str
    cover_image: str = ""
    cover_error: str = ""


class SkippedArticle(BaseModel):
    """Summary of a skipped article."""
    id: str
    reason: str


class FailedArticle(BaseModel):
    """Summary of a failed article."""
    id: str
    error: str
    source: str


class PipelineResult(BaseModel):
    """Result of a pipeline run."""
    ok: bool = True
    refetched: list[dict] = Field(default_factory=list)
    published: list[PublishedArticle] = Field(default_factory=list)
    skipped: list[SkippedArticle] = Field(default_factory=list)
    failed: list[FailedArticle] = Field(default_factory=list)
    error: Optional[str] = None


class SourceInfo(BaseModel):
    """Source status info."""
    enabled: bool = True
    authors: list[str] = Field(default_factory=list)


class PipelineStatus(BaseModel):
    """Current pipeline status."""
    running: bool
    last_result: Optional[PipelineResult] = None
    started_at: Optional[str] = None
    total_published: int = 0
    last_updated: Optional[str] = None
    sources: dict[str, SourceInfo] = Field(default_factory=dict)


class StateResponse(BaseModel):
    """Deduplication state."""
    published_ids: list[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


class LogsResponse(BaseModel):
    """Log lines response."""
    lines: list[str] = Field(default_factory=list)
