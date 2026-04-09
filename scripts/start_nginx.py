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

# 启动 Nginx
print("=== 启动 Nginx ===")
stdin, stdout, stderr = ssh.exec_command("systemctl start nginx && systemctl enable nginx")
print(stdout.read().decode())

# 检查状态
print("\n=== Nginx 状态 ===")
stdin, stdout, stderr = ssh.exec_command("systemctl status nginx")
print(stdout.read().decode())

print("\n=== 测试 Nginx ===")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8081 | head -20")
print(stdout.read().decode())

ssh.close()
