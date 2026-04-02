# -*- coding: utf-8 -*-
"""Initialize database tables and seed data."""

from .database import engine, Base
from .models import Article


def init_db(drop_tables: bool = False):
    """
    Initialize database tables.

    Args:
        drop_tables: If True, drop existing tables first (USE WITH CAUTION!)
    """
    if drop_tables:
        print("[!] Dropping all tables...")
        Base.metadata.drop_all(bind=engine)

    print("[*] Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("[+] Database initialized successfully!")


def main():
    """CLI entry point for database initialization."""
    import argparse

    parser = argparse.ArgumentParser(description="Initialize article database")
    parser.add_argument(
        "--drop", action="store_true", help="Drop existing tables before creating"
    )
    args = parser.parse_args()

    init_db(drop_tables=args.drop)


if __name__ == "__main__":
    main()
