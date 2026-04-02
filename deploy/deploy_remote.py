#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程部署脚本 - 从 Windows 本地部署到服务器"""

import paramiko
import sys
from pathlib import Path


# 服务器配置
SERVER = {
    "host": "REDACTED",
    "port": 22,
    "username": "root",
    "password": "REDACTED",
}

APP_DIR = "/opt/article-publisher"


def run_ssh_command(ssh, command: str) -> str:
    """执行 SSH 命令并返回输出"""
    stdin, stdout, stderr = ssh.exec_command(command)
    output = stdout.read().decode()
    error = stderr.read().decode()
    if error and "WARNING" not in error:
        print(f"[!] Error: {error}")
    return output


def deploy():
    """远程部署"""
    print("[*] 连接服务器...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(**SERVER)

    try:
        # 1. 上传文件
        print("[1/5] 上传项目文件...")
        sftp = ssh.open_sftp()

        # 获取本地项目路径
        local_root = Path(__file__).parent.parent

        # 上传核心目录
        for dirname in ["backend", "database", "deploy"]:
            print(f"  上传 {dirname}/...")
            remote_path = f"{APP_DIR}/{dirname}"
            # 先创建远程目录
            run_ssh_command(ssh, f"mkdir -p {remote_path}")
            # 上传文件
            for local_file in (local_root / dirname).rglob("*"):
                if local_file.is_file() and "__pycache__" not in str(local_file):
                    rel_path = local_file.relative_to(local_root / dirname)
                    remote_file_path = f"{remote_path}/{rel_path}"
                    # 创建远程子目录
                    run_ssh_command(ssh, f"mkdir -p {remote_file_path.rsplit('/', 1)[0]}")
                    sftp.put(str(local_file), remote_file_path)

        # 上传 config.yaml
        print("  上传 config.yaml...")
        sftp.put(str(local_root / "config.yaml"), f"{APP_DIR}/config.yaml")

        sftp.close()

        # 2. 设置权限
        print("[2/5] 设置权限...")
        run_ssh_command(ssh, f"chown -R article-publisher:article-publisher {APP_DIR}")
        run_ssh_command(ssh, f"chmod -R 755 {APP_DIR}")

        # 3. 初始化数据库
        print("[3/5] 初始化数据库...")
        run_ssh_command(ssh, f"cd {APP_DIR} && source venv/bin/activate && python -m database.init_db")

        # 4. 配置 Nginx
        print("[4/5] 配置 Nginx...")
        run_ssh_command(ssh, f"cp {APP_DIR}/deploy/nginx.conf /etc/nginx/sites-available/article-publisher")
        run_ssh_command(ssh, f"ln -sf /etc/nginx/sites-available/article-publisher /etc/nginx/sites-enabled/")
        run_ssh_command(ssh, "nginx -t")
        run_ssh_command(ssh, "systemctl reload nginx")

        # 5. 重启服务
        print("[5/5] 重启服务...")
        run_ssh_command(ssh, "systemctl daemon-reload")
        run_ssh_command(ssh, "systemctl restart article-publisher")
        run_ssh_command(ssh, "sleep 2")

        # 检查状态
        status = run_ssh_command(ssh, "systemctl is-active article-publisher")
        if "active" in status:
            print("\n[+] 部署成功！")
            print(f"\n访问地址: http://{SERVER['host']}")
        else:
            print("\n[!] 部署失败，查看日志:")
            print(run_ssh_command(ssh, "journalctl -u article-publisher -n 50 --no-pager"))

    finally:
        ssh.close()


if __name__ == "__main__":
    deploy()
