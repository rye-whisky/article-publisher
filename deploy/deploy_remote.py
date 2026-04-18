#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remote deployment helper for Article Publisher."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko


SERVER = {
    "host": os.environ.get("DEPLOY_HOST", "").strip(),
    "port": int(os.environ.get("DEPLOY_PORT", "22")),
    "username": os.environ.get("DEPLOY_USER", "root").strip() or "root",
    "password": os.environ.get("DEPLOY_PASSWORD", ""),
    "key_filename": os.environ.get("DEPLOY_KEY_FILE", "").strip(),
}

APP_DIR = "/opt/article-publisher"
BACKEND_PORT = 8001
NGINX_PORT = 8081


def check_env() -> None:
    """Validate required deployment configuration."""
    if not SERVER["host"]:
        print("[!] Error: set DEPLOY_HOST before running this script")
        print("    Example: DEPLOY_HOST=120.24.177.45 DEPLOY_KEY_FILE=/path/to/key.pem python deploy_remote.py")
        sys.exit(1)
    if not SERVER["password"] and not SERVER["key_filename"]:
        print("[!] Error: set DEPLOY_KEY_FILE or DEPLOY_PASSWORD before running this script")
        sys.exit(1)
    print(f"[*] Target server: {SERVER['host']}:{SERVER['port']} (user: {SERVER['username']})")


def connect_ssh() -> paramiko.SSHClient:
    """Create an SSH session using key auth first, then password if available."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    attempts: list[tuple[str, dict]] = []
    if SERVER["key_filename"]:
        attempts.append((
            "key",
            {
                "hostname": SERVER["host"],
                "port": SERVER["port"],
                "username": SERVER["username"],
                "key_filename": os.path.expanduser(SERVER["key_filename"]),
                "look_for_keys": False,
                "allow_agent": False,
                "timeout": 15,
            },
        ))
    if SERVER["password"]:
        attempts.append((
            "password",
            {
                "hostname": SERVER["host"],
                "port": SERVER["port"],
                "username": SERVER["username"],
                "password": SERVER["password"],
                "look_for_keys": False,
                "allow_agent": False,
                "timeout": 15,
            },
        ))

    last_error: Exception | None = None
    for method, kwargs in attempts:
        try:
            ssh.connect(**kwargs)
            print(f"  [+] Connected with {method} authentication")
            return ssh
        except Exception as exc:  # pragma: no cover - deployment helper
            last_error = exc

    print(f"[!] Connection failed: {last_error}")
    sys.exit(1)


def exec_raw(ssh: paramiko.SSHClient, command: str, timeout: int = 120) -> tuple[int, str, str]:
    """Execute an SSH command and return exit code, stdout, stderr."""
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, out, err


def run_cmd(
    ssh: paramiko.SSHClient,
    command: str,
    timeout: int = 120,
    check: bool = True,
) -> tuple[str, str]:
    """Execute a remote command, optionally raising when it fails."""
    exit_code, out, err = exec_raw(ssh, command, timeout=timeout)
    if err and "warning" not in err.lower():
        print(f"  [stderr] {err[:300]}")
    if check and exit_code != 0:
        raise RuntimeError(f"Remote command failed ({exit_code}): {command}\n{err or out}")
    return out, err


def remote_dir_exists(ssh: paramiko.SSHClient, path: str) -> bool:
    """Return True if the remote directory exists."""
    exit_code, _, _ = exec_raw(ssh, f"test -d {path}")
    return exit_code == 0


def detect_nginx_target(ssh: paramiko.SSHClient) -> tuple[str, list[str]]:
    """Return the nginx config target file and any follow-up commands."""
    if remote_dir_exists(ssh, "/etc/nginx/conf.d"):
        return "/etc/nginx/conf.d/article-publisher.conf", [
            "rm -f /etc/nginx/sites-enabled/article-publisher",
            "rm -f /etc/nginx/sites-available/article-publisher",
        ]

    if remote_dir_exists(ssh, "/etc/nginx/sites-available") and remote_dir_exists(ssh, "/etc/nginx/sites-enabled"):
        return "/etc/nginx/sites-available/article-publisher", [
            "ln -sf /etc/nginx/sites-available/article-publisher /etc/nginx/sites-enabled/article-publisher",
        ]

    raise RuntimeError("Unsupported nginx layout: neither conf.d nor sites-available/sites-enabled found")


def upload_dir(sftp, ssh: paramiko.SSHClient, local_dir: Path, remote_dir: str, exclude=None) -> None:
    """Recursively upload a directory to the target host."""
    if exclude is None:
        exclude = {"__pycache__", ".pyc", "node_modules", ".git", ".pytest_cache"}

    for item in local_dir.iterdir():
        name = item.name
        if name in exclude or name.startswith("."):
            continue

        remote_path = f"{remote_dir}/{name}"
        if item.is_dir():
            run_cmd(ssh, f"mkdir -p {remote_path}")
            upload_dir(sftp, ssh, item, remote_path, exclude)
        elif item.is_file():
            print(f"  {remote_path}")
            sftp.put(str(item), remote_path)


def deploy() -> None:
    """Deploy the local project to the configured remote server."""
    check_env()

    print("[*] Connecting to server...")
    ssh = connect_ssh()
    local_root = Path(__file__).resolve().parent.parent

    try:
        print("\n[1/7] Preparing remote directories...")
        run_cmd(ssh, f"mkdir -p {APP_DIR}/{{logs,data,output,backend,frontend/dist,deploy}}")

        print("\n[2/7] Uploading project files...")
        sftp = ssh.open_sftp()

        print("  Uploading backend/ ...")
        upload_dir(sftp, ssh, local_root / "backend", f"{APP_DIR}/backend")

        dist_dir = local_root / "frontend" / "dist"
        if dist_dir.exists() and any(dist_dir.iterdir()):
            print("  Uploading frontend/dist/ ...")
            upload_dir(sftp, ssh, dist_dir, f"{APP_DIR}/frontend/dist")
        else:
            print("  [!] Frontend build is missing. Run `cd frontend && npm run build` first.")
            sys.exit(1)

        print("  Uploading config.yaml ...")
        sftp.put(str(local_root / "config.yaml"), f"{APP_DIR}/config.yaml")

        print("  Uploading requirements.txt ...")
        sftp.put(str(local_root / "requirements.txt"), f"{APP_DIR}/requirements.txt")

        print("  Uploading deploy/ ...")
        upload_dir(sftp, ssh, local_root / "deploy", f"{APP_DIR}/deploy")
        sftp.close()

        print("\n[3/7] Setting up Python environment...")
        venv_exists = exec_raw(ssh, f"test -d {APP_DIR}/venv && echo EXISTS")[1]
        if venv_exists != "EXISTS":
            print("  Creating virtualenv ...")
            run_cmd(ssh, f"python3 -m venv {APP_DIR}/venv", timeout=60)
        else:
            print("  Virtualenv already exists, reusing it")

        print("  Installing dependencies ...")
        run_cmd(ssh, f"{APP_DIR}/venv/bin/pip install --upgrade pip", timeout=180)
        run_cmd(ssh, f"{APP_DIR}/venv/bin/pip install -r {APP_DIR}/requirements.txt", timeout=600)

        print("\n[4/7] Configuring system user...")
        run_cmd(ssh, "id -u article-publisher >/dev/null 2>&1 || useradd -r -s /bin/false article-publisher")
        run_cmd(ssh, f"chown -R article-publisher:article-publisher {APP_DIR}")

        print("\n[5/7] Updating systemd service...")
        service_content = f"""[Unit]
Description=Article Publisher API Service
After=network.target

[Service]
Type=simple
User=article-publisher
Group=article-publisher
WorkingDirectory={APP_DIR}/backend
Environment="PATH={APP_DIR}/venv/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONPATH={APP_DIR}/backend:{APP_DIR}"
ExecStart={APP_DIR}/venv/bin/uvicorn api:app \\
    --host 0.0.0.0 \\
    --port {BACKEND_PORT} \\
    --workers 1
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
MemoryMax=1G
MemoryHigh=800M
CPUQuota=150%
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={APP_DIR}

[Install]
WantedBy=multi-user.target
"""
        sftp = ssh.open_sftp()
        with sftp.file("/tmp/article-publisher.service", "w") as handle:
            handle.write(service_content)
        sftp.close()
        run_cmd(ssh, "mv /tmp/article-publisher.service /etc/systemd/system/article-publisher.service")
        run_cmd(ssh, "systemctl daemon-reload")

        print("\n[6/7] Updating Nginx config...")
        nginx_target, nginx_followups = detect_nginx_target(ssh)
        sftp = ssh.open_sftp()
        sftp.put(str(local_root / "deploy" / "nginx.conf"), "/tmp/article-publisher-nginx.conf")
        sftp.close()
        run_cmd(ssh, f"cp /tmp/article-publisher-nginx.conf {nginx_target}")
        for command in nginx_followups:
            run_cmd(ssh, command)
        nginx_out, nginx_err = run_cmd(ssh, "nginx -t")
        if "test is successful" in nginx_out or "test is successful" in nginx_err:
            print("  Nginx config test passed")
        else:
            print(f"  [!] Nginx config test returned unexpected output: {(nginx_err or nginx_out)[:300]}")
        run_cmd(ssh, "systemctl reload nginx")

        print("\n[7/7] Restarting service...")
        run_cmd(ssh, "systemctl restart article-publisher")
        time.sleep(3)
        status = exec_raw(ssh, "systemctl is-active article-publisher")[1]
        if status == "active":
            print("\n========================================")
            print("[+] Deployment succeeded")
            print("========================================")
            print(f"  Backend: http://{SERVER['host']}:{BACKEND_PORT}")
            print(f"  Nginx:   http://{SERVER['host']}:{NGINX_PORT}")
            api_check = exec_raw(ssh, f"curl -fsS http://localhost:{BACKEND_PORT}/api/status", timeout=30)[1]
            print(f"  /api/status -> {api_check[:160]}")
        else:
            print(f"\n[!] Service failed to start (status: {status})")
            logs = run_cmd(ssh, "journalctl -u article-publisher -n 80 --no-pager")[0]
            print(logs)
            sys.exit(1)
    finally:
        ssh.close()


if __name__ == "__main__":
    deploy()
