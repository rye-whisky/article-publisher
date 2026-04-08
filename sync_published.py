#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从本地数据库导出已发表文章并同步到服务器"""

import json
import sqlite3
import paramiko
import os

# 服务器配置
SERVER = {
    "host": "REDACTED",
    "port": 22,
    "username": "root",
    "key_filename": "F:\\sshkey\\REDACTED",
}

LOCAL_DB = "H:/article-publisher/data/articles.db"


def main():
    # 导出本地已发表文章
    print("[*] 从本地数据库导出已发表文章...")
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT article_id, source_key, title, cover_src, abstract,
               author, source, original_url, published_at, cms_id
        FROM articles
        WHERE cms_id IS NOT NULL AND cms_id != ''
        ORDER BY published_at DESC
    """)

    articles = []
    for row in cursor.fetchall():
        articles.append({
            "article_id": row["article_id"],
            "source_key": row["source_key"],
            "title": row["title"],
            "cover_src": row["cover_src"],
            "abstract": row["abstract"],
            "author": row["author"],
            "source": row["source"],
            "original_url": row["original_url"],
            "published_at": row["published_at"],
            "cms_id": row["cms_id"],
        })

    conn.close()
    print(f"[*] 找到 {len(articles)} 篇已发表文章")

    if not articles:
        print("[!] 没有找到已发表的文章")
        return

    # 显示前几篇
    print("\n 前 5 篇文章：")
    for i, art in enumerate(articles[:5], 1):
        print(f"  {i}. {art['title'][:60]}... (ID: {art['article_id']})")

    # 连接服务器并同步
    print(f"\n[*] 准备同步 {len(articles)} 篇文章到服务器...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=SERVER["host"],
        port=SERVER["port"],
        username=SERVER["username"],
        key_filename=SERVER["key_filename"],
        timeout=15,
    )

    # 上传数据
    json_data = json.dumps(articles, ensure_ascii=False)
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/sync_articles.json", "w") as f:
        f.write(json_data)
    sftp.close()

    # 执行同步
    sync_cmd = """
cd /opt/article-publisher && /opt/article-publisher/venv/bin/python3 -c "
import sqlite3
import json

conn = sqlite3.connect('data/articles.db')
cursor = conn.cursor()

with open('/tmp/sync_articles.json', 'r', encoding='utf-8') as f:
    articles = json.load(f)

synced = 0
for art in articles:
    article_id = art['article_id']
    # 检查是否已存在
    cursor.execute('SELECT id FROM articles WHERE article_id = ?', (article_id,))
    if cursor.fetchone():
        continue

    # 插入文章
    cursor.execute('''
        INSERT INTO articles (
            article_id, source_key, title, cover_src, abstract,
            author, source, original_url, published_at, cms_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')
    ''', (
        article_id, art['source_key'], art['title'], art['cover_src'],
        art['abstract'], art['author'], art['source'], art['original_url'],
        art['published_at'], art['cms_id']
    ))
    synced += 1

conn.commit()
print(f'同步完成: 新增 {synced} 篇文章')
conn.close()
"
    """

    stdin, stdout, stderr = ssh.exec_command(sync_cmd, timeout=120)
    result = stdout.read().decode()
    error = stderr.read().decode()
    print(result)
    if error:
        print("[stderr]", error)

    # 清理
    ssh.exec_command("rm -f /tmp/sync_articles.json")
    ssh.close()
    print("[+] 同步完成！")


if __name__ == "__main__":
    main()
