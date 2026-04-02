#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate existing article files to SQLite database."""

import sys
import yaml
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from pipelines import create_scrapers
from services.database import ArticleDatabase
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def migrate():
    """Migrate all existing article files to database."""
    base_dir = Path(__file__).resolve().parent.parent
    config_path = base_dir / "config.yaml"

    # Load config
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Setup HTTP session
    retry = Retry(total=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    # Create database
    db_path = base_dir / "data" / "articles.db"
    db = ArticleDatabase(db_path)
    print(f"Database: {db_path}")

    # Create scrapers
    scrapers = create_scrapers(cfg, session, base_dir)

    total = 0
    by_source = {}

    for source_key, scraper in scrapers.items():
        print(f"\nProcessing {source_key}...")
        count = 0
        for article in scraper._iter_articles(use_cache=False):
            try:
                db.insert_or_update(article)
                count += 1
                if count % 10 == 0:
                    print(f"  {source_key}: {count} articles...")
            except Exception as e:
                print(f"  ERROR: {article.get('article_id', '?')} - {e}")

        by_source[source_key] = count
        total += count
        print(f"  {source_key}: {count} articles migrated")

    print(f"\n=== Migration Complete ===")
    print(f"Total: {total} articles")
    for source, count in by_source.items():
        print(f"  {source}: {count}")

    # Show stats
    stats = db.get_stats()
    print(f"\nDatabase stats:")
    print(f"  Total: {stats['total_articles']}")
    print(f"  Published: {stats['published_articles']}")
    print(f"  Unpublished: {stats['unpublished_articles']}")


if __name__ == "__main__":
    migrate()
