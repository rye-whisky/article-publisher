#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import paramiko
import os

SERVER = {
    "host": "REDACTED",
    "port": 22,
    "username": "root",
    "key_filename": "F:\\sshkey\\REDACTED",
}

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    hostname=SERVER["host"],
    port=SERVER["port"],
    username=SERVER["username"],
    key_filename=SERVER["key_filename"],
    timeout=15,
)

# 检查服务状态
stdin, stdout, stderr = ssh.exec_command("systemctl status article-publisher")
print(stdout.read().decode())

print("\n=== 最近日志 ===")
stdin, stdout, stderr = ssh.exec_command("journalctl -u article-publisher -n 50 --no-pager")
print(stdout.read().decode())

ssh.close()
