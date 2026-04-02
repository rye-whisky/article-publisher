# -*- coding: utf-8 -*-
"""SSH Tunnel for PostgreSQL connection using paramiko."""

import paramiko
import sshtunnel
from sshtunnel import SSHTunnelForwarder
import time


def create_ssh_tunnel(
    ssh_host: str = "REDACTED",
    ssh_port: int = 22,
    ssh_username: str = "root",
    ssh_password: str = "REDACTED",
    remote_host: str = "localhost",
    remote_port: int = 5432,
    local_port: int = 5432,
):
    """Create and return an SSH tunnel to remote PostgreSQL."""
    server = SSHTunnelForwarder(
        (ssh_host, ssh_port),
        ssh_username=ssh_username,
        ssh_password=ssh_password,
        remote_bind_address=(remote_host, remote_port),
        local_bind_address=("localhost", local_port),
    )
    return server


if __name__ == "__main__":
    # 测试SSH隧道
    print("[*] Creating SSH tunnel...")
    tunnel = create_ssh_tunnel()
    tunnel.start()
    print(f"[+] SSH tunnel established: localhost:{tunnel.local_bind_port} -> {tunnel.remote_bind_address}")

    try:
        print("[*] Press Ctrl+C to stop tunnel...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping tunnel...")
        tunnel.stop()
        print("[+] Tunnel closed.")
