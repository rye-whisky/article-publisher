# -*- coding: utf-8 -*-
"""CRUD operations for articles."""

from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import datetime

from .models import Article


class ArticleCRUD:
    """CRUD operations for Article model."""

    @staticmethod
    def get_by_id(db: Session, article_id: int) -> Article | None:
        """Get article by primary key ID."""
        return db.query(Article).filter(Article.id == article_id).first()

    @staticmethod
    def get_by_article_id(db: Session, article_id: str) -> Article | None:
        """Get article by source article_id (e.g. STCN ID)."""
        return db.query(Article).filter(Article.article_id == article_id).first()

    @staticmethod
    def get_by_original_url(db: Session, original_url: str) -> Article | None:
        """Get article by original URL."""
        return db.query(Article).filter(Article.original_url == original_url).first()

    @staticmethod
    def get_by_cms_id(db: Session, cms_id: str) -> Article | None:
        """Get article by CMS ID."""
        return db.query(Article).filter(Article.cms_id == cms_id).first()

    @staticmethod
    def list_articles(
        db: Session,
        source_key: str | None = None,
        published: bool | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Article]:
        """List articles with optional filters."""
        query = db.query(Article)

        if source_key:
            query = query.filter(Article.source_key == source_key)
        if published is not None:
            query = query.filter(Article.published == published)
        if tag:
            query = query.filter(Article.tag == tag)

        return query.order_by(Article.created_at.desc()).offset(offset).limit(limit).all()

    @staticmethod
    def create_article(
        db: Session,
        article_id: str,
        source_key: str,
        title: str,
        blocks: list | None = None,
        author: str | None = None,
        source: str | None = None,
        abstract: str | None = None,
        cover_src: str | None = None,
        original_url: str | None = None,
        publish_time: datetime | None = None,
        tag: str | None = None,
    ) -> Article:
        """Create a new article."""
        article = Article(
            article_id=article_id,
            source_key=source_key,
            title=title,
            blocks_json=blocks,
            author=author,
            source=source,
            abstract=abstract,
            cover_src=cover_src,
            original_url=original_url,
            publish_time=publish_time,
            tag=tag,
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        return article

    @staticmethod
    def update_article(
        db: Session,
        article: Article,
        title: str | None = None,
        blocks: list | None = None,
        author: str | None = None,
        abstract: str | None = None,
        cover_src: str | None = None,
        tag: str | None = None,
    ) -> Article:
        """Update article fields."""
        if title is not None:
            article.title = title
        if blocks is not None:
            article.blocks_json = blocks
        if author is not None:
            article.author = author
        if abstract is not None:
            article.abstract = abstract
        if cover_src is not None:
            article.cover_src = cover_src
        if tag is not None:
            article.tag = tag

        db.commit()
        db.refresh(article)
        return article

    @staticmethod
    def mark_published(db: Session, article: Article, cms_id: str) -> Article:
        """Mark article as published with CMS ID."""
        article.published = True
        article.cms_id = cms_id
        article.published_at = datetime.now()
        db.commit()
        db.refresh(article)
        return article

    @staticmethod
    def delete_article(db: Session, article: Article) -> None:
        """Delete an article."""
        db.delete(article)
        db.commit()

    @staticmethod
    def count_articles(
        db: Session,
        source_key: str | None = None,
        published: bool | None = None,
        tag: str | None = None,
    ) -> int:
        """Count articles with optional filters."""
        query = db.query(Article)
        if source_key:
            query = query.filter(Article.source_key == source_key)
        if published is not None:
            query = query.filter(Article.published == published)
        if tag:
            query = query.filter(Article.tag == tag)
        return query.count()


# Convenience function
def get_article_crud() -> type[ArticleCRUD]:
    """Return ArticleCRUD class for dependency injection."""
    return ArticleCRUD
