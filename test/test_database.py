#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test script for SQLite database functionality."""

import sys
import json
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from services.database import ArticleDatabase

def test_database():
    """Test basic database operations."""
    # Create test database
    db_path = Path(__file__).parent.parent / "data" / "test_articles.db"
    db = ArticleDatabase(db_path)

    print("Testing database...")

    # Test insert
    test_article = {
        "article_id": "test:12345",
        "source_key": "test",
        "raw_id": "12345",
        "title": "Test Article",
        "source": "Test Source",
        "author": "Test Author",
        "publish_time": "2026-04-02 10:00",
        "original_url": "https://example.com/test",
        "cover_src": "",
        "blocks": [
            {"type": "h2", "text": "Test Heading"},
            {"type": "p", "text": "This is a test article content."},
        ],
    }

    row_id = db.insert_or_update(test_article)
    print(f"[OK] Inserted article with row_id={row_id}")

    # Test get
    retrieved = db.get_by_article_id("test:12345")
    assert retrieved is not None
    assert retrieved["title"] == "Test Article"
    print(f"[OK] Retrieved article: {retrieved['title']}")

    # Test update (same article_id)
    test_article["title"] = "Updated Title"
    db.insert_or_update(test_article)
    updated = db.get_by_article_id("test:12345")
    assert updated["title"] == "Updated Title"
    print(f"[OK] Updated article: {updated['title']}")

    # Test list
    articles = db.list_articles(limit=10)
    assert len(articles) >= 1
    print(f"[OK] Listed {len(articles)} article(s)")

    # Test count
    count = db.count_articles()
    print(f"[OK] Total articles: {count}")

    # Test mark published
    success = db.mark_published("test:12345", "cms_12345")
    assert success
    print(f"[OK] Marked as published: cms_12345")

    # Test get published IDs
    published_ids = db.get_published_ids()
    assert "test:12345" in published_ids
    print(f"[OK] Published IDs: {published_ids}")

    # Test stats
    stats = db.get_stats()
    print(f"[OK] Stats: {json.dumps(stats, indent=2, ensure_ascii=False)}")

    # Test delete
    deleted = db.delete("test:12345")
    assert deleted
    print(f"[OK] Deleted article")

    # Cleanup
    db.close()
    db_path.unlink()
    print(f"\n[OK] All tests passed! Database cleaned up.")

    # Show database file location
    db_dir = Path(__file__).parent.parent / "data"
    print(f"\nDatabase directory: {db_dir}")
    print(f"To use in production, set in config.yaml:")
    print(f"  database:")
    print(f"    sqlite_path: 'data/articles.db'")

if __name__ == "__main__":
    test_database()
