#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test cover upload: download image, compute hash, PUT to COS, confirm."""
import json, os, sys, tempfile

from pipeline import Pipeline
from crc64_js import compute_crc64_file


def main():
    p = Pipeline()
    articles = p.load_articles('techflow')
    a = [x for x in articles if x.get('cover_src')][0]
    cover_url = a['cover_src']
    print('Cover URL:', cover_url[:100])

    # Download
    img = p.session.get(cover_url, timeout=60)
    img.raise_for_status()
    content = img.content
    ext = 'webp' if cover_url.lower().endswith('.webp') else ('png' if cover_url.lower().endswith('.png') else 'jpg')
    print('Size:', len(content), 'bytes, ext:', ext)

    # Compute hash
    with tempfile.NamedTemporaryFile(delete=False, suffix='.' + ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    file_hash = compute_crc64_file(tmp_path)
    os.unlink(tmp_path)
    print('CRC64:', file_hash)

    # Step 1: Request upload (use_pre_sign_url=True)
    upload = p.request_cover_upload('cover.' + ext, file_hash, use_pre_sign_url=True)
    print()
    print('=== Step 1: upload_file response ===')
    print(json.dumps(upload, indent=2, ensure_ascii=False)[:800])

    file_info = upload.get('file_info', {})
    confirm_url = file_info.get('confirm_url') or upload.get('confirm_url') or ''
    if confirm_url:
        print()
        print('File already exists! confirm_url:', confirm_url)
        return

    pre_sign_url = upload.get('pre_sign_url') or file_info.get('pre_sign_url') or ''
    print()
    print('pre_sign_url:', pre_sign_url[:120])

    if not pre_sign_url:
        print('ERROR: no pre_sign_url!')
        return

    # Step 2: PUT to COS (only Host + Content-Length)
    import urllib3
    http = urllib3.PoolManager()
    bucket = upload.get('bucket_name') or file_info.get('bucket_name')
    region = upload.get('region') or file_info.get('region')
    hdrs = {'Content-Length': str(len(content))}
    if bucket and region:
        hdrs['Host'] = bucket + '.cos.' + region + '.myqcloud.com'
    print()
    print('=== Step 2: PUT to COS ===')
    print('Headers:', hdrs)
    r = http.request('PUT', pre_sign_url, headers=hdrs, body=content, timeout=60)
    print('Status:', r.status)
    if r.status != 200:
        err_text = r.data[:500].decode('utf-8', errors='replace')
        print('Error:', err_text)
        return

    print('PUT success!')
    print('Response headers:', dict(r.headers))

    # Step 3: Confirm
    confirm = p.request_cover_upload('cover.' + ext, file_hash, use_pre_sign_url=False, confirm=True)
    print()
    print('=== Step 3: Confirm response ===')
    print(json.dumps(confirm, indent=2, ensure_ascii=False)[:600])
    cu = confirm.get('file_info', {}).get('confirm_url', '') or confirm.get('confirm_url', '')
    print('Final cover URL:', cu)


if __name__ == '__main__':
    main()
