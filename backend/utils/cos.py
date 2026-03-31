# -*- coding: utf-8 -*-
"""COS (Cloud Object Storage) upload utility.

Handles requesting upload credentials, building COS authorization,
uploading files, and confirming uploads.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone

import requests
import urllib3

from crc64_js import compute_crc64_file

log = logging.getLogger("pipeline")


class COSUploader:
    """Uploads images to COS via ChainThink's upload API."""

    def __init__(self, upload_url: str, api_headers: dict, session: requests.Session, x_app_id: str = ""):
        self.upload_url = upload_url
        self.api_headers = api_headers
        self.session = session
        self.x_app_id = x_app_id

    # -- Credential request --

    def request_upload(self, file_name, file_hash, use_pre_sign_url=False, confirm=False):
        payload = {
            "file_name": file_name,
            "hash": file_hash,
            "use_pre_sign_url": use_pre_sign_url,
            "confirm": confirm,
        }
        r = requests.post(
            self.upload_url,
            headers=self.api_headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        data = r.json()
        if r.status_code == 200 and data.get("code") == 0:
            upload_data = data["data"]
            key_data = upload_data.get("key", {})
            if key_data:
                merged = dict(upload_data)
                for k, v in key_data.items():
                    if v not in (None, "", []):
                        merged[k] = v
                return merged
            return upload_data
        raise RuntimeError(f"cover upload request failed: {r.status_code} {data}")

    # -- COS auth (signs content-length;host) --

    @staticmethod
    def _build_cos_auth(secret_id, secret_key, method, host, path, content_length, sign_start, sign_end):
        key_time = f"{sign_start};{sign_end}"
        sign_key = hmac.new(secret_key.encode("utf-8"), key_time.encode("utf-8"), hashlib.sha1).hexdigest()
        http_string = (
            f"{method.lower()}\n{path}\n\ncontent-length={content_length}&host={host.lower()}\n"
        )
        sha1_http = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
        string_to_sign = f"sha1\n{key_time}\n{sha1_http}\n"
        sig = hmac.new(bytes.fromhex(sign_key), string_to_sign.encode("utf-8"), hashlib.sha1).hexdigest()
        return (
            f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}"
            f"&q-key-time={key_time}&q-header-list=content-length;host"
            f"&q-url-param-list=&q-signature={sig}"
        )

    @staticmethod
    def _parse_ts(value):
        if not value:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return int(float(text))
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"invalid expiration: {value}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    # -- PUT to COS --

    def put_file_to_cos(self, upload, content):
        file_info = upload.get("file_info", {})
        if file_info.get("confirm_url") or upload.get("confirm_url"):
            return ""
        bucket = upload.get("bucket_name") or file_info.get("bucket_name")
        region = upload.get("region") or file_info.get("region")
        object_key = (
            file_info.get("object")
            or file_info.get("object_key")
            or upload.get("object")
            or upload.get("object_key")
        )
        if not object_key:
            return ""
        content_type = (
            "image/jpeg"
            if ".jp" in (object_key or "").lower()
            else ("image/png" if ".png" in (object_key or "").lower() else "image/webp")
        )
        pre_sign_url = upload.get("pre_sign_url") or file_info.get("pre_sign_url") or ""
        if pre_sign_url:
            hdrs = {"Content-Length": str(len(content))}
            if bucket and region:
                hdrs["Host"] = f"{bucket}.cos.{region}.myqcloud.com"
            http = urllib3.PoolManager()
            r = http.request("PUT", pre_sign_url, headers=hdrs, body=content, timeout=60)
            if r.status != 200:
                body = r.data[:200].decode("utf-8", errors="replace")
                raise RuntimeError(f"cos put failed: {r.status} {body}")
            return pre_sign_url.split("?", 1)[0]
        secret_id = upload.get("access_key_id")
        secret_key = upload.get("access_key_secret")
        security_token = upload.get("security_token")
        expiration = self._parse_ts(upload.get("expiration"))
        if not all([bucket, region, object_key, secret_id, secret_key, security_token, expiration]):
            raise RuntimeError("incomplete cos credentials")
        host = f"{bucket}.cos.{region}.myqcloud.com"
        path = f"/{object_key.lstrip('/')}"
        url = f"https://{host}{path}"
        now_ts = int(datetime.now(timezone.utc).timestamp())
        sign_start, sign_end = min(now_ts, expiration), expiration
        if sign_end <= sign_start:
            sign_start = max(sign_end - 60, 0)
        auth = self._build_cos_auth(
            secret_id, secret_key, "PUT", host, path, len(content), sign_start, sign_end
        )
        r = requests.put(
            url,
            headers={
                "Authorization": auth,
                "x-cos-security-token": security_token,
                "Content-Type": content_type,
                "Content-Length": str(len(content)),
                "Origin": "https://admin.chainthink.cn",
                "Host": host,
            },
            data=content,
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(f"cos put failed: {r.status_code}")
        return url

    # -- High-level: upload image from URL --

    def upload_cover_from_url(self, image_url):
        if not image_url:
            return ""
        img = self.session.get(image_url, timeout=60)
        img.raise_for_status()
        ext = "jpg"
        ctype = img.headers.get("content-type", "").lower()
        if "webp" in ctype or image_url.lower().endswith(".webp"):
            ext = "webp"
        elif "png" in ctype or image_url.lower().endswith(".png"):
            ext = "png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            tmp.write(img.content)
            tmp_path = tmp.name
        try:
            file_hash = compute_crc64_file(tmp_path)
            upload = self.request_upload(f"cover.{ext}", file_hash, use_pre_sign_url=True)
            file_info = upload.get("file_info", {})
            confirm_url = file_info.get("confirm_url") or upload.get("confirm_url") or ""
            if confirm_url:
                return confirm_url
            key_data = upload.get("key", {})
            if key_data:
                for k, v in key_data.items():
                    if v not in (None, "", []):
                        upload[k] = v
                file_info = upload.get("file_info", {})
            has_cos = bool(
                upload.get("pre_sign_url")
                or (
                    upload.get("access_key_id")
                    and upload.get("access_key_secret")
                    and upload.get("security_token")
                    and upload.get("bucket_name")
                    and upload.get("region")
                    and upload.get("expiration")
                )
            )
            uploaded = False
            if has_cos:
                self.put_file_to_cos(upload, img.content)
                uploaded = True
            object_key = (
                file_info.get("object")
                or file_info.get("object_key")
                or upload.get("object")
                or upload.get("object_key")
                or ""
            )
            confirm_url = file_info.get("confirm_url") or upload.get("confirm_url") or ""
            if confirm_url:
                return confirm_url
            if uploaded and object_key:
                try:
                    cu = self.request_upload(f"cover.{ext}", file_hash, use_pre_sign_url=False, confirm=True)
                    cu_url = cu.get("file_info", {}).get("confirm_url") or cu.get("confirm_url") or ""
                    if cu_url:
                        return cu_url
                except Exception:
                    pass
            domain = file_info.get("domain") or upload.get("domain") or "https://cos.chainthink.cn"
            if object_key:
                return f"{domain.rstrip('/')}/{object_key.lstrip('/')}"
            rh = file_info.get("hash") or upload.get("hash") or file_hash
            re_ = file_info.get("ext") or upload.get("ext") or ext
            return f"https://cos.chainthink.cn/{self.x_app_id}_admin_file/{rh}/{rh}.{re_}"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
