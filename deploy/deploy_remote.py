#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程部署脚本 - 从 Windows 本地部署到阿里云服务器"""

import os
import sys
import time
import paramiko
from pathlib import Path


# 服务器配置 - 密钥从环境变量读取
SERVER = {
    "host": os.environ.get("DEPLOY_HOST", ""),
    "port": int(os.environ.get("DEPLOY_PORT", "22")),
    "username": os.environ.get("DEPLOY_USER", ""),
    "password": os.environ.get("DEPLOY_PASSWORD", ""),
    "key_filename": os.environ.get("DEPLOY_KEY_FILE", ""),
}

APP_DIR = "/opt/article-publisher"
BACKEND_PORT = 8001
NGINX_PORT = 8081
PYTHON_BIN = "python3.11"  # 服务器上的 Python 版本


def check_env():
    """检查环境变量"""
    if not SERVER["password"]:
        print("[!] 错误: 请设置环境变量 DEPLOY_PASSWORD")
        print("    用法: DEPLOY_PASSWORD=xxx python deploy_remote.py")
        sys.exit(1)
    print(f"[*] 目标服务器: {SERVER['host']}:{SERVER['port']} (用户: {SERVER['username']})")


def run_cmd(ssh, command: str, timeout: int = 120) -> tuple[str, str]:
    """执行 SSH 命令，返回 (stdout, stderr)"""
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err and "WARNING" not in err:
        print(f"  [stderr] {err[:200]}")
    return out, err


def upload_dir(sftp, ssh, local_dir: Path, remote_dir: str, exclude=None):
    """递归上传目录"""
    if exclude is None:
        exclude = {"__pycache__", ".pyc", "node_modules", ".git"}

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


def deploy():
    """远程部署主流程"""
    check_env()

    # 连接服务器
    print("[*] 连接服务器...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 尝试多种认证方式
    connected = False
    last_error = None

    # 1. 尝试指定密钥文件
    if SERVER["key_filename"]:
        try:
            key_path = os.path.expanduser(SERVER["key_filename"])
            ssh.connect(
                hostname=SERVER["host"],
                port=SERVER["port"],
                username=SERVER["username"],
                key_filename=key_path,
                timeout=15,
            )
            connected = True
            print("  [+] 使用指定密钥认证成功")
        except Exception as e:
            last_error = e

    # 2. 尝试密码认证
    if not connected and SERVER["password"]:
        try:
            ssh.connect(
                hostname=SERVER["host"],
                port=SERVER["port"],
                username=SERVER["username"],
                password=SERVER["password"],
                timeout=15,
            )
            connected = True
            print("  [+] 使用密码认证成功")
        except Exception as e:
            last_error = e

    if not connected:
        print(f"[!] 连接失败: {last_error}")
        sys.exit(1)

    local_root = Path(__file__).resolve().parent.parent

    try:
        # ===== Step 1: 创建目录 =====
        print("\n[1/7] 创建远程目录...")
        run_cmd(ssh, f"mkdir -p {APP_DIR}/{{logs,data,output,backend,frontend/dist,deploy}}")

        # ===== Step 2: 上传文件 =====
        print("\n[2/7] 上传项目文件...")
        sftp = ssh.open_sftp()

        # 上传 backend/
        print("  上传 backend/...")
        upload_dir(sftp, ssh, local_root / "backend", f"{APP_DIR}/backend")

        # 上传 frontend/dist/ (必须已 build)
        dist_dir = local_root / "frontend" / "dist"
        if dist_dir.exists() and any(dist_dir.iterdir()):
            print("  上传 frontend/dist/...")
            upload_dir(sftp, ssh, dist_dir, f"{APP_DIR}/frontend/dist")
        else:
            print("  [!] 前端未构建！请先运行: cd frontend && npm run build")
            sys.exit(1)

        # 上传 config.yaml
        print("  上传 config.yaml...")
        sftp.put(str(local_root / "config.yaml"), f"{APP_DIR}/config.yaml")

        # 上传 requirements.txt
        print("  上传 requirements.txt...")
        sftp.put(str(local_root / "requirements.txt"), f"{APP_DIR}/requirements.txt")

        # 上传 deploy/ (nginx.conf, logging.ini)
        print("  上传 deploy/...")
        upload_dir(sftp, ssh, local_root / "deploy", f"{APP_DIR}/deploy")

        sftp.close()

        # ===== Step 3: Python 虚拟环境 =====
        print("\n[3/7] 设置 Python 环境...")
        # 重建虚拟环境（使用 python3.11）
        print("  创建虚拟环境...")
        run_cmd(ssh, f"rm -rf {APP_DIR}/venv")
        run_cmd(ssh, f"{PYTHON_BIN} -m venv {APP_DIR}/venv", timeout=60)

        print("  升级 pip...")
        run_cmd(ssh, f"{APP_DIR}/venv/bin/pip install --upgrade pip", timeout=120)

        print("  安装依赖...")
        run_cmd(ssh, f"{APP_DIR}/venv/bin/pip install -r {APP_DIR}/requirements.txt", timeout=300)

        # ===== Step 4: 创建系统用户 =====
        print("\n[4/7] 配置系统用户...")
        run_cmd(ssh, "id -u article-publisher &>/dev/null || useradd -r -s /bin/false article-publisher")
        run_cmd(ssh, f"chown -R article-publisher:article-publisher {APP_DIR}")

        # ===== Step 5: systemd 服务 =====
        print("\n[5/7] 配置 systemd 服务...")
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
ExecStart={APP_DIR}/venv/bin/uvicorn api:app \\
    --host 0.0.0.0 \\
    --port {BACKEND_PORT} \\
    --workers 1
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# 资源限制
MemoryMax=1G
MemoryHigh=800M
CPUQuota=150%

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={APP_DIR} {APP_DIR}/backend

[Install]
WantedBy=multi-user.target
"""
        # 写入 service 文件
        sftp = ssh.open_sftp()
        with sftp.file("/tmp/article-publisher.service", "w") as f:
            f.write(service_content)
        sftp.close()
        run_cmd(ssh, "mv /tmp/article-publisher.service /etc/systemd/system/article-publisher.service")
        run_cmd(ssh, "systemctl daemon-reload")

        # ===== Step 6: Nginx 配置 =====
        print("\n[6/7] 配置 Nginx...")
        # 上传 nginx 配置
        sftp = ssh.open_sftp()
        nginx_src = str(local_root / "deploy" / "nginx.conf")
        sftp.put(nginx_src, "/tmp/article-publisher-nginx.conf")
        sftp.close()

        run_cmd(ssh, "cp /tmp/article-publisher-nginx.conf /etc/nginx/conf.d/article-publisher.conf")
        test_result = run_cmd(ssh, "nginx -t")[1]
        if "test is successful" not in test_result and "syntax is ok" not in test_result:
            print(f"  [!] Nginx 配置测试失败: {test_result}")
        else:
            print("  Nginx 配置测试通过")
        run_cmd(ssh, "systemctl reload nginx")

        # ===== Step 7: 启动服务 =====
        print("\n[7/7] 启动服务...")
        run_cmd(ssh, "systemctl restart article-publisher")
        time.sleep(3)

        # 检查状态
        status = run_cmd(ssh, "systemctl is-active article-publisher")[0]
        if "active" in status:
            print(f"\n{'='*40}")
            print(f"[+] 部署成功！")
            print(f"{'='*40}")
            print(f"  后端: http://{SERVER['host']}:{BACKEND_PORT}")
            print(f"  Nginx: http://{SERVER['host']}:{NGINX_PORT}")
            print(f"  API 状态: http://{SERVER['host']}:{BACKEND_PORT}/api/status")
            print(f"  前端: http://{SERVER['host']}:{NGINX_PORT}")

            # 验证 API
            print(f"\n验证 API...")
            api_check = run_cmd(ssh, f"curl -s http://localhost:{BACKEND_PORT}/api/status")[0]
            print(f"  /api/status → {api_check[:100]}")
        else:
            print(f"\n[!] 服务启动失败 (status: {status})")
            print("查看日志:")
            logs = run_cmd(ssh, "journalctl -u article-publisher -n 30 --no-pager")[0]
            print(logs)

    finally:
        ssh.close()


if __name__ == "__main__":
    deploy()
