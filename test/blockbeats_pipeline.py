#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlockBeats -> ChainThink 发布管道

抓取律动 BlockBeats 文章，上传封面+正文图片到 COS，推送到 ChainThink 后台。

用法:
  python blockbeats_pipeline.py https://www.theblockbeats.info/news/61745
  python blockbeats_pipeline.py https://www.theblockbeats.info/news/61745 --debug
"""

import re
import json
import time
import hmac
import hashlib
import tempfile
import os
import sys
import argparse
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================

API_URL = "https://api-v2.chainthink.cn/ccs/v1/admin/content/publish"
UPLOAD_API = "https://api-v2.chainthink.cn/ccs/v1/admin/upload_file"
API_TOKEN = "REDACTED"
X_USER_ID = "83"
X_APP_ID = "101"

CHAINTHINK_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json; charset=utf-8",
    "Origin": "https://admin.chainthink.cn",
    "Referer": "https://admin.chainthink.cn/",
    "User-Agent": "Mozilla/5.0",
    "x-token": API_TOKEN,
    "x-user-id": X_USER_ID,
    "X-App-Id": X_APP_ID,
}

ARTICLE_SOURCE_NAME = "律动 BlockBeats"

# 正文尾部截止关键词（遇到则截断，不推送到后台）
TAIL_CUT_TRIGGERS = [
    "点击了解律动BlockBeats 在招岗位",
    "欢迎加入律动 BlockBeats 官方社群",
    "Telegram 订阅群",
    "Telegram 交流群",
    "Twitter 官方账号",
]

http = requests.Session()
http.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"})


# ============================================================
# 工具函数
# ============================================================

def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def compute_crc64(path):
    import crcmod
    crc64_fn = crcmod.mkCrcFun(0x142F0E1EBA9EA3693, initCrc=0, xorOut=0xFFFFFFFFFFFFFFFF)
    with open(path, "rb") as f:
        return str(crc64_fn(f.read()))


def build_cos_authorization(secret_id, secret_key, method, host, path, content_length, sign_start, sign_end):
    key_time = f"{sign_start};{sign_end}"
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    http_string = f"{method.lower()}\n{path}\n\nhost={host}\n"
    string_to_sign = f"sha1\n{key_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}"
        f"&q-key-time={key_time}&q-header-list=host&q-url-param-list=&q-signature={signature}"
    )


# ============================================================
# 第一步：抓取文章
# ============================================================

def fetch_article(url, debug=False):
    """
    从 BlockBeats 文章页抓取标题、封面、正文 blocks。
    返回 dict: {title, cover_src, blocks: [{type:'p'|'img', text?, src?}], original_url}
    """
    r = http.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 标题
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""

    # 封面（og:image）
    og = soup.find("meta", property="og:image")
    cover_src = og["content"] if og else ""

    # 正文区域
    content = soup.find(class_="news-content")
    if not content:
        raise RuntimeError("页面中未找到 .news-content 元素")

    # 解析 blocks
    blocks = []
    for child in content.children:
        if not hasattr(child, "name") or not child.name:
            continue
        if child.name == "p":
            imgs = child.find_all("img")
            if imgs:
                for img in imgs:
                    src = img.get("src") or img.get("data-src") or ""
                    if src:
                        blocks.append({"type": "img", "src": src})
            else:
                text = child.get_text(strip=True)
                if text:
                    blocks.append({"type": "p", "text": text})
        elif child.name == "img":
            src = child.get("src") or child.get("data-src") or ""
            if src:
                blocks.append({"type": "img", "src": src})

    # 截断尾部钩子
    for i, b in enumerate(blocks):
        if b["type"] == "p":
            for trigger in TAIL_CUT_TRIGGERS:
                if trigger in b["text"]:
                    blocks = blocks[:i]
                    break
        if i >= len(blocks):
            break

    article = {
        "title": title,
        "source": ARTICLE_SOURCE_NAME,
        "cover_src": cover_src,
        "blocks": blocks,
        "original_url": url,
    }

    if debug:
        img_count = sum(1 for b in blocks if b["type"] == "img")
        print(f"[fetch] title={title[:40]}...")
        print(f"[fetch] cover={cover_src[:60]}...")
        print(f"[fetch] blocks={len(blocks)} ({len(blocks)-img_count} text + {img_count} img)")

    return article


# ============================================================
# 第二步：上传图片到 COS
# ============================================================

def request_upload(file_name, file_hash, use_pre_sign_url=True, confirm=False):
    payload = {
        "file_name": file_name,
        "hash": file_hash,
        "use_pre_sign_url": use_pre_sign_url,
        "confirm": confirm,
    }
    r = requests.post(UPLOAD_API, headers=CHAINTHINK_HEADERS,
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=30)
    r.raise_for_status()
    return r.json()


def upload_image(image_url, label="", debug=False):
    """
    下载图片 -> 请求 COS 凭证 -> PUT 上传 -> confirm -> 返回 confirm_url
    """
    if not image_url:
        return ""

    # 下载图片
    img_resp = http.get(image_url, timeout=60)
    img_resp.raise_for_status()
    content = img_resp.content
    ctype = img_resp.headers.get("content-type", "").lower()
    if "png" in ctype or image_url.lower().endswith(".png"):
        ext = "png"
    elif "webp" in ctype or image_url.lower().endswith(".webp"):
        ext = "webp"
    else:
        ext = "jpg"
    content_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"

    # 写临时文件算 hash
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        file_hash = compute_crc64(tmp_path)

        # Step 1: 请求上传凭证
        resp = request_upload(f"cover.{ext}", file_hash, use_pre_sign_url=True, confirm=False)
        key = resp.get("data", {}).get("key", {})

        pre_sign_url = key.get("pre_sign_url", "")
        object_key = key.get("object_key", "")
        access_key_id = key.get("access_key_id", "")
        access_key_secret = key.get("access_key_secret", "")
        security_token = key.get("security_token", "")
        bucket = key.get("bucket_name", "")
        region = key.get("region", "")
        expiration_str = key.get("expiration", "")

        if debug:
            print(f"  {label} hash={file_hash} obj={object_key[:40]}")

        # Step 2: PUT 上传
        if pre_sign_url and not access_key_id:
            # 预签名 URL 模式
            r = requests.put(pre_sign_url, data=content, headers={"Content-Type": content_type}, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"pre-sign PUT failed: {r.status_code} {r.text[:200]}")
        elif access_key_id and access_key_secret and security_token and bucket and region and object_key:
            # COS 临时密钥模式
            host = f"{bucket}.cos.{region}.myqcloud.com"
            path = f"/{object_key.lstrip('/')}"
            url = f"https://{host}{path}"
            try:
                from datetime import datetime as dt
                exp_ts = int(dt.fromisoformat(expiration_str.replace("Z", "+00:00")).timestamp())
            except Exception:
                exp_ts = int(time.time()) + 3600
            now_ts = int(time.time())
            sign_start = min(now_ts, exp_ts)
            sign_end = exp_ts
            if sign_end <= sign_start:
                sign_start = max(sign_end - 60, 0)
            auth = build_cos_authorization(
                access_key_id, access_key_secret, "PUT", host, path, len(content), sign_start, sign_end
            )
            headers = {
                "Authorization": auth,
                "x-cos-security-token": security_token,
                "Content-Type": content_type,
                "Content-Length": str(len(content)),
                "Host": host,
            }
            r = requests.put(url, headers=headers, data=content, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"COS PUT failed: {r.status_code} {r.text[:200]}")
        else:
            raise RuntimeError(f"no valid upload method: keys={list(key.keys())}")

        # Step 3: confirm
        confirm_resp = request_upload(f"cover.{ext}", file_hash, use_pre_sign_url=False, confirm=True)
        confirm_data = confirm_resp.get("data", {})
        file_info = confirm_data.get("file_info", {})
        confirm_url = file_info.get("confirm_url", "")

        if confirm_url:
            if debug:
                print(f"  {label} confirm OK")
            return confirm_url

        # Fallback
        domain = file_info.get("domain", "https://cos.chainthink.cn")
        obj = file_info.get("object", object_key)
        if obj:
            return f"{domain.rstrip('/')}/{obj.lstrip('/')}"

        raise RuntimeError(f"confirm returned no URL: {json.dumps(confirm_resp, ensure_ascii=False)[:300]}")

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ============================================================
# 第三步：构建 HTML + 发布
# ============================================================

def build_html(article):
    parts = [f"<p><strong>来源：</strong>{html_escape(article['source'] or '')}</p>"]
    for block in article["blocks"]:
        if block.get("type") == "img" and block.get("src"):
            uploaded = article.get("uploaded_images", {}).get(block["src"], block["src"])
            parts.append(f'<p><img src="{html_escape(uploaded)}" alt="" /></p>')
        elif block.get("type") == "p" and block.get("text"):
            parts.append(f"<p>{html_escape(block['text'])}</p>")
    return "".join(parts)


def build_abstract(article):
    texts = [b["text"].strip() for b in article["blocks"] if b.get("type") == "p" and b.get("text")]
    return re.sub(r"\s+", " ", " ".join(texts))[:180]


def publish(article, max_retries=3, debug=False):
    cover_image = article.get("uploaded_cover", "")
    payload = {
        "id": "0",
        "info": {"cover_image": cover_image} if cover_image else {},
        "is_translate": True,
        "translation": {
            "zh-CN": {
                "title": article["title"],
                "text": build_html(article),
                "abstract": build_abstract(article),
            }
        },
        "type": 5,
        "admin_detail": {},
        "strong_content_tags": {},
        "chain_is_calendar": False,
        "chain_calendar_time": int(time.time()),
        "chain_calendar_tendency": 0,
        "is_push_bian": 2,
        "content_pin_top": 0,
        "is_public": False,
        "user_id": "3",
        "chain_fixed_publish_time": 0,
        "as_user_id": "3",
        "is_chain": True,
        "chain_airdrop_time": 0,
        "chain_airdrop_time_end": 0,
    }

    for attempt in range(max_retries):
        try:
            r = requests.post(
                API_URL,
                headers=CHAINTHINK_HEADERS,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=60,
            )
            data = r.json()
            if r.status_code == 200 and data.get("code") == 0:
                return {"cms_id": data["data"]["id"]}
            raise RuntimeError(f"publish failed: {r.status_code} {data}")
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 3
                if debug:
                    print(f"  publish attempt {attempt+1} failed, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise


# ============================================================
# 主流程
# ============================================================

def run(url, debug=False):
    """完整流程：抓取 -> 上传封面+图片 -> 发布"""

    # 1. 抓取
    print("1. Fetching article...")
    article = fetch_article(url, debug=debug)
    img_blocks = [b for b in article["blocks"] if b["type"] == "img"]
    print(f"   Title: {article['title']}")
    print(f"   Cover: {article['cover_src'][:80]}...")
    print(f"   Blocks: {len(article['blocks'])} ({len(article['blocks'])-len(img_blocks)} text + {len(img_blocks)} img)")

    # 2. 上传封面
    print("\n2. Uploading cover...")
    if article["cover_src"]:
        try:
            article["uploaded_cover"] = upload_image(article["cover_src"], label="[cover]", debug=debug)
            print(f"   OK: {article['uploaded_cover'][:60]}...")
        except Exception as e:
            print(f"   FAILED: {e}")
            article["uploaded_cover"] = ""
    else:
        article["uploaded_cover"] = ""

    # 3. 上传正文图片
    print("\n3. Uploading inline images...")
    article["uploaded_images"] = {}
    for i, block in enumerate(article["blocks"]):
        if block["type"] == "img" and block.get("src"):
            label = f"[img-{i}]"
            try:
                uploaded = upload_image(block["src"], label=label, debug=debug)
                article["uploaded_images"][block["src"]] = uploaded
                print(f"   {label} OK")
            except Exception as e:
                print(f"   {label} FAILED: {e}")

    uploaded_count = len(article["uploaded_images"])
    print(f"   Total: {uploaded_count}/{len(img_blocks)} uploaded")

    # 4. 发布
    print("\n4. Publishing to ChainThink...")
    result = publish(article, debug=debug)

    # 汇总
    print(f"\n{'='*40}")
    print(f"Title:      {article['title']}")
    print(f"CMS ID:     {result['cms_id']}")
    print(f"Cover:      {'yes' if article.get('uploaded_cover') else 'no'}")
    print(f"Images:     {uploaded_count}/{len(img_blocks)}")
    print(f"URL:        {url}")

    return result


def main():
    parser = argparse.ArgumentParser(description="BlockBeats -> ChainThink pipeline")
    parser.add_argument("url", help="BlockBeats article URL, e.g. https://www.theblockbeats.info/news/61745")
    parser.add_argument("--debug", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if "theblockbeats.info/news/" not in args.url:
        print(f"Error: URL should be a BlockBeats article page")
        sys.exit(1)

    run(args.url, debug=args.debug)


if __name__ == "__main__":
    main()
