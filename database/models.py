# -*- coding: utf-8 -*-
"""SQLAlchemy ORM models for article storage."""

from sqlalchemy import String, Boolean, DateTime, JSON, CheckConstraint, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from .database import Base


class Article(Base):
    """Article model storing fetched and published articles."""

    __tablename__ = "articles"

    # 主键
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement="auto")

    # 来源标识
    article_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    source_key: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    original_url: Mapped[str | None] = mapped_column(String(1000), unique=True)

    # 文章内容
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(200))
    abstract: Mapped[str | None] = mapped_column(String(2000))
    cover_src: Mapped[str | None] = mapped_column(String(1000))

    # 内容块（JSONB）
    blocks_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 发布时间（原始文章的发布时间）
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 标签
    tag: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # CMS 发布状态
    cms_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
        nullable=False,
    )

    # 约束
    __table_args__ = (
        CheckConstraint(
            "source_key IN ('stcn', 'techflow', 'blockbeats', 'chaincatcher')",
            name="articles_source_key_check",
        ),
        CheckConstraint(
            "tag IN ('ai+web3', 'ai股票', 'AI巨头动态', 'AI与社会', 'AI落地应用')",
            name="articles_tag_check",
        ),
        Index("idx_articles_source_key", "source_key"),
        Index("idx_articles_published", "published"),
        Index("idx_articles_tag", "tag"),
        Index("idx_articles_publish_time", "publish_time"),
    )

    def __repr__(self) -> str:
        return f"<Article(id={self.id}, article_id={self.article_id!r}, title={self.title!r})>"
