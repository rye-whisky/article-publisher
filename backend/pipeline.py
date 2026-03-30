#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChainThink Article Publisher Pipeline.

Core business logic: fetch, clean, deduplicate, publish articles from
STCN and TechFlow to the ChainThink CMS platform.

Can be used via CLI or imported as a module by the FastAPI backend.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from crc64_js import compute_crc64_file

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _expand_env(value):
    if not isinstance(value, str):
        return value
    return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ''), value)


def _expand_recursive(obj):
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_recursive(v) for v in obj]
    return obj


def load_config(base_dir: Path) -> dict:
    config_path = base_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {config_path}")
    with open(config_path, encoding='utf-8') as f:
        raw = yaml.safe_load(f)
    return _expand_recursive(raw)


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class Pipeline:
    """Encapsulates all pipeline state and operations."""

    def __init__(self, base_dir: Path = None):
        if base_dir is None:
            # Default: project root (parent of backend/)
            base_dir = Path(__file__).resolve().parent.parent
        self.base_dir = base_dir
        self.cfg = load_config(base_dir)

        # Derived paths
        self.state_file = base_dir / self.cfg['paths']['state_file']
        self.stcn_dir = base_dir / self.cfg['paths']['stcn_output']
        self.techflow_dir = base_dir / self.cfg['paths']['techflow_output']

        # Config constants
        self.api_url = self.cfg['chainthink']['api_url']
        self.upload_url = self.cfg['chainthink']['upload_url']
        self.x_user_id = str(self.cfg['chainthink']['user_id'])
        self.x_app_id = str(self.cfg['chainthink']['app_id'])
        self.allowed_authors = set(self.cfg['sources']['stcn'].get('allowed_authors', []))
        self.stcn_cfg = self.cfg['sources']['stcn']
        self.techflow_cfg = self.cfg['sources']['techflow']

        # API headers
        self.api_headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://admin.chainthink.cn",
            "Referer": "https://admin.chainthink.cn/",
            "User-Agent": "Mozilla/5.0",
            "x-token": self.cfg['chainthink']['token'],
            "x-user-id": self.x_user_id,
            "X-App-Id": self.x_app_id,
        }

        # HTTP session with retry
        retry_cfg = self.cfg.get('retry', {})
        retry = Retry(
            total=retry_cfg.get('max_retries', 3),
            backoff_factor=retry_cfg.get('backoff_factor', 1),
            status_forcelist=retry_cfg.get('status_forcelist', [500, 502, 503, 504]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    # -- State --

    def load_state(self):
        if not self.state_file.exists():
            return {"published_ids": [], "updated_at": None}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save_state(self, state):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.now().isoformat()
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- Helpers --

    @staticmethod
    def html_escape(s):
        return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))

    def fetch_html(self, url):
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == 'iso-8859-1':
            r.encoding = r.apparent_encoding or 'utf-8'
        return r.text

    @staticmethod
    def today_str():
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def sanitize_filename(name):
        return re.sub(r'[\\/:*?"<>|]', '_', name)

    # -- STCN --

    def parse_stcn_list(self):
        list_url = self.stcn_cfg['list_url']
        html = self.fetch_html(list_url)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        items = []
        seen = set()
        for a in soup.select('a[href*="/article/detail/"]'):
            href = a.get('href') or ''
            if '/article/detail/' not in href:
                continue
            full_url = urljoin(list_url, href)
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
            article_block = a.find_parent('li') or a.find_parent(class_='content') or a.parent
            block = article_block.get_text(" ", strip=True) if article_block else (a.parent.get_text(" ", strip=True) if a.parent else text)
            author_match = re.search(r'券商中国\s+(沐阳|周乐)\s+(\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2})', block)
            if not author_match:
                continue
            author = author_match.group(1)
            time_text = author_match.group(2)
            publish_time = f"{self.today_str()} {time_text}" if re.fullmatch(r'\d{2}:\d{2}', time_text) else f"2026-{time_text.replace(' ', ' ')}"
            items.append({
                "article_id": article_id, "title": title, "author": author,
                "publish_time": publish_time, "original_url": full_url, "source": "券商中国",
            })
        log.info("STCN list: found %d articles", len(items))
        return items

    @staticmethod
    def clean_stcn_body(md_text):
        body = md_text.replace('\r\n', '\n')
        if "---" in body:
            body = body.split("---", 1)[-1]
        body = re.sub(r'^SECURITY NOTICE:[\s\S]*?<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\nSource: Web Fetch\n---\n', '', body)
        body = re.sub(r'\n<<<END_EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>\s*$', '', body)

        stop_patterns = [
            r'\n\s*排版[：:]', r'\n\s*校对[：:]', r'\n\s*责编[：:]', r'\n\s*责任编辑[：:]',
            r'\n\s*声明[：:]', r'\n\s*版权声明[：:]', r'\n\s*转载声明[：:]', r'\n\s*风险提示[：:]',
            r'\n\s*下载["""]?证券时报["""]?官方APP', r'\n\s*(?:或)?关注官方微信(?:公众)?号', r'\n\s*微信编辑器',
        ]
        cut_positions = [m.start() for p in stop_patterns for m in [re.search(p, body, flags=re.IGNORECASE)] if m]
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
            if re.search(r'下载["""]?证券时报["""]?官方APP|关注官方微信(?:公众)?号|不构成实质性投资建议|据此操作风险自担', line):
                continue
            lines.append(line)
        body = '\n'.join(lines)
        return re.sub(r'\n{3,}', '\n\n', body).strip()

    @staticmethod
    def extract_stcn_body_from_soup(soup):
        candidates = []
        selectors = ['div.detail-content', 'div.detail-content-wrapper', 'article', 'div[class*="detail"]', 'div[class*="article"]']
        seen = set()
        for selector in selectors:
            for node in soup.select(selector):
                nid = id(node)
                if nid in seen:
                    continue
                seen.add(nid)
                paragraphs = [re.sub(r'\s+', ' ', el.get_text(' ', strip=True)).strip()
                               for el in node.find_all(['p', 'h2', 'h3', 'li'])
                               if el.get_text(' ', strip=True).strip()]
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

    @staticmethod
    def blocks_from_plain_text(body):
        blocks = []
        for p in re.split(r'\n\s*\n', body):
            p = p.strip()
            if not p:
                continue
            if len(p) <= 24 and all(m not in p for m in ['。', '！', '？']):
                blocks.append({"type": "h2", "text": p})
            else:
                blocks.append({"type": "p", "text": p})
        return blocks

    def fetch_stcn_detail(self, item):
        html = self.fetch_html(item["original_url"])
        soup = BeautifulSoup(html, "html.parser")
        text = self.extract_stcn_body_from_soup(soup)
        body = self.clean_stcn_body(text)
        return {**item, "source_key": "stcn", "article_id_full": f"stcn:{item['article_id']}", "blocks": self.blocks_from_plain_text(body)}

    def save_stcn_article(self, article):
        self.stcn_dir.mkdir(parents=True, exist_ok=True)
        path = self.stcn_dir / f"{self.today_str()}_{article['article_id']}_{self.sanitize_filename(article['title'])}.md"
        body = "\n\n".join([b['text'] for b in article['blocks'] if b['type'] != 'img'])
        content = f"# {article['title']}\n\n**来源**：{article['source']}\n**作者**：{article['author']}\n**发布时间**：{article['publish_time']}\n**原文链接**：{article['original_url']}\n\n---\n\n{body}\n"
        path.write_text(content, encoding='utf-8')
        return path

    def build_stcn_item_from_url(self, url, author='', publish_time=''):
        m = re.search(r'/detail/(\d+)\.html', url)
        if not m:
            raise ValueError(f'invalid stcn detail url: {url}')
        article_id = m.group(1)
        try:
            for item in self.parse_stcn_list():
                if item.get('article_id') == article_id:
                    return {'article_id': article_id, 'title': item.get('title') or article_id,
                            'author': author or item.get('author', ''), 'publish_time': publish_time or item.get('publish_time', ''),
                            'original_url': url, 'source': item.get('source', '券商中国')}
        except Exception:
            pass
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, 'html.parser')
        title_node = soup.find('h1')
        title = title_node.get_text(' ', strip=True) if title_node else (soup.title.get_text(' ', strip=True) if soup.title else article_id)
        text = soup.get_text('\n', strip=True)
        if not author:
            ma = re.search(r'券商中国\s*(沐阳|周乐)', text)
            if ma:
                author = ma.group(1)
        if not publish_time:
            mt = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}:\d{2})', text)
            if mt:
                t = mt.group(1)
                publish_time = f"{self.today_str()} {t}" if re.fullmatch(r'\d{2}:\d{2}', t) else (f"2026-{t}" if re.fullmatch(r'\d{2}-\d{2}\s+\d{2}:\d{2}', t) else t)
        return {'article_id': article_id, 'title': title, 'author': author, 'publish_time': publish_time, 'original_url': url, 'source': '券商中国'}

    # -- TechFlow --

    def parse_techflow_list(self):
        list_url = self.techflow_cfg['list_url']
        html = self.fetch_html(list_url)
        soup = BeautifulSoup(html, 'html.parser')
        items = []
        seen = set()
        for a in soup.select('a[href*="/zh-CN/article/"]'):
            href = a.get('href') or ''
            full_url = urljoin(list_url, href)
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
            items.append({"article_id": article_id, "title": title[:120], "original_url": full_url, "source": "深潮 TechFlow"})
        log.info("TechFlow list: found %d articles", len(items))
        return items

    @staticmethod
    def is_techflow_leadin_text(text):
        text = re.sub(r'\s+', ' ', (text or '').strip())
        if not text:
            return True
        return any(re.match(p, text, flags=re.IGNORECASE) for p in [
            r'^作者\s*[：:].*$', r'^撰文\s*[：:].*$', r'^编译\s*[：:].*$', r'^深潮导读\s*[：:]?.*$',
            r'^TechFlow Selected\s*深潮精选$', r'^By\s+.+$', r'^Written by\s+.+$', r'^Author\s*[：:].*$', r'^译者\s*[：:].*$',
        ])

    @staticmethod
    def is_techflow_hook_text(text):
        text = (text or '').strip()
        return any(re.search(p, text, flags=re.IGNORECASE) for p in [
            r'欢迎加入深潮\s*TechFlow官方社群', r'^Telegram订阅群\s*[：:]', r'^Twitter官方账号\s*[：:]',
            r'^Twitter英文账号\s*[：:]', r't\.me/TechFlowDaily', r'x\.com/TechFlowPost',
            r'x\.com/BlockFlow_News', r'关注.*深潮', r'加入.*社群',
        ])

    def fetch_techflow_detail(self, item):
        html = self.fetch_html(item['original_url'])
        soup = BeautifulSoup(html, 'html.parser')
        article = soup.find('article') or soup.find('main') or soup.body
        title = soup.find('h1').get_text(" ", strip=True) if soup.find('h1') else item['title']
        blocks, cover_src = [], ''
        for el in article.find_all(['h2', 'h3', 'p', 'img']):
            if el.name == 'img':
                src = el.get('src') or ''
                if src and src.startswith('http'):
                    if not cover_src:
                        cover_src = src
                    blocks.append({"type": "img", "src": src, "alt": el.get('alt', '')})
                continue
            text = el.get_text(" ", strip=True)
            if not text or text in {title, 'TechFlow Selected 深潮精选'} or self.is_techflow_leadin_text(text):
                continue
            if self.is_techflow_hook_text(text):
                break
            blocks.append({"type": el.name, "text": text})
        while blocks and blocks[-1].get('type') != 'img' and self.is_techflow_hook_text(blocks[-1].get('text', '')):
            blocks.pop()
        return {"source_key": "techflow", "article_id_full": f"techflow:{item['article_id']}",
                "article_id": item['article_id'], "title": title, "author": '', "source": item['source'],
                "publish_time": self.today_str(), "original_url": item['original_url'], "cover_src": cover_src, "blocks": blocks}

    def save_techflow_article(self, article):
        self.techflow_dir.mkdir(parents=True, exist_ok=True)
        path = self.techflow_dir / f"techflow_{article['article_id']}.json"
        path.write_text(json.dumps({
            "article_id": article['article_id'], "title": article['title'], "source": article['source'],
            "original_url": article['original_url'], "cover_src": article.get('cover_src', ''), "blocks": article['blocks'],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    # -- Article file parsers --

    def parse_stcn_article_file(self, path):
        text = path.read_text(encoding='utf-8')
        title = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        author = re.search(r'\*\*作者\*\*：(.+)', text)
        source = re.search(r'\*\*来源\*\*：(.+)', text)
        publish_time = re.search(r'\*\*发布时间\*\*：(.+)', text)
        original_url = re.search(r'\*\*原文链接\*\*：(.+)', text)
        aid = re.search(r'/detail/(\d+)\.html', text)
        body = text.split('---', 1)[1] if '---' in text else text
        return {'source_key': 'stcn', 'article_id': f"stcn:{aid.group(1) if aid else path.stem}",
                'raw_id': aid.group(1) if aid else path.stem, 'title': title.group(1).strip() if title else path.stem,
                'author': author.group(1).strip() if author else '', 'source': source.group(1).strip() if source else '券商中国',
                'publish_time': publish_time.group(1).strip() if publish_time else '',
                'original_url': original_url.group(1).strip() if original_url else '',
                'blocks': self.blocks_from_plain_text(self.clean_stcn_body(body)), 'path': str(path)}

    @staticmethod
    def parse_techflow_article_file(path):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return {'source_key': 'techflow', 'article_id': f"techflow:{data['article_id']}",
                'raw_id': str(data['article_id']), 'title': data['title'], 'author': data.get('author', ''),
                'source': data.get('source', '深潮 TechFlow'), 'publish_time': data.get('publish_time', ''),
                'original_url': data.get('original_url', ''), 'cover_src': data.get('cover_src', ''),
                'blocks': data.get('blocks', []), 'path': str(path)}

    # -- HTML builder --

    def build_html(self, article):
        parts = [f"<p><strong>来源：</strong>{self.html_escape(article['source'] or '')}</p>"]
        cover_src = article.get('cover_src', '') or ''
        for block in article['blocks']:
            if block.get('type') == 'img' and block.get('src'):
                if cover_src and block['src'] == cover_src:
                    continue
                parts.append(f'<p><img src="{self.html_escape(block["src"])}" alt="{self.html_escape(block.get("alt", ""))}" /></p>')
                continue
            text = (block.get('text') or '').strip()
            if not text:
                continue
            tag = block.get('type', 'p') if block.get('type') in ['p', 'h2', 'h3'] else 'p'
            parts.append(f'<{tag}>{self.html_escape(text)}</{tag}>')
        return ''.join(parts)

    @staticmethod
    def build_abstract(article):
        texts = [b['text'].strip() for b in article['blocks'] if b.get('type') != 'img' and b.get('text')]
        return re.sub(r'\s+', ' ', ' '.join(texts))[:180]

    # -- Cover upload --

    def request_cover_upload(self, file_name, file_hash, use_pre_sign_url=False, confirm=False):
        payload = {'file_name': file_name, 'hash': file_hash, 'use_pre_sign_url': use_pre_sign_url, 'confirm': confirm}
        r = requests.post(self.upload_url, headers=self.api_headers, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=30)
        data = r.json()
        if r.status_code == 200 and data.get('code') == 0:
            upload_data = data['data']
            key_data = upload_data.get('key', {})
            if key_data:
                # Bug fix: only merge non-empty values from key_data
                merged = dict(upload_data)
                for k, v in key_data.items():
                    if v not in (None, '', []):
                        merged[k] = v
                return merged
            return upload_data
        raise RuntimeError(f'cover upload request failed: {r.status_code} {data}')

    @staticmethod
    def _build_cos_auth(secret_id, secret_key, method, host, path, content_length, sign_start, sign_end):
        key_time = f"{sign_start};{sign_end}"
        sign_key = hmac.new(secret_key.encode('utf-8'), key_time.encode('utf-8'), hashlib.sha1).hexdigest()
        http_string = f"{method.lower()}\n{path}\n\ncontent-length={content_length}&host={host.lower()}\n"
        sha1_http = hashlib.sha1(http_string.encode('utf-8')).hexdigest()
        string_to_sign = f"sha1\n{key_time}\n{sha1_http}\n"
        sig = hmac.new(bytes.fromhex(sign_key), string_to_sign.encode('utf-8'), hashlib.sha1).hexdigest()
        return f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}&q-key-time={key_time}&q-header-list=content-length;host&q-url-param-list=&q-signature={sig}"

    @staticmethod
    def _parse_ts(value):
        if not value:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        if re.fullmatch(r'\d+(?:\.\d+)?', text):
            return int(float(text))
        try:
            dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError as exc:
            raise RuntimeError(f'invalid expiration: {value}') from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    def put_file_to_cos(self, upload, content):
        file_info = upload.get('file_info', {})
        if file_info.get('confirm_url') or upload.get('confirm_url'):
            return ''
        bucket = upload.get('bucket_name') or file_info.get('bucket_name')
        region = upload.get('region') or file_info.get('region')
        object_key = file_info.get('object') or file_info.get('object_key') or upload.get('object') or upload.get('object_key')
        if not object_key:
            return ''
        content_type = 'image/jpeg' if '.jp' in (object_key or '').lower() else ('image/png' if '.png' in (object_key or '').lower() else 'image/webp')
        pre_sign_url = upload.get('pre_sign_url') or file_info.get('pre_sign_url') or ''
        if pre_sign_url:
            # COS pre-signed URL: only Host + Content-Length, nothing else.
            # requests/urllib3 adds User-Agent/Accept-Encoding which cause 403.
            hdrs = {'Content-Length': str(len(content))}
            if bucket and region:
                hdrs['Host'] = f"{bucket}.cos.{region}.myqcloud.com"
            import urllib3
            http = urllib3.PoolManager()
            r = http.request('PUT', pre_sign_url, headers=hdrs, body=content, timeout=60)
            if r.status != 200:
                body = r.data[:200].decode('utf-8', errors='replace')
                raise RuntimeError(f'cos put failed: {r.status} {body}')
            return pre_sign_url.split('?', 1)[0]
        secret_id = upload.get('access_key_id')
        secret_key = upload.get('access_key_secret')
        security_token = upload.get('security_token')
        expiration = self._parse_ts(upload.get('expiration'))
        if not all([bucket, region, object_key, secret_id, secret_key, security_token, expiration]):
            raise RuntimeError('incomplete cos credentials')
        host = f"{bucket}.cos.{region}.myqcloud.com"
        path = f"/{object_key.lstrip('/')}"
        url = f"https://{host}{path}"
        now_ts = int(datetime.now(timezone.utc).timestamp())
        sign_start, sign_end = min(now_ts, expiration), expiration
        if sign_end <= sign_start:
            sign_start = max(sign_end - 60, 0)
        auth = self._build_cos_auth(secret_id, secret_key, 'PUT', host, path, len(content), sign_start, sign_end)
        r = requests.put(url, headers={'Authorization': auth, 'x-cos-security-token': security_token,
                                        'Content-Type': content_type, 'Content-Length': str(len(content)),
                                        'Origin': 'https://admin.chainthink.cn', 'Host': host}, data=content, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'cos put failed: {r.status_code}')
        return url

    def upload_cover_from_url(self, image_url):
        if not image_url:
            return ''
        img = self.session.get(image_url, timeout=60)
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
            file_hash = compute_crc64_file(tmp_path)
            upload = self.request_cover_upload(f'cover.{ext}', file_hash, use_pre_sign_url=True)
            file_info = upload.get('file_info', {})
            # Check if file already exists (hash dedup hit)
            confirm_url = file_info.get('confirm_url') or upload.get('confirm_url') or ''
            if confirm_url:
                return confirm_url
            # Merge key data (non-empty values only)
            key_data = upload.get('key', {})
            if key_data:
                for k, v in key_data.items():
                    if v not in (None, '', []):
                        upload[k] = v
                file_info = upload.get('file_info', {})
            # Check has COS upload target
            has_cos = bool(
                upload.get('pre_sign_url') or (
                    upload.get('access_key_id') and
                    upload.get('access_key_secret') and
                    upload.get('security_token') and
                    upload.get('bucket_name') and
                    upload.get('region') and
                    upload.get('expiration')
                )
            )
            uploaded = False
            if has_cos:
                self.put_file_to_cos(upload, img.content)
                uploaded = True
            object_key = file_info.get('object') or file_info.get('object_key') or upload.get('object') or upload.get('object_key') or ''
            confirm_url = file_info.get('confirm_url') or upload.get('confirm_url') or ''
            if confirm_url:
                return confirm_url
            # Confirm after COS upload
            if uploaded and object_key:
                try:
                    cu = self.request_cover_upload(f'cover.{ext}', file_hash, use_pre_sign_url=False, confirm=True)
                    cu_url = cu.get('file_info', {}).get('confirm_url') or cu.get('confirm_url') or ''
                    if cu_url:
                        return cu_url
                except Exception:
                    pass
            # Fallback: construct URL
            domain = file_info.get('domain') or upload.get('domain') or 'https://cos.chainthink.cn'
            if object_key:
                return f"{domain.rstrip('/')}/{object_key.lstrip('/')}"
            rh = file_info.get('hash') or upload.get('hash') or file_hash
            re_ = file_info.get('ext') or upload.get('ext') or ext
            return f"https://cos.chainthink.cn/{self.x_app_id}_admin_file/{rh}/{rh}.{re_}"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # -- Publisher --

    def publish(self, article):
        cover_image, cover_error = '', ''
        if article.get('source_key') == 'techflow' and article.get('cover_src'):
            try:
                cover_image = self.upload_cover_from_url(article['cover_src'])
            except Exception as exc:
                cover_error = str(exc)
                log.warning("Cover upload failed for %s: %s", article.get('article_id_full', ''), exc)
        payload = {
            'id': '0',
            'info': {'cover_image': cover_image} if cover_image else {},
            'is_translate': True,
            'translation': {'zh-CN': {'title': article['title'], 'text': self.build_html(article), 'abstract': self.build_abstract(article)}},
            'type': 5, 'admin_detail': {}, 'strong_content_tags': {}, 'chain_is_calendar': False,
            'chain_calendar_time': int(datetime.now().timestamp()), 'chain_calendar_tendency': 0,
            'is_push_bian': 2, 'content_pin_top': 0, 'is_public': False, 'user_id': '3',
            'chain_fixed_publish_time': 0, 'as_user_id': '3', 'is_chain': True,
            'chain_airdrop_time': 0, 'chain_airdrop_time_end': 0,
        }
        r = requests.post(self.api_url, headers=self.api_headers, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=30)
        data = r.json()
        if r.status_code == 200 and data.get('code') == 0:
            return {'cms_id': data['data']['id'], 'cover_image': cover_image, 'cover_error': cover_error}
        raise RuntimeError(f"publish failed: {r.status_code} {data}")

    # -- Orchestration --

    def ingest_sources(self, source, state):
        fetched = []
        published_ids = set(state.get('published_ids', []))
        if source in ('stcn', 'all') and self.stcn_cfg.get('enabled', True):
            for item in self.parse_stcn_list():
                if item['author'] not in self.allowed_authors:
                    continue
                aid = f"stcn:{item['article_id']}"
                if aid in published_ids:
                    continue
                try:
                    article = self.fetch_stcn_detail(item)
                    self.save_stcn_article(article)
                    fetched.append(aid)
                    log.info("Fetched STCN: %s - %s", aid, item['title'][:40])
                except Exception as e:
                    log.error("Fetch STCN %s failed: %s", item['article_id'], e)
        if source in ('techflow', 'all') and self.techflow_cfg.get('enabled', True):
            existing = {p.stem.replace('techflow_', '') for p in self.techflow_dir.glob('techflow_*.json')} if self.techflow_dir.exists() else set()
            for item in self.parse_techflow_list():
                aid = f"techflow:{item['article_id']}"
                if aid in published_ids or item['article_id'] in existing:
                    continue
                try:
                    article = self.fetch_techflow_detail(item)
                    self.save_techflow_article(article)
                    fetched.append(aid)
                    log.info("Fetched TechFlow: %s - %s", aid, item['title'][:40])
                except Exception as e:
                    log.error("Fetch TechFlow %s failed: %s", item['article_id'], e)
        log.info("Ingested %d new articles", len(fetched))
        return fetched

    def load_articles(self, source):
        articles = []
        if source in ('stcn', 'all') and self.stcn_dir.exists():
            for f in sorted(self.stcn_dir.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.suffix == '.json':
                    articles.append(self.parse_techflow_article_file(f))
                    articles[-1]['source_key'] = 'stcn'
                    articles[-1]['article_id'] = f"stcn:{articles[-1].get('raw_id', articles[-1].get('article_id', ''))}"
                elif f.suffix == '.md':
                    articles.append(self.parse_stcn_article_file(f))
        if source in ('techflow', 'all') and self.techflow_dir.exists():
            for f in sorted(self.techflow_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
                articles.append(self.parse_techflow_article_file(f))
        return articles

    def run(self, source='all', since_today_0700=False, republish_ids=None, skip_fetch=False,
            refetch_stcn_urls=None, refetch_techflow_ids=None, dry_run=False):
        """Run the full pipeline. Returns result dict."""
        started = datetime.now()
        log.info("Pipeline started: source=%s dry_run=%s", source, dry_run)

        state = self.load_state()
        refetch_mode = bool(refetch_stcn_urls or refetch_techflow_ids)
        refreshed = []

        if refetch_mode:
            if source in ('stcn', 'all') and refetch_stcn_urls:
                for url in refetch_stcn_urls:
                    try:
                        item = self.build_stcn_item_from_url(url)
                        article = self.fetch_stcn_detail(item)
                        path = self.save_stcn_article(article)
                        refreshed.append({'id': article['article_id_full'], 'path': str(path)})
                    except Exception as e:
                        log.error("Refetch STCN %s failed: %s", url, e)
            if source in ('techflow', 'all') and refetch_techflow_ids:
                tf_items = {it['article_id']: it for it in self.parse_techflow_list()}
                for aid in refetch_techflow_ids:
                    item = tf_items.get(str(aid)) or {'article_id': str(aid), 'title': str(aid),
                                'original_url': f'https://www.techflowpost.com/zh-CN/article/{aid}', 'source': '深潮 TechFlow'}
                    try:
                        article = self.fetch_techflow_detail(item)
                        path = self.save_techflow_article(article)
                        refreshed.append({'id': article['article_id_full'], 'path': str(path)})
                    except Exception as e:
                        log.error("Refetch TechFlow %s failed: %s", aid, e)
        elif not skip_fetch:
            self.ingest_sources(source, state)

        published_ids = set(state.get('published_ids', []))
        republish_set = set(republish_ids or [])

        if refetch_mode and not republish_set:
            self.save_state(state)
            return {'ok': True, 'refetched': refreshed, 'published': [], 'skipped': [], 'failed': []}

        articles = self.load_articles(source)
        accepted, skipped = [], []
        for article in articles:
            if article['source_key'] == 'stcn' and article.get('author') not in self.allowed_authors:
                skipped.append({'id': article['article_id'], 'reason': 'author'})
                continue
            if since_today_0700 and article['source_key'] == 'stcn':
                pt = article.get('publish_time', '')
                m = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', pt)
                if not m or m.group(1) != self.today_str() or m.group(2) < '07:00':
                    skipped.append({'id': article['article_id'], 'reason': 'time'})
                    continue
            if article['article_id'] in published_ids and article['article_id'] not in republish_set:
                skipped.append({'id': article['article_id'], 'reason': 'already_published'})
                continue
            accepted.append(article)

        published, failed = [], []
        if dry_run:
            log.info("Dry run: would publish %d articles", len(accepted))
        else:
            for article in accepted:
                try:
                    result = self.publish(article)
                    published.append({'article_id': article['article_id'], 'cms_id': result['cms_id'],
                                     'title': article['title'], 'cover_image': result.get('cover_image', '')})
                    published_ids.add(article['article_id'])
                    log.info("Published %s -> CMS %s", article['article_id'], result['cms_id'])
                except Exception as e:
                    failed.append({'id': article['article_id'], 'error': str(e), 'source': article['source_key']})
                    log.error("Publish failed %s: %s", article['article_id'], e)

        state['published_ids'] = sorted(published_ids)
        self.save_state(state)

        elapsed = (datetime.now() - started).total_seconds()
        log.info("Pipeline done: published=%d skipped=%d failed=%d %.1fs", len(published), len(skipped), len(failed), elapsed)
        return {'ok': True, 'refetched': refreshed, 'published': published, 'skipped': skipped, 'failed': failed}


def setup_logging(base_dir: Path = None):
    """Configure logging to file + stdout."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
    cfg = load_config(base_dir)
    log_file = base_dir / cfg['paths']['log_file']
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("{asctime} [{levelname}] {message}", style='{')
    fh = logging.FileHandler(str(log_file), encoding='utf-8')
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    log.setLevel(logging.DEBUG)
    log.addHandler(fh)
    log.addHandler(sh)


# Module-level pipeline instance (lazy)
_pipeline = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ChainThink Article Publisher Pipeline")
    parser.add_argument('--source', choices=['stcn', 'techflow', 'all'], default='all')
    parser.add_argument('--since-today-0700', action='store_true')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--republish', nargs='*', default=[])
    parser.add_argument('--skip-fetch', action='store_true')
    parser.add_argument('--refetch-stcn-url', nargs='*', default=[])
    parser.add_argument('--refetch-techflow-id', nargs='*', default=[])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    setup_logging()
    p = Pipeline()
    result = p.run(
        source=args.source,
        since_today_0700=args.since_today_0700,
        republish_ids=args.republish,
        skip_fetch=args.skip_fetch,
        refetch_stcn_urls=args.refetch_stcn_url or None,
        refetch_techflow_ids=args.refetch_techflow_id or None,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
