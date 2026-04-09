#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从旧服务器下载数据库并上传到新服务器"""

import paramiko
import os

OLD_SERVER = {
    "host": "REDACTED",
    "port": 22,
    "username": "root",
    "password": "REDACTED",
}

NEW_SERVER = {
    "host": "REDACTED",
    "port": 22,
    "username": "root",
    "key_filename": "F:\\sshkey\\REDACTED",
}

DB_PATH = "/opt/article-publisher/data/articles.db"
TEMP_PATH = "/tmp/articles.db"


def download_db():
    """从旧服务器下载数据库"""
    print("[*] 从旧服务器下载数据库...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=OLD_SERVER["host"],
        port=OLD_SERVER["port"],
        username=OLD_SERVER["username"],
        password=OLD_SERVER["password"],
        timeout=15,
    )

    # 检查数据库
    stdin, stdout, stderr = ssh.exec_command(f"ls -lh {DB_PATH}")
    print(stdout.read().decode())

    # 下载数据库
    print(f"[*] 下载数据库...")
    sftp = ssh.open_sftp()
    sftp.get(DB_PATH, TEMP_PATH)
    sftp.close()
    ssh.close()

    print(f"[+] 数据库已下载到: {TEMP_PATH}")


def upload_db():
    """上传数据库到新服务器"""
    print("\n[*] 上传数据库到新服务器...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=NEW_SERVER["host"],
        port=NEW_SERVER["port"],
        username=NEW_SERVER["username"],
        key_filename=NEW_SERVER["key_filename"],
        timeout=15,
    )

    # 停止服务
    print("[*] 停止服务...")
    ssh.exec_command("systemctl stop article-publisher")

    # 备份现有数据库
    stdin, stdout, stderr = ssh.exec_command(f"cp {DB_PATH} {DB_PATH}.backup 2>/dev/null || true")
    print(stdout.read().decode())

    # 上传数据库
    print(f"[*] 上传数据库...")
    sftp = ssh.open_sftp()
    sftp.put(TEMP_PATH, DB_PATH)
    sftp.close()

    # 设置权限
    ssh.exec_command(f"chown article-publisher:article-publisher {DB_PATH}")

    # 重启服务
    print("[*] 重启服务...")
    ssh.exec_command("systemctl start article-publisher")

    # 检查状态
    stdin, stdout, stderr = ssh.exec_command("systemctl is-active article-publisher")
    status = stdout.read().decode().strip()
    if "active" in status:
        print("[+] 服务已启动")
    else:
        print("[!] 服务启动失败")
        print(stderr.read().decode())

    ssh.close()
    print("[+] 数据库迁移完成！")


def main():
    try:
        download_db()
        upload_db()
    except Exception as e:
        print(f"[!] 迁移失败: {e}")
    finally:
        # 清理本地临时文件
        if os.path.exists(TEMP_PATH):
            os.remove(TEMP_PATH)
            print(f"[*] 已清理临时文件")


if __name__ == "__main__":
    main()
