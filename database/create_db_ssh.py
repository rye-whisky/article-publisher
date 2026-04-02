# -*- coding: utf-8 -*-
"""Create database via SSH command."""

import paramiko
import time


def create_database_via_ssh(
    db_name: str = "info_article",
    db_owner: str = "ai_news_user",
    ssh_host: str = "REDACTED",
    ssh_port: int = 22,
    ssh_username: str = "root",
    ssh_password: str = "REDACTED",
):
    """Create database via SSH using sudo -u postgres."""
    print(f"[*] Connecting to {ssh_host}...")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=ssh_host,
        port=ssh_port,
        username=ssh_username,
        password=ssh_password,
    )

    try:
        # Check if database exists
        check_cmd = f"sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname='{db_name}'\""
        stdin, stdout, stderr = client.exec_command(check_cmd)
        exists = stdout.read().decode().strip()

        if exists == "1":
            print(f"[!] Database '{db_name}' already exists")
        else:
            # Create database with cd workaround
            create_cmd = f"cd /tmp && sudo -u postgres psql -c \"CREATE DATABASE {db_name} OWNER {db_owner};\""
            stdin, stdout, stderr = client.exec_command(create_cmd)
            stdout_str = stdout.read().decode().strip()
            error_str = stderr.read().decode().strip()

            if error_str and "ERROR" in error_str:
                print(f"[!] Error: {error_str}")
            else:
                print(f"[+] Database '{db_name}' created successfully")

    finally:
        client.close()
        print("[+] SSH connection closed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create PostgreSQL database via SSH")
    parser.add_argument("--name", default="info_article", help="Database name")
    args = parser.parse_args()

    create_database_via_ssh(args.name)
