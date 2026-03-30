# ChainThink 文章发布管线 — 完整技术文档

> 最后更新：2026-03-30  
> 脚本路径：`C:\Users\whisky\.openclaw\workspace-creative\skills\stcn-chainthink-pipeline\scripts\stcn_chainthink_pipeline.py`  
> Skill 文档：`C:\Users\whisky\.openclaw\workspace-creative\skills\stcn-chainthink-pipeline\SKILL.md`

---

## 一、全局架构概览

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   信源抓取    │ ──▶ │  文章清洗     │ ──▶ │  去重检查     │ ──▶ │  推送发布     │
│  (Fetcher)   │     │  (Cleaner)   │     │  (Dedupe)    │     │  (Publisher)  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │                     │
  HTTP + BeautifulSoup   正则 + 规则引擎     JSON 状态文件        ChainThink API
  requests.Session       清洗后落地本地       stcn_chainthink_     POST /publish
                                               state.json
```

两条信源完全独立、互不影响：
- **券商中国（STCN）**：`https://www.stcn.com/article/wx/qszg.html`
- **深潮 TechFlow**：`https://www.techflowpost.com/zh-CN/article`

---

## 二、信源抓取（Fetcher）

### 2.1 券商中国

**入口函数**：`parse_stcn_list()`

**抓取流程**：

1. 用 `requests.Session`（带 `User-Agent` + `Accept-Language`）GET 列表页 `https://www.stcn.com/article/wx/qszg.html`
2. 用 BeautifulSoup 解析 HTML
3. 查找所有 `a[href*="/article/detail/"]` 链接，提取文章 ID 和标题
4. 对每个链接，向上找到父级 `li` 或 `.content` 元素作为「文章卡片」
5. 在卡片文本中用正则匹配 `券商中国 (沐阳|周乐) HH:MM` 格式，提取**作者**和**时间**
6. 只保留作者为「沐阳」或「周乐」的文章（`ALLOWED_AUTHORS = {"沐阳", "周乐"}`）
7. 时间为 `HH:MM` 格式的补上今天日期

**详情抓取**：`fetch_stcn_detail(item)`

1. GET 详情页 `https://www.stcn.com/article/detail/{ID}.html`
2. 用 `extract_stcn_body_from_soup()` 从页面中提取正文：
   - 按优先级尝试多个 CSS 选择器：`div.detail-content` → `div.detail-content-wrapper` → `article` → `div[class*="detail"]` → `div[class*="article"]`
   - 对每个候选节点，提取所有 `p`/`h2`/`h3`/`li` 元素文本
   - 按评分排序（段落多 → 高分；含排版/校对关键词 → 加分；长度 > 500 → 加分）
   - 取最高分候选作为正文
3. 调用 `clean_stcn_body()` 清洗（见下一节）

**工具链**：
| 工具 | 用途 |
|------|------|
| `requests.Session` | HTTP 请求，自动处理编码 |
| `BeautifulSoup` | HTML 解析，CSS 选择器定位元素 |
| `re` | 正则提取作者、时间、文章 ID |

### 2.2 深潮 TechFlow

**入口函数**：`parse_techflow_list()`

**抓取流程**：

1. GET 列表页 `https://www.techflowpost.com/zh-CN/article`
2. 查找所有 `a[href*="/zh-CN/article/"]` 链接
3. 提取文章 ID（URL 末尾数字）和标题（去掉日期前缀）

**详情抓取**：`fetch_techflow_detail(item)`

1. GET 详情页 `https://www.techflowpost.com/zh-CN/article/{ID}`
2. 定位 `article` 或 `main` 或 `body` 元素
3. 遍历 `h2`/`h3`/`p`/`img` 元素，构建 blocks 列表：
   - `img` → `{"type": "img", "src": ..., "alt": ...}`，第一张图片自动记为封面
   - 文本元素 → `{"type": "p"/"h2"/"h3", "text": ...}`
4. 过滤掉前置说明（作者/撰文/编译/导读等）
5. 遇到社群/Twitter/Telegram 引导内容时**停止**提取（break）

---

## 三、文章清洗（Cleaner）

### 3.1 券商中国清洗逻辑

**函数**：`clean_stcn_body(md_text: str) -> str`

**步骤**：

1. **去除外层包装**：去掉 `web_fetch` 的安全标签（`EXTERNAL_UNTRUSTED_CONTENT` 等）
2. **尾部截断**（核心逻辑）：按以下正则从上到下扫描，找到最早的匹配位置，**截断其后的所有内容**：
   - `排版：`、`校对：`、`责编：`、`责任编辑：`
   - `声明：`、`版权声明：`、`转载声明：`、`风险提示：`
   - `下载"证券时报"官方APP`、`关注官方微信公众号`
   - `微信编辑器`
3. **逐行过滤**：去除单独成行的「来源：」「作者：」「原标题：」以及含「下载证券时报APP」「关注官方微信公众号」「不构成实质性投资建议」的行
4. **压缩空行**：连续 3+ 空行 → 2 空行

### 3.2 深潮 TechFlow 清洗逻辑

清洗集成在 `fetch_techflow_detail()` 中：

1. **前置去除**（`is_techflow_leadin_text()`）：
   - 匹配 `作者：`、`撰文：`、`编译：`、`深潮导读：`、`By ...`、`Written by ...` 等
   - 匹配到的行直接跳过，不进入 blocks
2. **后置截断**（`is_techflow_hook_text()`）：
   - 匹配 `欢迎加入深潮 TechFlow 官方社群`、`Telegram 订阅群：`、`Twitter 官方账号：` 等
   - 遇到匹配行时 **break**，停止后续提取
3. **硬编码截止文本**：`stop_text = '找到这些创始人——那些不符合本地 VC 体系优化出来的「标准简历模板」的人——是我们现在在做的事。'`
   - 如果正文段落精确匹配此文本，也会 break
   - ⚠️ 这是一段临时硬编码，需要未来移除或改为配置

---

## 四、文章落地（Saver）

清洗后的文章保存到本地，作为持久化记录和去重依据。

### 4.1 券商中国

**函数**：`save_stcn_article(article)`  
**格式**：Markdown  
**路径**：`output/stcn_articles/{日期}_{文章ID}_{标题}.md`  
**内容结构**：
```markdown
# {标题}

**来源**：券商中国
**作者**：{作者}
**发布时间**：{时间}
**原文链接**：{URL}

---

{正文}
```

### 4.2 深潮 TechFlow

**函数**：`save_techflow_article(article)`  
**格式**：JSON  
**路径**：`output/techflow_articles/techflow_{ID}.json`  
**内容结构**：
```json
{
  "article_id": "30885",
  "title": "...",
  "source": "深潮 TechFlow",
  "original_url": "...",
  "cover_src": "https://...",
  "blocks": [
    {"type": "img", "src": "...", "alt": ""},
    {"type": "p", "text": "..."},
    ...
  ]
}
```

---

## 五、去重检查（Dedupe）

**状态文件**：`data/stcn_chainthink_state.json`

```json
{
  "published_ids": [
    "stcn:3708814",
    "stcn:3708898",
    "techflow:30883",
    ...
  ],
  "updated_at": "2026-03-30T10:15:00"
}
```

**去重逻辑**：
1. 脚本启动时加载状态文件，获取 `published_ids` 集合
2. 对每篇文章，检查 `{source_key}:{article_id}` 是否在集合中
3. 已存在的标记为 `already_published`，跳过发布
4. 发布成功后，将新 ID 追加到集合中
5. **发布失败的文章不会写入状态**，下次运行会重试

**深潮额外去重**：除了状态文件，还会检查 `output/techflow_articles/` 目录中是否已有同名 JSON 文件，避免重复抓取。

---

## 六、HTML 构建（Builder）

**函数**：`build_html(article) -> str`

将清洗后的 blocks 转为发布用 HTML：

1. 开头插入 `<p><strong>来源：</strong>{来源名}</p>`
2. 遍历 blocks：
   - `img` → `<p><img src="..." alt="..." /></p>`
   - 深潮封面图（`cover_src`）跳过，不插入正文
   - `p` → `<p>{文本}</p>`
   - `h2`/`h3` → `<h2>{文本}</h2>` / `<h3>{文本}</h3>`
3. 所有文本经过 `html_escape()` 转义（`&` `<` `>` `"`）

**摘要构建**：`build_abstract(article) -> str`  
取所有文本 block 的内容拼接，截取前 180 字符。

---

## 七、封面处理（Cover Handler）— 仅深潮

券商中国文章无封面，深潮文章有封面，需要完整上传流程。

### 7.1 上传流程总览

```
封面图片 URL
     │
     ▼
 download image bytes (requests.get)
     │
     ▼
 compute CRC64 hash (Node.js crc64.js)
     │
     ▼
 POST /ccs/v1/admin/upload_file (申请上传凭证)
     │
     ├──▶ Mode 1: 新对象 → 返回 COS 临时凭证
     │         │
     │         ▼
     │    PUT to Tencent COS (上传图片)
     │         │
     │         ▼
     │    POST /upload_file (confirm=true 通知后端)
     │         │
     │         ▼
     │    返回 confirm_url
     │
     ├──▶ Mode 2: 已存在对象 → 返回 file_info.confirm_url
     │         │
     │         ▼
     │    直接使用 confirm_url，不上传
     │
     └──▶ Mode 3: 预签名 URL → 返回 pre_sign_url
               │
               ▼
          PUT 到预签名 URL
               │
               ▼
          构建 URL: domain + object_key
```

### 7.2 CRC64 哈希计算

**函数**：`compute_crc64_hash(file_path)`  
ChainThink 后端要求文件使用 CRC64 校验。实现方式：

1. 从 `https://admin.chainthink.cn/crc64.js` 下载 CRC64 算法脚本（缓存到 temp）
2. 用 Node.js 执行：读取文件二进制 → CRC64 计算 → 输出哈希字符串
3. 哈希值传给 `upload_file` 接口

### 7.3 上传凭证申请

**函数**：`request_cover_upload(file_name, file_hash, use_pre_sign_url, confirm)`  
**接口**：`POST https://api-v2.chainthink.cn/ccs/v1/admin/upload_file`

请求头：
```
x-token: {JWT token}
x-user-id: 83
X-App-Id: 101
Content-Type: application/json
```

请求体：
```json
{
  "file_name": "cover.jpg",
  "hash": "{CRC64 hash}",
  "use_pre_sign_url": true,
  "confirm": false
}
```

### 7.4 COS 上传

**函数**：`put_file_to_cos(upload, content_bytes)`  
**鉴权方式**：腾讯云 COS HMAC-SHA1 签名

签名构建步骤：
1. `KeyTime = {start};{end}`（使用凭证的 expiration 时间窗口）
2. `SignKey = HMAC-SHA1(SecretKey, KeyTime)` → hex
3. `HttpString = "PUT\n{path}\n\ncontent-length={len}&host={host}\n"`
4. `StringToSign = "sha1\n{KeyTime}\n{SHA1(HttpString)}\n"`
5. `Signature = HMAC-SHA1(SignKey_bytes, StringToSign)` → hex
6. `Authorization = "q-sign-algorithm=sha1&q-ak={SecretId}&q-sign-time={KeyTime}..."`

请求头：
```
Authorization: {签名}
x-cos-security-token: {临时安全令牌}
Content-Type: image/jpeg|png|webp
```

### 7.5 已修复的关键 Bug（2026-03-27）

| Bug | 原因 | 修复 |
|-----|------|------|
| 空字符串覆盖凭证 | `{**upload, **key_data}` 中空值覆盖了有效凭证 | 只合并非空值：`if v not in (None, '', [])` |
| 误判「凭证不完整」 | 已存在对象触发 object_key 检查 | 优先检查 `confirm_url`，有则直接返回 |
| 缺少 Confirm 调用 | COS 上传后未通知后端 | 上传成功后调用 `upload_file(confirm=True)` |

---

## 八、推送发布（Publisher）

**函数**：`publish(article)`  
**接口**：`POST https://api-v2.chainthink.cn/ccs/v1/admin/content/publish`

### 8.1 请求体结构

```json
{
  "id": "0",
  "info": {
    "cover_image": "{confirm_url}"   // 仅深潮有值
  },
  "is_translate": true,
  "translation": {
    "zh-CN": {
      "title": "{标题}",
      "text": "{HTML正文}",
      "abstract": "{摘要，前180字}"
    }
  },
  "type": 5,
  "is_public": false,
  "user_id": "3",
  "as_user_id": "3",
  "is_chain": true,
  "chain_is_calendar": false,
  "chain_calendar_time": {当前时间戳},
  "chain_calendar_tendency": 0,
  "is_push_bian": 2,
  "content_pin_top": 0,
  "strong_content_tags": {},
  "admin_detail": {},
  "chain_fixed_publish_time": 0,
  "chain_airdrop_time": 0,
  "chain_airdrop_time_end": 0
}
```

### 8.2 请求头

```
Accept: application/json
Content-Type: application/json; charset=utf-8
Origin: https://admin.chainthink.cn
Referer: https://admin.chainthink.cn/
x-token: {JWT token}
x-user-id: 83
X-App-Id: 101
```

### 8.3 响应处理

成功响应：
```json
{
  "code": 0,
  "data": {
    "id": "117311835769507840"    // CMS 文章 ID
  }
}
```

- HTTP 200 + `code == 0` → 发布成功，记录 CMS ID 到输出
- 其他 → 抛出 `RuntimeError`，文章不写入去重状态，下次重试

---

## 九、命令行用法

```powershell
# 增量抓取+发布（券商中国）
python stcn_chainthink_pipeline.py --source stcn --incremental

# 增量抓取+发布（深潮）
python stcn_chainthink_pipeline.py --source techflow --incremental

# 两个源一起
python stcn_chainthink_pipeline.py --source all --incremental

# 只抓取今天 07:00 之后的券商中国文章
python stcn_chainthink_pipeline.py --source stcn --since-today-0700

# 重新抓取指定文章（不发布）
python stcn_chainthink_pipeline.py --source stcn --refetch-stcn-url https://www.stcn.com/article/detail/3700146.html

# 重新抓取+强制重发
python stcn_chainthink_pipeline.py --source stcn --refetch-stcn-url https://www.stcn.com/article/detail/3700146.html --republish stcn:3700146
```

---

## 十、定时任务配置

当前通过 OpenClaw cron 管理，每个信源独立一个任务：

| 任务名 | Job ID | 间隔 | 脚本参数 |
|--------|--------|------|----------|
| STCN ChainThink Incremental 10m | `8ebab0d4-...` | 10 分钟 | `--source stcn --incremental` |
| TechFlow ChainThink Incremental 10m | `efe4e837-...` | 10 分钟 | `--source techflow --incremental` |

**已知问题**（2026-03-30）：定时任务通过 isolated session 执行时，连续报 `500 auth_unavailable: no auth available` 错误（连续 3 次超时）。手动在主会话执行脚本正常工作。可能是 isolated session 缺少认证上下文。

---

## 十一、数据文件位置

| 文件 | 路径 | 用途 |
|------|------|------|
| 脚本 | `skills/stcn-chainthink-pipeline/scripts/stcn_chainthink_pipeline.py` | 主入口 |
| SKILL 文档 | `skills/stcn-chainthink-pipeline/SKILL.md` | 技能说明 |
| 去重状态 | `data/stcn_chainthink_state.json` | 已发布文章 ID 列表 |
| 券商中国文章 | `output/stcn_articles/*.md` | 落地的 Markdown 文章 |
| 深潮文章 | `output/techflow_articles/techflow_*.json` | 落地的 JSON 文章 |
| CRC64 算法 | `{temp}/chainthink_crc64.js` | 从后端下载的哈希库（缓存） |

---

## 十二、外部依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python 3 | 3.x | 运行时 |
| requests | - | HTTP 请求 |
| beautifulsoup4 | - | HTML 解析 |
| Node.js | 22.x | 执行 CRC64 哈希计算 |

---

## 十三、关键设计决策

1. **双信源完全解耦**：一个信源失败不影响另一个，定时任务也各自独立
2. **先落地再发布**：文章先保存到本地文件，再从本地文件读取发布，保证可追溯
3. **状态文件去重**：用 JSON 文件而非数据库，简单可靠
4. **发布失败不记录**：只有发布成功的文章才写入状态，失败的文章下次自动重试
5. **封面三种模式兼容**：新对象上传 + 已存在复用 + 预签名 URL，全路径覆盖
6. **券商中国双重过滤**：列表页先按作者过滤，详情页再做一次校验
