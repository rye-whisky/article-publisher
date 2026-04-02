# -*- coding: utf-8 -*-
"""Create a new PostgreSQL database."""

import sys
from pathlib import Path
# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.ssh_tunnel import create_ssh_tunnel
import psycopg2
from psycopg2 import sql


def create_database(
    db_name: str = "info_article",
    ssh_host: str = "REDACTED",
    ssh_username: str = "root",
    ssh_password: str = "REDACTED",
    db_user: str = "postgres",  # Use postgres superuser to create database
    db_password: str = "REDACTED",  # SSH password as DB password
    db_host: str = "localhost",
    db_port: int = 5432,
    grant_to_user: str = "ai_news_user",
):
    """Create a new PostgreSQL database through SSH tunnel."""
    # Start SSH tunnel
    print("[*] Starting SSH tunnel...")
    tunnel = create_ssh_tunnel(ssh_host=ssh_host, ssh_username=ssh_username, ssh_password=ssh_password)
    tunnel.start()
    print(f"[+] Tunnel established: localhost:{tunnel.local_bind_port}")

    try:
        # Connect to PostgreSQL (connect to default 'postgres' database first)
        conn = psycopg2.connect(
            host=db_host,
            port=tunnel.local_bind_port,
            user=db_user,
            password=db_password,
            database="postgres"  # Connect to default database
        )
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # Check if database exists
        cursor.execute(
            sql.SQL("SELECT 1 FROM pg_database WHERE datname = {}").format(sql.Literal(db_name))
        )
        exists = cursor.fetchone()

        if exists:
            print(f"[!] Database '{db_name}' already exists")
        else:
            # Create database
            cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(db_name),
                sql.Identifier(grant_to_user)
            ))
            print(f"[+] Database '{db_name}' created successfully")

        cursor.close()
        conn.close()

    finally:
        tunnel.stop()
        print("[+] Tunnel closed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create PostgreSQL database")
    parser.add_argument("--name", default="info_article", help="Database name")
    args = parser.parse_args()

    create_database(args.name)
