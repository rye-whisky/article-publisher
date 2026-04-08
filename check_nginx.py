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

# 检查端口监听
print("=== 端口监听 ===")
stdin, stdout, stderr = ssh.exec_command("netstat -tlnp | grep -E '8001|8081'")
print(stdout.read().decode())

# 检查 Nginx 状态
print("\n=== Nginx 状态 ===")
stdin, stdout, stderr = ssh.exec_command("systemctl status nginx")
print(stdout.read().decode())

# 检查 Nginx 配置
print("\n=== Nginx 配置 ===")
stdin, stdout, stderr = ssh.exec_command("cat /etc/nginx/conf.d/article-publisher.conf")
print(stdout.read().decode())

# 测试后端连接
print("\n=== 测试后端连接 ===")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8001/api/status")
print(stdout.read().decode())

ssh.close()
