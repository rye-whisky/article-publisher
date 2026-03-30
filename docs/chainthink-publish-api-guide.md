# ChainThink 文章发布 API 对接指南

> 本文档指导如何通过脚本实现"带封面图片的文章上传与发布"全流程。

---

## 目录

1. [前置准备](#1-前置准备)
2. [认证与通用 Headers](#2-认证与通用-headers)
3. [完整流程概览](#3-完整流程概览)
4. [步骤 1：计算文件哈希](#步骤-1计算文件-crc64-哈希)
5. [步骤 2：申请上传凭证](#步骤-2申请上传凭证-upload_file)
6. [步骤 3：上传文件到 COS](#步骤-3上传文件到-cos)
7. [步骤 4：确认上传（Confirm）](#步骤-4确认上传-confirm)
8. [步骤 5：发布文章](#步骤-5发布文章-publish)
9. [无封面文章的简化流程](#无封面文章的简化流程)
10. [错误处理与常见陷阱](#错误处理与常见陷阱)
11. [完整伪代码](#完整伪代码)
12. [附录：签名算法](#附录cos-临时密钥签名算法)

---

## 1. 前置准备

### 需要获取的凭证

| 凭证 | 获取方式 | 说明 |
|---|---|---|
| `x-token` | 登录 `https://admin.chainthink.cn` 后，F12 → Network → 任意请求的 Header | JWT，有过期时间（约 24h） |
| `x-user-id` | 同上 | 固定值，如 `"83"` |
| `crc64.js` | `GET https://admin.chainthink.cn/crc64.js` | 用于计算文件哈希 |

### 依赖

- **HTTP 客户端**：任意（curl / requests / fetch / axios）
- **Node.js**：执行 `crc64.js` 计算文件哈希
- 无需 SDK，腾讯云 COS 上传用原生 HTTP PUT 即可

---

## 2. 认证与通用 Headers

所有对 `api-v2.chainthink.cn` 的请求都携带以下 Headers：

```http
Content-Type: application/json; charset=utf-8
Accept: application/json, text/plain, */*
Origin: https://admin.chainthink.cn
Referer: https://admin.chainthink.cn/
User-Agent: Mozilla/5.0
x-token: <你的JWT>
x-user-id: <你的用户ID>
X-App-Id: 101
```

**注意**：`X-App-Id` 始终为 `101`，这是后台的应用标识。

---

## 3. 完整流程概览

```
带封面的文章发布（5 步）

  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │  ① 计算封面图的 CRC64 哈希                            │
  │            │                                        │
  │            ▼                                        │
  │  ② POST /upload_file (confirm=false)  申请上传凭证    │
  │            │                                        │
  │      ┌─────┴──────┐                                 │
  │      │            │                                 │
  │  文件已存在    文件不存在                               │
  │  (有confirm_url) (有COS凭证)                          │
  │      │            │                                 │
  │      │            ▼                                 │
  │      │     ③ PUT 文件到腾讯云 COS                      │
  │      │            │                                 │
  │      │            ▼                                 │
  │      │     ④ POST /upload_file (confirm=true) 确认    │
  │      │            │                                 │
  │      └─────┬──────┘                                 │
  │            ▼                                        │
  │     拿到封面图 URL                                    │
  │            │                                        │
  │            ▼                                        │
  │  ⑤ POST /content/publish  发布文章                    │
  │                                                     │
  └─────────────────────────────────────────────────────┘
```

---

## 步骤 1：计算文件 CRC64 哈希

ChainThink 使用自定义 CRC64 算法（非标准 CRC64-ECMA / CRC64-ISO），必须使用后台提供的 `crc64.js`。

### 下载 crc64.js

```bash
curl -o crc64.js https://admin.chainthink.cn/crc64.js
```

### 计算哈希（Node.js）

```javascript
// crc64_compute.js
const fs = require('fs');
const vm = require('vm');

const code = fs.readFileSync('crc64.js', 'utf8');
const buf = fs.readFileSync(process.argv[2]); // 文件路径

const ctx = {
  console, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer,
  DataView, Int32Array, Uint32Array, Buffer, process, require,
  module: { exports: {} }, exports: {}
};
ctx.window = ctx;
ctx.self = ctx;
ctx.global = ctx;

vm.createContext(ctx);
vm.runInContext(code, ctx);
process.stdout.write(String(ctx.CRC64.crc64(buf)));
```

```bash
node crc64_compute.js cover.jpg
# 输出: 14947056464881677747
```

---

## 步骤 2：申请上传凭证 (upload_file)

### 请求

```http
POST https://api-v2.chainthink.cn/ccs/v1/admin/upload_file
```

```json
{
  "file_name": "cover.jpg",
  "hash": "14947056464881677747",
  "use_pre_sign_url": true,
  "confirm": false
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_name` | string | ✅ | 文件名，决定后端识别的格式（`.jpg` / `.png` / `.webp`） |
| `hash` | string | ✅ | 步骤 1 计算的 CRC64 哈希 |
| `use_pre_sign_url` | bool | ✅ | `true` = 申请预签名 URL（推荐）；`false` = 申请临时密钥 |
| `confirm` | bool | ✅ | 首次请求为 `false`；确认请求为 `true` |

### 响应解读

#### 场景 A：文件已存在（hash 去重命中）

```json
{
  "code": 0,
  "data": {
    "file_info": {
      "confirm_url": "https://cos.chainthink.cn/101_admin_file/14947056464881677747/14947056464881677747.jpg",
      "object": "101_admin_file/14947056464881677747/14947056464881677747.jpg"
    }
  }
}
```

**→ 直接拿到 `file_info.confirm_url`，跳到步骤 5 发布文章。不需要上传。**

#### 场景 B：新文件 — 返回预签名 URL

```json
{
  "code": 0,
  "data": {
    "key": {
      "pre_sign_url": "https://chainthink-xxx.cos.ap-guangzhou.myqcloud.com/101_admin_file/hash/hash.jpg?sign=xxx&expires=xxx",
      "object_key": "101_admin_file/hash/hash.jpg"
    },
    "file_info": {}
  }
}
```

**→ 进入步骤 3，用 `key.pre_sign_url` 上传。**

⚠️ **陷阱**：预签名 URL 和 `object_key` 在 `data.key` 子对象里，不在 `data` 顶层！合并时小心空值覆盖。

#### 场景 C：新文件 — 返回临时密钥

```json
{
  "code": 0,
  "data": {
    "access_key_id": "AKIDxxxxxxxx",
    "access_key_secret": "xxxxxxxx",
    "security_token": "xxxxxxxx",
    "bucket_name": "chainthink-xxx",
    "region": "ap-guangzhou",
    "object_key": "101_admin_file/hash/hash.jpg",
    "expiration": 1745600000,
    "file_info": {}
  }
}
```

**→ 进入步骤 3，用临时密钥签名上传（见附录）。**

---

## 步骤 3：上传文件到 COS

### 模式 A：预签名 URL 上传（推荐）

```http
PUT <pre_sign_url>
```

```
PUT https://chainthink-xxx.cos.ap-guangzhou.myqcloud.com/101_admin_file/hash/hash.jpg?sign=xxx&...
```

**Headers**：

```http
Content-Type: image/jpeg
Content-Length: <文件字节数>
Host: chainthink-xxx.cos.ap-guangzhou.myqcloud.com
```

**Body**：图片的二进制内容

**成功响应**：`HTTP 200`

### 模式 B：临时密钥签名上传

```http
PUT https://<bucket>.cos.<region>.myqcloud.com/<object_key>
```

**Headers**：

```http
Authorization: <COS 签名（见附录）>
x-cos-security-token: <security_token>
Content-Type: image/jpeg
Content-Length: <文件字节数>
Host: <bucket>.cos.<region>.myqcloud.com
Origin: https://admin.chainthink.cn
Referer: https://admin.chainthink.cn/
```

**Body**：图片的二进制内容

**成功响应**：`HTTP 200`

### Content-Type 对照

| 扩展名 | Content-Type |
|---|---|
| `.jpg` / `.jpeg` | `image/jpeg` |
| `.png` | `image/png` |
| `.webp` | `image/webp` |

---

## 步骤 4：确认上传 (Confirm)

> ⚠️ 仅预签名 URL 模式需要此步骤。临时密钥模式无需 confirm。

上传到 COS 成功后，必须通知后端文件已就绪。

### 请求

```http
POST https://api-v2.chainthink.cn/ccs/v1/admin/upload_file
```

```json
{
  "file_name": "cover.jpg",
  "hash": "14947056464881677747",
  "use_pre_sign_url": false,
  "confirm": true
}
```

### 响应

```json
{
  "code": 0,
  "data": {
    "file_info": {
      "confirm_url": "https://cos.chainthink.cn/101_admin_file/hash/hash.jpg"
    }
  }
}
```

**→ 拿到 `confirm_url`，这就是最终的封面图 URL。**

### Confirm 失败的兜底

如果 confirm 失败，可以自行拼接 URL：

```
https://cos.chainthink.cn/<object_key>
```

其中 `object_key` 在步骤 2 的响应中已获得。

---

## 步骤 5：发布文章 (publish)

### 请求

```http
POST https://api-v2.chainthink.cn/ccs/v1/admin/content/publish
```

```json
{
  "id": "0",
  "info": {
    "cover_image": "https://cos.chainthink.cn/101_admin_file/hash/hash.jpg"
  },
  "is_translate": true,
  "translation": {
    "zh-CN": {
      "title": "文章标题",
      "text": "<p>段落一</p><p>段落二</p>",
      "abstract": "文章摘要，不超过180字"
    }
  },
  "type": 5,
  "admin_detail": {},
  "strong_content_tags": {},
  "chain_is_calendar": false,
  "chain_calendar_time": 1743331200,
  "chain_calendar_tendency": 0,
  "is_push_bian": 2,
  "content_pin_top": 0,
  "is_public": false,
  "user_id": "3",
  "chain_fixed_publish_time": 0,
  "as_user_id": "3",
  "is_chain": true,
  "chain_airdrop_time": 0,
  "chain_airdrop_time_end": 0
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | `"0"` = 新建；传 CMS ID = 更新已有文章 |
| `info.cover_image` | string | 封面图 URL（来自步骤 2/4）；无封面则 `info` 传 `{}` |
| `is_translate` | bool | `true` = 多语言模式 |
| `translation.zh-CN.title` | string | 中文标题 |
| `translation.zh-CN.text` | string | HTML 正文（支持 `<p>` `<img>` `<h2>` 等标签） |
| `translation.zh-CN.abstract` | string | 摘要，建议 ≤ 180 字 |
| `type` | int | 文章类型，固定 `5` |
| `is_public` | bool | `false` = 存为草稿；`true` = 直接发布 |
| `user_id` | string | 发布者 ID |
| `as_user_id` | string | 代理发布者 ID，通常与 `user_id` 相同 |
| `is_chain` | bool | 固定 `true` |
| `is_push_bian` | int | 固定 `2` |
| 其他字段 | — | 保持示例中的默认值即可 |

### 响应

```json
{
  "code": 0,
  "data": {
    "id": "116362190339805184"
  }
}
```

`data.id` 是后台的文章 ID（CMS ID），后续可用于更新或删除。

---

## 无封面文章的简化流程

如果文章没有封面图，跳过步骤 1-4，直接调用步骤 5，`info` 传空对象：

```json
{
  "id": "0",
  "info": {},
  ...
}
```

---

## 错误处理与常见陷阱

### 1. CRC64 不是标准哈希

必须用后台提供的 `crc64.js` 计算。Python 标准 `crcmod`、Java `CRC64` 等都不行。

### 2. `data.key` 合并陷阱

预签名 URL 模式下，凭证在 `data.key` 子对象中：

```python
# ❌ 错误：空值覆盖非空值
merged = {**upload_data, **key_data}

# ✅ 正确：只覆盖非空值
for k, v in key_data.items():
    if v not in (None, '', []):
        upload_data[k] = v
```

### 3. 已存在对象不能再次上传

当响应中 `file_info.confirm_url` 存在时，表示文件已上传过。此时**不要**再 PUT 到 COS，直接使用该 URL。

### 4. 预签名模式必须 Confirm

PUT 到 COS 成功后，如果不上传确认（`confirm=true`），后端发布时找不到文件，会报 `"object not found"`。

### 5. Token 过期

JWT token 有过期时间（通常约 24 小时）。过期后所有 API 返回 401，需要重新登录获取新 token。

### 6. 超时与重试

- COS 上传建议 `timeout=60s`
- API 调用建议 `timeout=30s`
- 建议实现重试机制（最多 3 次），尤其针对 502 / 网络超时
- 发布失败时**不要**写入去重 state，否则下次无法重试

---

## 完整伪代码

```python
import requests, json, tempfile, subprocess, os
from pathlib import Path

# ========== 配置 ==========
API_BASE = "https://api-v2.chainthink.cn"
TOKEN = "<your_jwt_token>"
USER_ID = "<your_user_id>"
APP_ID = "101"

HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://admin.chainthink.cn",
    "Referer": "https://admin.chainthink.cn/",
    "x-token": TOKEN,
    "x-user-id": USER_ID,
    "X-App-Id": APP_ID,
}

# ========== 步骤 1: 计算 CRC64 ==========
def compute_crc64(file_path: str) -> str:
    """用 Node.js 执行 crc64.js 计算文件哈希"""
    # 先下载 crc64.js（缓存到本地）
    crc64_js = Path(tempfile.gettempdir()) / "chainthink_crc64.js"
    if not crc64_js.exists():
        r = requests.get("https://admin.chainthink.cn/crc64.js", timeout=60)
        crc64_js.write_text(r.text)

    node_script = """
    const fs=require('fs'); const vm=require('vm');
    const code=fs.readFileSync(process.argv[1],'utf8');
    const buf=fs.readFileSync(process.argv[2]);
    const ctx={console,TextEncoder,TextDecoder,Uint8Array,ArrayBuffer,
               DataView,Int32Array,Uint32Array,Buffer,process,require,
               module:{exports:{}},exports:{}};
    ctx.window=ctx; ctx.self=ctx; ctx.global=ctx;
    vm.createContext(ctx); vm.runInContext(code, ctx);
    process.stdout.write(String(ctx.CRC64.crc64(buf)));
    """
    result = subprocess.check_output(
        ["node", "-e", node_script, str(crc64_js), file_path], text=True
    )
    return result.strip()


# ========== 步骤 2: 申请上传凭证 ==========
def request_upload(file_name, file_hash, use_pre_sign=True, confirm=False):
    url = f"{API_BASE}/ccs/v1/admin/upload_file"
    payload = {
        "file_name": file_name,
        "hash": file_hash,
        "use_pre_sign_url": use_pre_sign,
        "confirm": confirm,
    }
    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    data = r.json()
    if r.status_code == 200 and data.get("code") == 0:
        return data["data"]
    raise RuntimeError(f"upload_file failed: {r.status_code} {data}")


# ========== 步骤 3: 上传到 COS ==========
def upload_to_cos(upload_data, file_bytes):
    # 检查是否已存在
    file_info = upload_data.get("file_info", {})
    if file_info.get("confirm_url"):
        return file_info["confirm_url"]  # 已存在，跳过

    # 合并 key 子对象（预签名模式）
    key_data = upload_data.get("key", {})
    for k, v in key_data.items():
        if v not in (None, "", []):
            upload_data[k] = v
    file_info = upload_data.get("file_info", {})

    # 优先用预签名 URL
    pre_sign_url = upload_data.get("pre_sign_url") or file_info.get("pre_sign_url")
    if pre_sign_url:
        r = requests.put(pre_sign_url, data=file_bytes, timeout=60)
        r.raise_for_status()
        return pre_sign_url.split("?", 1)[0]

    # 退回临时密钥模式（见附录签名算法）
    # ... build_cos_authorization() ...
    raise RuntimeError("no upload method available")


# ========== 步骤 4: Confirm ==========
def confirm_upload(file_name, file_hash):
    return request_upload(file_name, file_hash, use_pre_sign=False, confirm=True)


# ========== 封面上传完整流程 ==========
def upload_cover(image_bytes, ext="jpg"):
    # 1. 写临时文件算哈希
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as f:
        f.write(image_bytes)
        tmp = f.name
    try:
        file_hash = compute_crc64(tmp)

        # 2. 申请凭证
        upload_data = request_upload(f"cover.{ext}", file_hash, use_pre_sign=True)

        # 检查已存在
        file_info = upload_data.get("file_info", {})
        if file_info.get("confirm_url"):
            return file_info["confirm_url"]

        # 3. 上传到 COS
        uploaded_url = upload_to_cos(upload_data, image_bytes)

        # 4. Confirm
        confirm_data = confirm_upload(f"cover.{ext}", file_hash)
        confirm_url = confirm_data.get("file_info", {}).get("confirm_url", "")
        if confirm_url:
            return confirm_url

        return uploaded_url
    finally:
        os.unlink(tmp)


# ========== 步骤 5: 发布文章 ==========
def publish_article(title, html_body, abstract, cover_url="", user_id="3"):
    url = f"{API_BASE}/ccs/v1/admin/content/publish"
    payload = {
        "id": "0",
        "info": {"cover_image": cover_url} if cover_url else {},
        "is_translate": True,
        "translation": {
            "zh-CN": {
                "title": title,
                "text": html_body,
                "abstract": abstract[:180],
            }
        },
        "type": 5,
        "admin_detail": {},
        "strong_content_tags": {},
        "chain_is_calendar": False,
        "chain_calendar_time": int(datetime.now().timestamp()),
        "chain_calendar_tendency": 0,
        "is_push_bian": 2,
        "content_pin_top": 0,
        "is_public": False,
        "user_id": user_id,
        "chain_fixed_publish_time": 0,
        "as_user_id": user_id,
        "is_chain": True,
        "chain_airdrop_time": 0,
        "chain_airdrop_time_end": 0,
    }
    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    data = r.json()
    if r.status_code == 200 and data.get("code") == 0:
        return data["data"]["id"]  # CMS ID
    raise RuntimeError(f"publish failed: {r.status_code} {data}")


# ========== 使用示例 ==========
if __name__ == "__main__":
    # 从 URL 下载封面图
    img_response = requests.get("https://example.com/cover.jpg", timeout=30)
    cover_url = upload_cover(img_response.content, ext="jpg")

    # 发布文章
    cms_id = publish_article(
        title="BTC 突破 10 万美元大关",
        html_body="<p>比特币价格在今日凌晨突破 10 万美元...</p>",
        abstract="比特币价格历史性突破 10 万美元，市场情绪高涨",
        cover_url=cover_url,
    )
    print(f"发布成功，CMS ID: {cms_id}")
```

---

## 附录：COS 临时密钥签名算法

当 `use_pre_sign_url=false` 时，返回临时密钥，需要自行计算签名。

### 签名步骤

```
1. KeyTime = "<当前时间戳>;<过期时间戳>"
2. SignKey = HMAC-SHA1(SecretKey, KeyTime) → 十六进制
3. HttpString = "<method>\n<path>\n\ncontent-length=<len>&host=<host>\n"
4. StringToSign = "sha1\n<KeyTime>\n<SHA1(HttpString)>\n"
5. Signature = HMAC-SHA1(SignKey_hex, StringToSign) → 十六进制
6. Authorization = "q-sign-algorithm=sha1&q-ak=<SecretId>&q-sign-time=<KeyTime>&q-key-time=<KeyTime>&q-header-list=content-length;host&q-url-param-list=&q-signature=<Signature>"
```

### Python 实现

```python
import hmac, hashlib

def build_cos_authorization(secret_id, secret_key, method, host, path, content_length, sign_start, sign_end):
    key_time = f"{sign_start};{sign_end}"
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    http_string = f"{method.lower()}\n{path}\n\ncontent-length={content_length}&host={host.lower()}\n"
    sha1_http = hashlib.sha1(http_string.encode()).hexdigest()
    string_to_sign = f"sha1\n{key_time}\n{sha1_http}\n"
    signature = hmac.new(bytes.fromhex(sign_key), string_to_sign.encode(), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}"
        f"&q-key-time={key_time}&q-header-list=content-length;host"
        f"&q-url-param-list=&q-signature={signature}"
    )
```

---

## API 端点速查表

| 步骤 | 方法 | URL | 用途 |
|---|---|---|---|
| 申请凭证 | `POST` | `https://api-v2.chainthink.cn/ccs/v1/admin/upload_file` | 获取 COS 上传凭证或预签名 URL |
| 上传文件 | `PUT` | `<pre_sign_url>` 或 `https://<bucket>.cos.<region>.myqcloud.com/<object_key>` | 上传图片到腾讯云 COS |
| 确认上传 | `POST` | `https://api-v2.chainthink.cn/ccs/v1/admin/upload_file` | confirm=true，通知后端文件就绪 |
| 发布文章 | `POST` | `https://api-v2.chainthink.cn/ccs/v1/admin/content/publish` | 创建/更新文章 |
| CRC64 库 | `GET` | `https://admin.chainthink.cn/crc64.js` | 哈希计算脚本 |
