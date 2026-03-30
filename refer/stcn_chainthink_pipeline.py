#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import json
import re
import os
import subprocess
import tempfile
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

API_URL = "https://api-v2.chainthink.cn/ccs/v1/admin/content/publish"
API_TOKEN = "REDACTED"
X_USER_ID = "83"
X_APP_ID = "101"

ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = ROOT / "data" / "stcn_chainthink_state.json"
STCN_SOURCE_DIR = ROOT / "output" / "stcn_articles"
TECHFLOW_SOURCE_DIR = ROOT / "output" / "techflow_articles"

STCN_LIST_URL = "https://www.stcn.com/article/wx/qszg.html"
TECHFLOW_LIST_URL = "https://www.techflowpost.com/zh-CN/article"
ALLOWED_AUTHORS = {"沐阳", "周乐"}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json; charset=utf-8",
    "Origin": "https://admin.chainthink.cn",
    "Referer": "https://admin.chainthink.cn/",
    "User-Agent": "Mozilla/5.0",
    "x-token": API_TOKEN,
    "x-user-id": X_USER_ID,
    "X-App-Id": X_APP_ID,
}

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

session = requests.Session()
session.headers.update(HTTP_HEADERS)


def load_state():
    if not STATE_FILE.exists():
        return {"published_ids": [], "updated_at": None}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def html_escape(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;'))


def fetch_html(url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == 'iso-8859-1':
        r.encoding = r.apparent_encoding or 'utf-8'
    return r.text


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def extract_article_body_markdown(text: str) -> str:
    if "---" in text:
        parts = text.split("---", 1)
        if len(parts) > 1:
            text = parts[1]
    text = re.sub(r'^SECURITY NOTICE:[\s\S]*?<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\nSource: Web Fetch\n---\n', '', text)
    text = re.sub(r'\n<<<END_EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\s*$', '', text)
    return text.strip()


def parse_stcn_list():
    html = fetch_html(STCN_LIST_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    items = []
    # 通过详情链接去重，再用周边文本匹配作者时间
    seen = set()
    for a in soup.select('a[href*="/article/detail/"]'):
        href = a.get('href') or ''
        if '/article/detail/' not in href:
            continue
        full_url = urljoin(STCN_LIST_URL, href)
        m = re.search(r'/detail/(\d+)\.html', full_url)
        if not m:
            continue
        article_id = m.group(1)
        if article_id in seen:
            continue
        seen.add(article_id)
        title = (a.get_text(" ", strip=True) or '').strip()
        if not title or len(title) > 120:
            continue
        # 扩大搜索范围，使用整个 li 元素或 .content 元素
        article_block = a.find_parent('li') or a.find_parent(class_='content') or a.parent
        block = article_block.get_text(" ", strip=True) if article_block else (a.parent.get_text(" ", strip=True) if a.parent else text)
        author_match = re.search(r'券商中国\s+(沐阳|周乐)\s+(\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2})', block)
        if not author_match:
            continue
        author = author_match.group(1)
        time_text = author_match.group(2)
        publish_time = f"{today_str()} {time_text}" if re.fullmatch(r'\d{2}:\d{2}', time_text) else f"2026-{time_text.replace(' ', ' ')}"
        items.append({
            "article_id": article_id,
            "title": title,
            "author": author,
            "publish_time": publish_time,
            "original_url": full_url,
            "source": "券商中国",
        })
    return items


def clean_stcn_body(md_text: str) -> str:
    body = extract_article_body_markdown(md_text)
    body = body.replace('\r\n', '\n')

    stop_patterns = [
        r'\n\s*排版[：:]',
        r'\n\s*校对[：:]',
        r'\n\s*责编[：:]',
        r'\n\s*责任编辑[：:]',
        r'\n\s*声明[：:]',
        r'\n\s*版权声明[：:]',
        r'\n\s*转载声明[：:]',
        r'\n\s*风险提示[：:]',
        r'\n\s*下载[“"]?证券时报[”"]?官方APP',
        r'\n\s*(?:或)?关注官方微信(?:公众)?号',
        r'\n\s*微信编辑器',
    ]
    cut_positions = []
    for pattern in stop_patterns:
        m = re.search(pattern, body, flags=re.IGNORECASE)
        if m:
            cut_positions.append(m.start())
    if cut_positions:
        body = body[:min(cut_positions)]

    lines = []
    for line in body.split('\n'):
        line = line.strip()
        if not line:
            lines.append('')
            continue
        if re.match(r'^(来源|作者|原标题)\s*[：:]', line):
            continue
        if re.search(r'下载[“"]?证券时报[”"]?官方APP|关注官方微信(?:公众)?号|不构成实质性投资建议|据此操作风险自担', line):
            continue
        lines.append(line)

    body = '\n'.join(lines)
    body = re.sub(r'\n{3,}', '\n\n', body)
    return body.strip()


def extract_stcn_body_from_soup(soup: BeautifulSoup) -> str:
    candidates = []
    selectors = [
        'div.detail-content',
        'div.detail-content-wrapper',
        'article',
        'div[class*="detail"]',
        'div[class*="article"]',
    ]
    seen = set()
    for selector in selectors:
        for node in soup.select(selector):
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            paragraphs = []
            for el in node.find_all(['p', 'h2', 'h3', 'li']):
                text = el.get_text(' ', strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if not text:
                    continue
                paragraphs.append(text)
            if not paragraphs:
                continue
            score = len(paragraphs)
            joined = '\n\n'.join(paragraphs)
            if re.search(r'排版[：:]|校对[：:]|声明[：:]', joined):
                score += 3
            if len(joined) > 500:
                score += 3
            candidates.append((score, joined))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return soup.get_text('\n', strip=True)


def fetch_stcn_detail(item):
    html = fetch_html(item["original_url"])
    soup = BeautifulSoup(html, "html.parser")
    text = extract_stcn_body_from_soup(soup)
    body = clean_stcn_body(text)
    return {
        **item,
        "source_key": "stcn",
        "article_id_full": f"stcn:{item['article_id']}",
        "blocks": blocks_from_plain_text(body),
    }


def build_stcn_item_from_url(url: str, author: str = '', publish_time: str = ''):
    m = re.search(r'/detail/(\d+)\.html', url)
    if not m:
        raise ValueError(f'invalid stcn detail url: {url}')
    article_id = m.group(1)

    # 优先用列表页元数据回填作者/时间，避免详情页编码/结构波动导致作者识别失败
    try:
        for item in parse_stcn_list():
            if item.get('article_id') == article_id:
                return {
                    'article_id': article_id,
                    'title': item.get('title') or article_id,
                    'author': author or item.get('author', ''),
                    'publish_time': publish_time or item.get('publish_time', ''),
                    'original_url': url,
                    'source': item.get('source', '券商中国'),
                }
    except Exception:
        pass

    html = fetch_html(url)
    soup = BeautifulSoup(html, 'html.parser')
    title_node = soup.find('h1')
    title = title_node.get_text(' ', strip=True) if title_node else (soup.title.get_text(' ', strip=True) if soup.title else article_id)
    text = soup.get_text('\n', strip=True)
    if not author:
        m_author = re.search(r'券商中国\s*(沐阳|周乐)', text)
        if m_author:
            author = m_author.group(1)
    if not publish_time:
        m_time = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}:\d{2})', text)
        if m_time:
            time_text = m_time.group(1)
            publish_time = f"{today_str()} {time_text}" if re.fullmatch(r'\d{2}:\d{2}', time_text) else (f"2026-{time_text}" if re.fullmatch(r'\d{2}-\d{2}\s+\d{2}:\d{2}', time_text) else time_text)
    return {
        'article_id': article_id,
        'title': title,
        'author': author,
        'publish_time': publish_time,
        'original_url': url,
        'source': '券商中国',
    }


def blocks_from_plain_text(body: str):
    blocks = []
    for p in re.split(r'\n\s*\n', body):
        p = p.strip()
        if not p:
            continue
        if len(p) <= 24 and all(mark not in p for mark in ['。', '！', '？']):
            blocks.append({"type": "h2", "text": p})
        else:
            blocks.append({"type": "p", "text": p})
    return blocks


def save_stcn_article(article):
    STCN_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    path = STCN_SOURCE_DIR / f"{today_str()}_{article['article_id']}_{sanitize_filename(article['title'])}.md"
    body = "\n\n".join([b['text'] for b in article['blocks'] if b['type'] != 'img'])
    content = f"# {article['title']}\n\n**来源**：{article['source']}\n**作者**：{article['author']}\n**发布时间**：{article['publish_time']}\n**原文链接**：{article['original_url']}\n\n---\n\n{body}\n"
    path.write_text(content, encoding='utf-8')
    return path


def parse_techflow_list():
    html = fetch_html(TECHFLOW_LIST_URL)
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    seen = set()
    for a in soup.select('a[href*="/zh-CN/article/"]'):
        href = a.get('href') or ''
        full_url = urljoin(TECHFLOW_LIST_URL, href)
        m = re.search(r'/article/(\d+)', full_url)
        if not m:
            continue
        article_id = m.group(1)
        if article_id in seen:
            continue
        seen.add(article_id)
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        title = re.sub(r'^\d{4}\.\d{2}\.\d{2}(?:-\s*\d+小时前)?', '', text).strip()
        title = re.sub(r'^(原创)?', '', title).strip()
        items.append({
            "article_id": article_id,
            "title": title[:120],
            "original_url": full_url,
            "source": "深潮 TechFlow",
        })
    return items


def is_techflow_leadin_text(text: str) -> bool:
    text = re.sub(r'\s+', ' ', (text or '').strip())
    if not text:
        return True
    leadin_patterns = [
        r'^作者\s*[：:].*$',
        r'^撰文\s*[：:].*$',
        r'^编译\s*[：:].*$',
        r'^深潮导读\s*[：:]?.*$',
        r'^TechFlow Selected\s*深潮精选$',
        r'^By\s+.+$',
        r'^Written by\s+.+$',
        r'^Author\s*[：:].*$',
        r'^译者\s*[：:].*$',
    ]
    return any(re.match(p, text, flags=re.IGNORECASE) for p in leadin_patterns)


def is_techflow_hook_text(text: str) -> bool:
    text = (text or '').strip()
    hook_patterns = [
        r'欢迎加入深潮\s*TechFlow官方社群',
        r'^Telegram订阅群\s*[：:]',
        r'^Twitter官方账号\s*[：:]',
        r'^Twitter英文账号\s*[：:]',
        r't\.me/TechFlowDaily',
        r'x\.com/TechFlowPost',
        r'x\.com/BlockFlow_News',
        r'关注.*深潮',
        r'加入.*社群',
    ]
    return any(re.search(p, text, flags=re.IGNORECASE) for p in hook_patterns)


def fetch_techflow_detail(item):
    html = fetch_html(item['original_url'])
    soup = BeautifulSoup(html, 'html.parser')
    article = soup.find('article') or soup.find('main') or soup.body
    title = soup.find('h1').get_text(" ", strip=True) if soup.find('h1') else item['title']
    blocks = []
    cover_src = ''
    stop_text = '找到这些创始人——那些不符合本地 VC 体系优化出来的「标准简历模板」的人——是我们现在在做的事。'
    for el in article.find_all(['h2', 'h3', 'p', 'img']):
        if el.name == 'img':
            src = el.get('src') or ''
            if src and src.startswith('http'):
                if not cover_src:
                    cover_src = src
                blocks.append({"type": "img", "src": src, "alt": el.get('alt', '')})
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if text in {title, 'TechFlow Selected 深潮精选'}:
            continue
        if is_techflow_leadin_text(text):
            continue
        if is_techflow_hook_text(text):
            break
        blocks.append({"type": el.name, "text": text})
        if text == stop_text:
            break
    while blocks and blocks[-1].get('type') != 'img' and is_techflow_hook_text(blocks[-1].get('text', '')):
        blocks.pop()
    return {
        "source_key": "techflow",
        "article_id_full": f"techflow:{item['article_id']}",
        "article_id": item['article_id'],
        "title": title,
        "author": '',
        "source": item['source'],
        "publish_time": today_str(),
        "original_url": item['original_url'],
        "cover_src": cover_src,
        "blocks": blocks,
    }


def save_techflow_article(article):
    TECHFLOW_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    path = TECHFLOW_SOURCE_DIR / f"techflow_{article['article_id']}.json"
    path.write_text(json.dumps({
        "article_id": article['article_id'],
        "title": article['title'],
        "source": article['source'],
        "original_url": article['original_url'],
        "cover_src": article.get('cover_src', ''),
        "blocks": article['blocks'],
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def parse_stcn_article_file(path: Path):
    text = path.read_text(encoding='utf-8')
    title = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    author = re.search(r'\*\*作者\*\*：(.+)', text)
    source = re.search(r'\*\*来源\*\*：(.+)', text)
    publish_time = re.search(r'\*\*发布时间\*\*：(.+)', text)
    original_url = re.search(r'\*\*原文链接\*\*：(.+)', text)
    article_id_match = re.search(r'/detail/(\d+)\.html', text)
    body = text.split('---', 1)[1] if '---' in text else text
    return {
        'source_key': 'stcn',
        'article_id': f"stcn:{article_id_match.group(1) if article_id_match else path.stem}",
        'raw_id': article_id_match.group(1) if article_id_match else path.stem,
        'title': title.group(1).strip() if title else path.stem,
        'author': author.group(1).strip() if author else '',
        'source': source.group(1).strip() if source else '券商中国',
        'publish_time': publish_time.group(1).strip() if publish_time else '',
        'original_url': original_url.group(1).strip() if original_url else '',
        'blocks': blocks_from_plain_text(clean_stcn_body(body)),
        'path': str(path),
    }


def parse_techflow_article_file(path: Path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    return {
        'source_key': 'techflow',
        'article_id': f"techflow:{data['article_id']}",
        'raw_id': str(data['article_id']),
        'title': data['title'],
        'author': data.get('author', ''),
        'source': data.get('source', '深潮 TechFlow'),
        'publish_time': data.get('publish_time', ''),
        'original_url': data.get('original_url', ''),
        'cover_src': data.get('cover_src', ''),
        'blocks': data.get('blocks', []),
        'path': str(path),
    }


def article_time_ok(article, since_today_0700=False):
    if not since_today_0700 or article['source_key'] != 'stcn':
        return True
    pt = article.get('publish_time', '')
    m = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', pt)
    if not m:
        return False
    date_part, hm = m.groups()
    return date_part == today_str() and hm >= '07:00'


def build_html(article):
    parts = [f"<p><strong>来源：</strong>{html_escape(article['source'] or '')}</p>"]
    cover_src = article.get('cover_src', '') or ''
    for block in article['blocks']:
        if block.get('type') == 'img' and block.get('src'):
            src = block['src']
            if cover_src and src == cover_src:
                continue
            parts.append(f'<p><img src="{html_escape(src)}" alt="{html_escape(block.get("alt", ""))}" /></p>')
            continue
        text = (block.get('text') or '').strip()
        if not text:
            continue
        tag = block.get('type', 'p')
        if tag not in ['p', 'h2', 'h3']:
            tag = 'p'
        parts.append(f'<{tag}>{html_escape(text)}</{tag}>')
    return ''.join(parts)


def build_abstract(article):
    texts = [b['text'].strip() for b in article['blocks'] if b.get('type') != 'img' and b.get('text')]
    return re.sub(r'\s+', ' ', ' '.join(texts))[:180]


def request_cover_upload(file_name: str, file_hash: str, use_pre_sign_url: bool = False, confirm: bool = False):
    api = 'https://api-v2.chainthink.cn/ccs/v1/admin/upload_file'
    payload = {
        'file_name': file_name,
        'hash': file_hash,
        'use_pre_sign_url': use_pre_sign_url,
        'confirm': confirm,
    }
    r = requests.post(api, headers=HEADERS, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=30)
    data = r.json()
    if r.status_code == 200 and data.get('code') == 0:
        upload_data = data['data']
        key_data = upload_data.get('key', {})
        if key_data:
            merged = dict(upload_data)
            merged.update(key_data)
            return merged
        return upload_data
    raise RuntimeError(f'cover upload request failed: {r.status_code} {data}')


def compute_crc64_hash(file_path: str):
    js_path = Path(tempfile.gettempdir()) / 'chainthink_crc64.js'
    if not js_path.exists():
        js = session.get('https://admin.chainthink.cn/crc64.js', timeout=60)
        js.raise_for_status()
        js_path.write_text(js.text, encoding='utf-8')
    node_script = (
        "const fs=require('fs'); const vm=require('vm');"
        "const code=fs.readFileSync(process.argv[1],'utf8');"
        "const buf=fs.readFileSync(process.argv[2]);"
        "const ctx={console,TextEncoder,TextDecoder,Uint8Array,ArrayBuffer,DataView,Int32Array,Uint32Array,Buffer,process,require,module:{exports:{}},exports:{}};"
        "ctx.window=ctx; ctx.self=ctx; ctx.global=ctx; vm.createContext(ctx); vm.runInContext(code, ctx);"
        "process.stdout.write(String(ctx.CRC64.crc64(buf)));"
    )
    out = subprocess.check_output(['node', '-e', node_script, str(js_path), file_path], text=True)
    return out.strip()


def _hmac_sha1(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha1).digest()


def build_cos_authorization(secret_id: str, secret_key: str, method: str, host: str, path: str, content_length: int, sign_start: int, sign_end: int):
    key_time = f"{sign_start};{sign_end}"
    sign_key = hmac.new(secret_key.encode('utf-8'), key_time.encode('utf-8'), hashlib.sha1).hexdigest()
    http_string = f"{method.lower()}\n{path}\n\ncontent-length={content_length}&host={host.lower()}\n"
    sha1_http_string = hashlib.sha1(http_string.encode('utf-8')).hexdigest()
    string_to_sign = f"sha1\n{key_time}\n{sha1_http_string}\n"
    signature = hmac.new(bytes.fromhex(sign_key), string_to_sign.encode('utf-8'), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}&q-key-time={key_time}"
        f"&q-header-list=content-length;host&q-url-param-list=&q-signature={signature}"
    )


def parse_unix_timestamp(value):
    if value is None or value == '':
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return int(float(text))
    iso_text = text.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(iso_text)
    except ValueError as exc:
        raise RuntimeError(f'invalid expiration format: {value}') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def put_file_to_cos(upload: dict, content: bytes):
    file_info = upload.get('file_info', {})

    # 如果是已存在对象，直接返回 confirm_url
    confirm_url = file_info.get('confirm_url') or upload.get('confirm_url') or ''
    if confirm_url:
        return ''

    bucket = upload.get('bucket_name') or file_info.get('bucket_name')
    region = upload.get('region') or file_info.get('region')
    object_key = file_info.get('object') or file_info.get('object_key') or upload.get('object') or upload.get('object_key')
    pre_sign_url = upload.get('pre_sign_url') or file_info.get('pre_sign_url') or ''

    content_type = 'image/jpeg' if object_key and (object_key.lower().endswith('.jpg') or object_key.lower().endswith('.jpeg')) else ('image/png' if object_key and object_key.lower().endswith('.png') else 'image/webp')

    # 如果没有 object_key，说明是已存在对象，直接跳过上传
    if not object_key:
        return ''

    if pre_sign_url:
        headers = {
            'Content-Type': content_type,
            'Content-Length': str(len(content)),
            'Host': f"{bucket}.cos.{region}.myqcloud.com" if bucket and region else '',
        }
        headers = {k: v for k, v in headers.items() if v}
        r = requests.put(pre_sign_url, headers=headers, data=content, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'cos put failed: {r.status_code} {r.text}')
        return pre_sign_url.split('?', 1)[0]

    secret_id = upload.get('access_key_id')
    secret_key = upload.get('access_key_secret')
    security_token = upload.get('security_token')
    expiration = parse_unix_timestamp(upload.get('expiration'))
    if not all([bucket, region, object_key, secret_id, secret_key, security_token, expiration]):
        raise RuntimeError(f'incomplete cos upload credentials: {upload}')
    host = f"{bucket}.cos.{region}.myqcloud.com"
    path = f"/{object_key.lstrip('/')}"
    url = f"https://{host}{path}"
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sign_start = min(now_ts, expiration)
    sign_end = expiration
    if sign_end <= sign_start:
        sign_start = max(sign_end - 60, 0)
    authorization = build_cos_authorization(secret_id, secret_key, 'PUT', host, path, len(content), sign_start, sign_end)
    headers = {
        'Authorization': authorization,
        'x-cos-security-token': security_token,
        'Content-Type': content_type,
        'Content-Length': str(len(content)),
        'Origin': 'https://admin.chainthink.cn',
        'Referer': 'https://admin.chainthink.cn/',
        'Host': host,
    }
    r = requests.put(url, headers=headers, data=content, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'cos put failed: {r.status_code} {r.text}')
    return url


def upload_cover_from_url(image_url: str, debug: bool = False):
    if not image_url:
        return ''
    img = session.get(image_url, timeout=60)
    img.raise_for_status()
    ext = 'jpg'
    ctype = img.headers.get('content-type', '').lower()
    if 'webp' in ctype or image_url.lower().endswith('.webp'):
        ext = 'webp'
    elif 'png' in ctype or image_url.lower().endswith('.png'):
        ext = 'png'
    with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp:
        tmp.write(img.content)
        tmp_path = tmp.name
    try:
        file_hash = compute_crc64_hash(tmp_path)
        upload = request_cover_upload(f'cover.{ext}', file_hash, use_pre_sign_url=True, confirm=False)
        file_info = upload.get('file_info', {})

        # 合并 data.key 的内容（新对象场景）
        key_data = upload.get('key', {})
        if key_data:
            # 只覆盖非空的值
            for k, v in key_data.items():
                if v not in (None, '', []):
                    upload[k] = v
            file_info = upload.get('file_info', {})

        if debug:
            print(json.dumps({'debug_upload': upload}, ensure_ascii=False, indent=2))

        has_cos_target = bool(
            upload.get('pre_sign_url') or (
                upload.get('access_key_id') and
                upload.get('access_key_secret') and
                upload.get('security_token') and
                upload.get('bucket_name') and
                upload.get('region') and
                upload.get('expiration')
            )
        )
        uploaded_to_cos = False
        if has_cos_target:
            put_file_to_cos(upload, img.content)
            uploaded_to_cos = True

        # 获取 object_key（可能在 confirm 之后使用）
        object_key = file_info.get('object') or file_info.get('object_key') or upload.get('object') or upload.get('object_key') or ''

        confirm_url = file_info.get('confirm_url') or upload.get('confirm_url') or ''
        if confirm_url:
            return confirm_url

        # 如果上传了到 COS，需要调用 confirm 接口
        if uploaded_to_cos and object_key:
            try:
                confirm_upload = request_cover_upload(f'cover.{ext}', file_hash, use_pre_sign_url=False, confirm=True)
                confirm_url = confirm_upload.get('file_info', {}).get('confirm_url') or confirm_upload.get('confirm_url') or ''
                if confirm_url:
                    return confirm_url
            except Exception:
                pass  # 忽略 confirm 错误，继续使用构建的 URL

        domain = file_info.get('domain') or upload.get('domain') or 'https://cos.chainthink.cn'
        if object_key:
            return f"{domain.rstrip('/')}/{object_key.lstrip('/')}"

        returned_hash = file_info.get('hash') or upload.get('hash') or file_hash
        returned_ext = file_info.get('ext') or upload.get('ext') or ext
        return f"https://cos.chainthink.cn/{X_APP_ID}_admin_file/{returned_hash}/{returned_hash}.{returned_ext}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def publish(article):
    cover_image = ''
    cover_error = ''
    if article.get('source_key') == 'techflow' and article.get('cover_src'):
        try:
            cover_image = upload_cover_from_url(article.get('cover_src', '') or '')
        except Exception as exc:
            cover_error = str(exc)
            cover_image = ''
    payload = {
        'id': '0',
        'info': {'cover_image': cover_image} if cover_image else {},
        'is_translate': True,
        'translation': {'zh-CN': {'title': article['title'], 'text': build_html(article), 'abstract': build_abstract(article)}},
        'type': 5,
        'admin_detail': {},
        'strong_content_tags': {},
        'chain_is_calendar': False,
        'chain_calendar_time': int(datetime.now().timestamp()),
        'chain_calendar_tendency': 0,
        'is_push_bian': 2,
        'content_pin_top': 0,
        'is_public': False,
        'user_id': '3',
        'chain_fixed_publish_time': 0,
        'as_user_id': '3',
        'is_chain': True,
        'chain_airdrop_time': 0,
        'chain_airdrop_time_end': 0,
    }
    r = requests.post(API_URL, headers=HEADERS, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=30)
    data = r.json()
    if r.status_code == 200 and data.get('code') == 0:
        return {'cms_id': data['data']['id'], 'cover_image': cover_image, 'cover_error': cover_error}
    raise RuntimeError(f"publish failed: {r.status_code} {data}")


def ingest_sources(source, state):
    fetched = []
    published_ids = set(state.get('published_ids', []))
    if source in ('stcn', 'all'):
        for item in parse_stcn_list():
            if item['author'] not in ALLOWED_AUTHORS:
                continue
            article_id_full = f"stcn:{item['article_id']}"
            if article_id_full in published_ids:
                continue
            article = fetch_stcn_detail(item)
            save_stcn_article(article)
            fetched.append(article_id_full)
    if source in ('techflow', 'all'):
        existing = {p.stem.replace('techflow_', '') for p in TECHFLOW_SOURCE_DIR.glob('techflow_*.json')} if TECHFLOW_SOURCE_DIR.exists() else set()
        for item in parse_techflow_list():
            article_id_full = f"techflow:{item['article_id']}"
            if article_id_full in published_ids or item['article_id'] in existing:
                continue
            article = fetch_techflow_detail(item)
            save_techflow_article(article)
            fetched.append(article_id_full)
    return fetched


def refetch_targets(source, stcn_urls=None, techflow_ids=None):
    refreshed = []
    stcn_urls = stcn_urls or []
    techflow_ids = techflow_ids or []

    if source in ('stcn', 'all'):
        for url in stcn_urls:
            item = build_stcn_item_from_url(url)
            article = fetch_stcn_detail(item)
            path = save_stcn_article(article)
            refreshed.append({'id': article['article_id_full'], 'path': str(path)})

    if source in ('techflow', 'all'):
        techflow_items = {item['article_id']: item for item in parse_techflow_list()}
        for article_id in techflow_ids:
            item = techflow_items.get(str(article_id)) or {
                'article_id': str(article_id),
                'title': str(article_id),
                'original_url': f'https://www.techflowpost.com/zh-CN/article/{article_id}',
                'source': '深潮 TechFlow',
            }
            article = fetch_techflow_detail(item)
            path = save_techflow_article(article)
            refreshed.append({'id': article['article_id_full'], 'path': str(path)})

    return refreshed


def load_articles(source):
    articles = []
    if source in ('stcn', 'all') and STCN_SOURCE_DIR.exists():
        for f in sorted(STCN_SOURCE_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True):
            articles.append(parse_stcn_article_file(f))
    if source in ('techflow', 'all') and TECHFLOW_SOURCE_DIR.exists():
        for f in sorted(TECHFLOW_SOURCE_DIR.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            articles.append(parse_techflow_article_file(f))
    return articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', choices=['stcn', 'techflow', 'all'], default='all')
    parser.add_argument('--since-today-0700', action='store_true')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--republish', nargs='*', default=[])
    parser.add_argument('--skip-fetch', action='store_true')
    parser.add_argument('--refetch-stcn-url', nargs='*', default=[])
    parser.add_argument('--refetch-techflow-id', nargs='*', default=[])
    args = parser.parse_args()

    state = load_state()
    refreshed = []
    refetch_mode = bool(args.refetch_stcn_url or args.refetch_techflow_id)
    if refetch_mode:
        refreshed = refetch_targets(args.source, args.refetch_stcn_url, args.refetch_techflow_id)
    elif not args.skip_fetch:
        ingest_sources(args.source, state)

    published_ids = set(state.get('published_ids', []))
    republish_ids = set(args.republish or [])

    if refetch_mode and not republish_ids:
        save_state(state)
        print(json.dumps({'ok': True, 'refetched': refreshed, 'published': [], 'skipped': []}, ensure_ascii=False, indent=2))
        return

    articles = load_articles(args.source)

    accepted, skipped = [], []
    for article in articles:
        if article['source_key'] == 'stcn' and article['author'] not in ALLOWED_AUTHORS:
            skipped.append({'id': article['article_id'], 'reason': 'author'})
            continue
        if args.since_today_0700 and not article_time_ok(article, True):
            skipped.append({'id': article['article_id'], 'reason': 'time'})
            continue
        if article['article_id'] in published_ids and article['article_id'] not in republish_ids:
            skipped.append({'id': article['article_id'], 'reason': 'already_published'})
            continue
        accepted.append(article)

    source_order = []
    for key in ['stcn', 'techflow']:
        if args.source in (key, 'all'):
            source_order.append(key)
    accepted.sort(key=lambda article: (source_order.index(article['source_key']) if article['source_key'] in source_order else 999, article.get('publish_time', '')), reverse=False)

    published = []
    failed = []
    for source_key in source_order:
        source_articles = [article for article in accepted if article['source_key'] == source_key]
        for article in source_articles:
            try:
                publish_result = publish(article)
                published.append({
                    'article_id': article['article_id'],
                    'cms_id': publish_result['cms_id'],
                    'title': article['title'],
                    'cover_image': publish_result.get('cover_image', ''),
                    'cover_error': publish_result.get('cover_error', ''),
                })
                published_ids.add(article['article_id'])
            except Exception as e:
                failed.append({'id': article['article_id'], 'reason': 'publish_failed', 'error': str(e), 'source': source_key})
                continue

    state['published_ids'] = sorted(published_ids)
    save_state(state)
    print(json.dumps({'ok': True, 'refetched': refreshed, 'published': published, 'skipped': skipped, 'failed': failed}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
