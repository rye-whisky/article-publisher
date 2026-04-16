# AI 文章管线架构文档

## 1. 整体架构

```
浏览器
  │
  ├─ /api/ai/* ──→ ai_articles Router ──→ AiPipelineService
  │                                           │
  │                                           ├─ Kr36Scraper      (36氪 AI 资讯)
  │                                           ├─ BaoyuScraper     (宝玉的分享)
  │                                           ├─ ClaudeScraper    (Claude 官方博客)
  │                                           └─ BestBlogsScraper (BestBlogs.dev)
  │                                           │
  │                                           ├─ ArticleDatabase (SQLite)
  │                                           └─ Publisher (推送 CMS，复用区块链管线)
  │
  └─ /api/*     ──→ 原有区块链管线 Router ──→ PipelineService
```

两条管线（区块链 / AI）完全平行，共享以下基础设施：

- `RunState` — 线程安全的运行状态追踪（含 10 分钟自动超时）
- `SourceScheduleState` — 每源定时调度器（threading.Timer）
- `ArticleDatabase` — 同一个 SQLite 数据库，AI 来源通过 `source_key` 区分
- `Publisher` — AI 文章推送复用区块链管线的 Publisher + COS 上传

### 文件索引

| 用途 | 路径 |
|------|------|
| 应用入口 | `backend/api.py` |
| 爬虫基类 | `backend/pipelines/base.py` |
| AI 爬虫工厂 | `backend/ai_pipelines/__init__.py` |
| 36氪爬虫 | `backend/ai_pipelines/kr36.py` |
| 宝玉爬虫 | `backend/ai_pipelines/baoyu.py` |
| Claude 爬虫 | `backend/ai_pipelines/claude.py` |
| BestBlogs 爬虫 | `backend/ai_pipelines/bestblogs.py` |
| AI 编排服务 | `backend/services/ai_pipeline_service.py` |
| API 路由 | `backend/routes/ai_articles.py` |
| 路由注册 | `backend/routes/__init__.py` |
| 数据库服务 | `backend/services/database.py` |

---

## 2. 初始化流程

**`backend/api.py`** — 应用启动时创建并注册 AI 管线：

```python
# lifespan() 中，在区块链管线之后初始化
ai_svc = AiPipelineService.create(BASE_DIR, db)
app.state.ai_pipeline_service = ai_svc

# 从数据库恢复已启用的定时调度
ai_svc.restore_schedules()

# 为没有保存过配置的 AI 来源设置默认调度（15 分钟，默认禁用）
for src in ["bestblogs"]:
    existing = db.get_schedule(f"ai_{src}")
    if existing is None:
        db.save_schedule(f"ai_{src}", False, 15)
```

**`backend/ai_pipelines/__init__.py`** — 工厂函数根据配置创建爬虫实例：

```python
def create_ai_scrapers(cfg, session, base_dir) -> dict:
    ai_cfg = cfg.get("ai_sources", {})
    scrapers = {}

    # kr36: 默认启用
    if ai_cfg.get("kr36", {}).get("enabled", True):
        scrapers["kr36"] = Kr36Scraper(ai_cfg.get("kr36", {}), session, output_dir)

    # baoyu: 默认启用
    if ai_cfg.get("baoyu", {}).get("enabled", True):
        scrapers["baoyu"] = BaoyuScraper(ai_cfg.get("baoyu", {}), session, output_dir)

    # claude: 默认启用
    if ai_cfg.get("claude", {}).get("enabled", True):
        scrapers["claude"] = ClaudeScraper(ai_cfg.get("claude", {}), session, output_dir)

    # bestblogs: 默认禁用
    if ai_cfg.get("bestblogs", {}).get("enabled", False):
        scrapers["bestblogs"] = BestBlogsScraper(...)

    return scrapers
```

config.yaml 中的配置结构：

```yaml
ai_sources:
  kr36:
    enabled: true
  baoyu:
    enabled: true
    rss_url: "https://baoyu.io/feed.xml"   # 可覆盖
  claude:
    enabled: true
  bestblogs:
    enabled: false
    min_score: 70

paths:
  ai_articles_output: "output/ai_articles"
```

---

## 3. 三条爬虫详解

### 3.1 36氪 AI 资讯（kr36）

**文件**: `backend/ai_pipelines/kr36.py`

**数据源**: `https://www.36kr.com/information/AI/`

**核心难点**: 36氪是 Vue SSR 页面，文章列表和详情的数据都嵌在 `window.initialState={...}` 这个 JS 全局变量中，没有独立 API。需要从 HTML 中提取 JSON。

#### 列表抓取 (`parse_list`)

```python
def parse_list(self) -> list[dict]:
    r = self.session.get("https://www.36kr.com/information/AI/", timeout=30)
    # 编码处理：只在服务端未声明时才用 apparent_encoding
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"

    state = self._extract_initial_state(r.text)
    item_list = (
        state.get("information", {})
        .get("informationList", {})
        .get("itemList", [])
    )

    for item in item_list:
        mat = item.get("templateMaterial", {})
        item_id = str(mat.get("itemId", ""))
        title = mat.get("widgetTitle", "").strip()
        summary = mat.get("summary", "").strip()
        author = mat.get("authorName", "").strip()
        ts = mat.get("publishTime")  # 毫秒级时间戳
        publish_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
```

#### initialState 解析 (`_extract_initial_state`)

使用 `json.JSONDecoder.raw_decode()` 从 HTML 中安全提取 JSON，不需要用正则截断：

```python
@staticmethod
def _extract_initial_state(html: str) -> dict | None:
    m = re.search(r"window\.initialState\s*=\s*", html)
    if not m:
        return None
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(html, m.end())
    return obj
```

#### 详情抓取 (`fetch_detail`)

访问 `https://www.36kr.com/p/{item_id}`，同样从 initialState 提取：

```python
def fetch_detail(self, item: dict) -> dict:
    state = self._extract_initial_state(r.text)
    detail = (
        state.get("articleDetail", {})
        .get("articleDetailData", {})
        .get("data", {})
    )
    content_html = detail.get("widgetContent", "")
    blocks = self._html_to_blocks(content_html)

    # 用详情页的更完整字段覆盖列表页数据
    if detail.get("widgetTitle"):
        item["title"] = detail["widgetTitle"]
    if detail.get("author"):
        item["author"] = str(detail["author"]).strip()
```

#### HTML 转 blocks (`_html_to_blocks`)

```python
@staticmethod
def _html_to_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for el in soup.find_all(True):
        if el.name == "img":
            src = el.get("src") or el.get("data-src") or ""
            if src.startswith("/"):
                src = "https://www.36kr.com" + src  # 相对路径补全
            blocks.append({"type": "img", "src": src})
        elif el.name in ("p", "h2", "h3", "h4", "li"):
            # 跳过嵌套元素，避免重复提取
            if el.parent and el.parent.name in ("p", "h2", "h3", "h4", "li", "ul", "ol"):
                continue
            tag = el.name if el.name in ("h2", "h3", "h4") else "p"
            blocks.append({"type": tag, "text": text})
        elif el.name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                blocks.append({"type": "p", "text": text})
    return blocks
```

---

### 3.2 宝玉的分享（baoyu）

**文件**: `backend/ai_pipelines/baoyu.py`

**数据源**: RSS `https://baoyu.io/feed.xml` + HTML 详情页

**两阶段抓取**: RSS 提供列表（标题、链接、摘要、发布时间），HTML 页面提供完整正文。

#### 列表抓取 (`parse_list`) — RSS XML 解析

```python
def parse_list(self) -> list[dict]:
    r = self.session.get(self.rss_url, timeout=30)
    root = ET.fromstring(r.text)
    items_el = root.find("channel").findall("item")

    for item in items_el:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        description = item.findtext("description", "").strip()

        # 从 URL 最后一段提取 ID
        # https://baoyu.io/post/abc123 → raw_id = "abc123"
        raw_id = link.rstrip("/").rsplit("/", 1)[-1]

        results.append({
            "article_id": f"baoyu:{raw_id}",
            "abstract": description,  # RSS description 作为摘要
            "blocks": [],             # 由 fetch_detail 填充
        })
```

#### 详情抓取 (`fetch_detail`)

```python
def fetch_detail(self, item: dict) -> dict:
    html = self.fetch_html(url)  # BaseScraper 提供的方法
    blocks = self._html_to_blocks(html)
```

#### HTML 转 blocks (`_html_to_blocks`)

```python
@staticmethod
def _html_to_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    # 定位正文区域，优先级：article > prose class div > main
    article = (
        soup.find("article")
        or soup.find("div", class_=lambda c: c and "prose" in str(c))
        or soup.find("main")
    )

    for el in article.find_all(["p", "h2", "h3", "h4", "img", "ul", "ol"]):
        # blockquote 内的段落也提取
        if el.parent.name == "blockquote" and el.name == "p":
            blocks.append({"type": "p", "text": text})

        # 相对路径图片补全
        if src.startswith("/"):
            src = "https://baoyu.io" + src
```

---

### 3.3 Claude 官方博客（claude）

**文件**: `backend/ai_pipelines/claude.py`

**数据源**: `https://claude.com/blog`

**两阶段抓取**: 列表页提取文章链接，详情页提取完整内容。

#### 列表抓取 (`parse_list`)

```python
def parse_list(self) -> list[dict]:
    soup = BeautifulSoup(r.text, "html.parser")

    # 匹配 /blog/xxx 格式的链接，排除 /blog 本身
    for link in soup.find_all("a", href=re.compile(r"^/blog/[^/]+$")):
        slug = href.rstrip("/").rsplit("/", 1)[-1]  # URL 段作为 ID
        title_elem = link.find("h3")                 # 标题在链接内 h3

        results.append({
            "article_id": f"claude:{slug}",
            "original_url": f"https://claude.com{href}",
        })
```

#### 详情抓取 (`fetch_detail`)

```python
def fetch_detail(self, item: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # 标题
    title_elem = soup.find("h1")

    # 发布时间 — 优先 <time> 标签，其次 class 含 time/date 的 span
    time_elem = soup.find("time") or soup.find("span", class_=re.compile(r"time|date"))
    if time_elem:
        time_text = time_elem.get("datetime") or time_elem.get_text(strip=True)
        item["publish_time"] = self._parse_date(time_text)

    # 摘要 — meta description
    desc_elem = soup.find("meta", attrs={"name": "description"})

    # 封面图 — og:image
    og_img = soup.find("meta", property="og:image")

    # 正文 blocks
    blocks = self._extract_content(soup)
```

#### 内容提取 (`_extract_content`)

```python
@staticmethod
def _extract_content(soup) -> list[dict]:
    # 定位正文容器，优先级：article > main > 含 content/article/post class 的 div
    article = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"content|article|post", re.I))
    )
    if not article:
        article = soup  # 兜底

    for el in article.find_all(["h2", "h3", "h4", "p", "img", "ul", "ol"]):
        # 跳过嵌套元素
        if el.parent and el.parent.name in ("h2", "h3", "h4", "p", "li", "ul", "ol"):
            continue
        # ...提取 block
```

#### 日期解析 (`_parse_date`)

支持多种日期格式：

```python
@staticmethod
def _parse_date(date_str: str) -> str:
    # "2026-04-10"       → "2026-04-10"
    # "Apr 10, 2026"     → "2026-04-10"
    # "April 10, 2026"   → "2026-04-10"
    # "10 Apr 2026"      → "2026-04-10"
    # "2026-04-10T12:00" → "2026-04-10"
```

---

## 4. AiPipelineService 编排层

**文件**: `backend/services/ai_pipeline_service.py`

与区块链管线的 `PipelineService` 平行，提供抓取编排、定时调度、文章查询等功能。

### 4.1 抓取流程 (`ingest`)

```
ingest(source="all")
  │
  for source_key, scraper in scrapers:
    │
    ├─ items = scraper.parse_list()          # 抓列表
    │
    for item in items:
      │
      ├─ article = scraper.fetch_detail(item) # 抓详情
      ├─ scraper.save(article)                # 存 JSON 文件
      └─ database.insert_or_update(db_record) # 存 SQLite
```

去重逻辑：通过 `database.get_by_article_id()` 检查是否已存在。

### 4.2 文章查询 (`list_articles`)

```python
def list_articles(self, source="all", category=None, min_score=None,
                  tag=None, page=1, page_size=20) -> tuple[int, list[dict]]:
    # 自动限定为 AI 来源（WHERE source_key IN (kr36, baoyu, claude, bestblogs)）
    # 支持按来源、分类、最低分数、标签过滤
    # 标签匹配：tags LIKE '%"人工智能"%'（JSON 数组模糊查询）
```

### 4.3 定时调度

复用 `SourceScheduleState`，和区块链管线机制完全一致：

- 调度配置持久化到数据库 `settings` 表，key 格式 `schedule_ai_{source_key}`
- `restore_schedules()` 启动时从数据库恢复已启用的定时器
- 每个来源独立的 `threading.Timer`，间隔可配置（1-1440 分钟）

```python
def set_source_schedule(self, source_key, enabled, interval_minutes):
    sched.set_config(enabled, interval_minutes,
                     run_fn=lambda: self._source_scheduler_run(source_key))
    self.database.save_schedule(f"ai_{source_key}", enabled, interval_minutes)
```

### 4.4 状态查询 (`get_status`)

```python
{
    "running": false,
    "started_at": "2026-04-14T10:00:00",
    "last_result": { "ok": true, "summary": { "kr36": {"new": 3, "total": 10} } },
    "total": 50,          # AI 文章总数
    "published": 5,       # 已推送数
    "sources": ["kr36", "baoyu", "claude"]
}
```

---

## 5. API 路由

**文件**: `backend/routes/ai_articles.py`

挂载前缀：`/api/ai`，所有写操作需 admin 权限。

### 端点一览

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/ai/articles` | 公开 | 分页列表（source/category/min_score/tag 过滤） |
| GET | `/api/ai/articles/{id}` | 公开 | 文章详情（含 blocks） |
| PUT | `/api/ai/articles/{id}` | admin | 更新文章字段 |
| DELETE | `/api/ai/articles/{id}` | admin | 删除文章 |
| POST | `/api/ai/articles/{id}/ai-edit` | admin | AI 编辑正文（返回编辑结果，不自动保存） |
| POST | `/api/ai/articles/{id}/publish` | admin | 推送到 CMS |
| POST | `/api/ai/articles/batch-delete` | admin | 批量删除 |
| GET | `/api/ai/status` | 公开 | 运行状态 + 调度状态 + 统计 |
| POST | `/api/ai/run` | admin | 后台线程触发抓取 |
| POST | `/api/ai/cancel` | admin | 取消运行中的抓取 |
| GET | `/api/ai/schedules` | 公开 | 获取所有来源调度状态 |
| PUT | `/api/ai/schedules/{source_key}` | admin | 更新调度配置 |
| GET | `/api/ai/tags` | 公开 | 获取所有标签 |
| GET | `/api/ai/stats` | 公开 | 统计信息 |

### AI 编辑流程 (`/api/ai/articles/{id}/ai-edit`)

```
前端传入: { system_prompt: "Prompt 1 (来自设置页)", user_prompt: "Prompt 2 (用户输入)" }
    │
    ├─ 1. 从 article.blocks 构建 HTML 文本: <p>段落</p>\n<h2>标题</h2>
    │
    ├─ 2. 合并 Prompt: final_prompt = system_prompt + "\n\n" + user_prompt
    │
    ├─ 3. 调用 LLM: ai_edit_text(body_text, database, system_prompt=final_prompt)
    │     └─ LLM 路由: 根据 DB 设置 llm_edit_factory/api_url/api_key/model 选择模型
    │
    └─ 4. 返回: { ok: true, edited_text: "AI 编辑后的 HTML 文本" }
          （前端拿到后用户手动确认再保存）
```

### CMS 推送 (`/api/ai/articles/{id}/publish`)

```python
# 复用区块链管线的 Publisher
pipeline_svc = request.app.state.pipeline_service
result = pipeline_svc.publisher.publish(article)
svc.database.mark_published(article_id, result["cms_id"])
```

---

## 6. 数据库 Schema

**文件**: `backend/services/database.py`

AI 文章和区块链文章共用 `articles` 表，通过 `source_key` 区分。AI 管线新增了 5 个字段：

```sql
-- 基础字段（和区块链文章共用）
articles (
    id              INTEGER PRIMARY KEY,
    article_id      TEXT NOT NULL UNIQUE,    -- "kr36:123456" / "baoyu:abc" / "claude:slug"
    source_key      TEXT NOT NULL,           -- "kr36" / "baoyu" / "claude" / "bestblogs"
    raw_id          TEXT,                    -- "123456" / "abc" / "slug"
    title           TEXT NOT NULL,
    source          TEXT,                    -- "36氪" / "宝玉的分享" / "Claude"
    author          TEXT,
    publish_time    TEXT,
    original_url    TEXT,
    cover_src       TEXT,
    blocks          TEXT,                    -- JSON: [{type:"p", text:"..."}, {type:"img", src:"..."}]
    abstract        TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    published_at    TEXT,
    cms_id          TEXT,

    -- AI 管线新增字段
    score               INTEGER,             -- BestBlogs 评分 (0-100)
    tags                TEXT,                -- JSON 数组: ["人工智能", "GPT"]
    category            TEXT,                -- "人工智能" / "软件编程" / "商业科技"
    language            TEXT DEFAULT 'zh',   -- "zh" / "en"
    one_sentence_summary TEXT                -- 一句话摘要
)
```

### article_id 格式

每个来源的 ID 格式：

| 来源 | article_id 示例 | raw_id |
|------|-----------------|--------|
| 36氪 | `kr36:1234567890` | 文章数字 ID |
| 宝玉 | `baoyu:weekly-digest-42` | URL 最后一段 |
| Claude | `claude:claude-4-opus` | URL slug |
| BestBlogs | `bestblogs:2c79231c` | guid 去掉 RAW_ 前缀 |

---

## 7. Block 数据结构

所有爬虫的输出统一为 `blocks` 列表：

```json
[
    { "type": "h2", "text": "主要观点" },
    { "type": "h3", "text": "子标题" },
    { "type": "p",  "text": "正文段落..." },
    { "type": "img", "src": "https://example.com/img.jpg" }
]
```

前端渲染逻辑 (`App.jsx`)：

```jsx
{a.blocks?.map((b, i) => {
    if (b.type === 'img') return <p key={i}><img src={b.src} /></p>
    if (b.type === 'h2') return <h3 key={i}>{b.text}</h3>
    if (b.type === 'h3') return <h4 key={i}>{b.text}</h4>
    if (b.type === 'h4') return <h5 key={i}>{b.text}</h5>
    return <p key={i}>{b.text}</p>
})}
```

---

## 8. 前端架构

### 双模式切换

前端通过 `localStorage.app_mode` 在两个模式间切换：

- `blockchain` — 区块链文章管线（仪表盘 / 文章列表 / 提示词管理 / 日志）
- `ai` — AI 文章管线（AI 仪表盘 / AI 文章列表 / 日志）

```jsx
// App.jsx - 模式切换
const handleModeChange = (newMode) => {
    setMode(newMode)
    localStorage.setItem('app_mode', newMode)
    setPage(newMode === 'ai' ? 'ai-dashboard' : 'dashboard')
}
```

### 页面组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `AiDashboardPage` | `frontend/src/App.jsx:1662-1915` | AI 仪表盘（状态、操作、调度、统计） |
| `AiArticlesPage` | `frontend/src/App.jsx:1327-1654` | AI 文章列表（分页、筛选、批量管理） |
| `ArticleEditor` | `frontend/src/App.jsx:404-640` | 文章编辑器（区块链/AI 共用，`isAiArticle` 区分） |

### API 调用

**文件**: `frontend/src/api.js`

所有 AI 相关 API 以 `/ai/` 为前缀：

```javascript
getAiArticles: (params) => request(`/ai/articles?${qs}`),
getAiArticle: (id) => request(`/ai/articles/${encodeURIComponent(id)}`),
updateAiArticle: (id, data) => request(`/ai/articles/${id}`, { method: 'PUT', ... }),
deleteAiArticle: (id) => request(`/ai/articles/${id}`, { method: 'DELETE' }),
ingestAiArticles: () => request('/ai/ingest', { method: 'POST' }),
publishAiArticle: (id) => request(`/ai/articles/${id}/publish`, { method: 'POST' }),
aiEditAiArticle: (id, sp, up) => request(`/ai/articles/${id}/ai-edit`, { method: 'POST', ... }),
getAiStatus: () => request('/ai/status'),
runAiIngest: (source) => request('/ai/run', { method: 'POST', ... }),
cancelAiRun: () => request('/ai/cancel', { method: 'POST' }),
updateAiSchedule: (key, en, min) => request(`/ai/schedules/${key}`, { method: 'PUT', ... }),
getAiTags: () => request('/ai/tags'),
getAiStats: () => request('/ai/stats'),
batchDeleteAiArticles: (ids) => request('/ai/articles/batch-delete', { method: 'POST', ... }),
```

---

## 9. 部署

### systemd 服务

**文件**: `article-publisher.service`

```ini
[Service]
User=article-publisher
WorkingDirectory=/opt/article-publisher
ExecStart=/opt/article-publisher/venv/bin/python backend/api.py
Restart=always
RestartSec=10
```

安全加固：`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=true`

### 安装脚本

**文件**: `install-service.sh`

```bash
sudo bash install-service.sh
# 自动完成：创建用户、venv、安装依赖、配置权限、注册服务
```

---

## 10. 与区块链管线的对比

| 维度 | 区块链管线 | AI 管线 |
|------|-----------|---------|
| 服务类 | `PipelineService` | `AiPipelineService` |
| 路由前缀 | `/api/` | `/api/ai/` |
| 爬虫目录 | `backend/pipelines/` | `backend/ai_pipelines/` |
| 来源 | stcn, techflow, blockbeats, chaincatcher, odaily | kr36, baoyu, claude, bestblogs |
| 存储 | JSON 文件 + SQLite | JSON 文件 + SQLite（共用表） |
| 调度存储 | `schedule_{source}` | `schedule_ai_{source}` |
| 发布 | Publisher + COS 上传 | 复用同一 Publisher |
| AI 摘要 | 抓取后自动生成 | RSS/页面自带摘要 |
| 前端模式 | blockchain | ai |
