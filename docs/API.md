# API 接口文档 / API Reference

Base URL: `http://localhost:8000`

---

## 1. 文章 Articles

### GET /api/articles

获取文章列表（分页，不含正文 blocks）。

**Parameters:**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `source` | string | `"all"` | 信源筛选：`stcn` / `techflow` / `blockbeats` / `chaincatcher` / `all` |
| `page` | int | `1` | 页码 |
| `page_size` | int | `20` | 每页数量 (1-100) |

**Response:**

```json
{
  "total": 80,
  "page": 1,
  "page_size": 20,
  "articles": [
    {
      "article_id": "techflow:30949",
      "source_key": "techflow",
      "title": "文章标题",
      "author": "作者",
      "source": "深潮 TechFlow",
      "publish_time": "2026-04-01 18:00",
      "original_url": "https://...",
      "published": true,
      "abstract": "摘要...",
      "cover_image": "https://cos.xxx/cover.jpg"
    }
  ]
}
```

> 列表接口不返回 `blocks`（正文内容），以节省内存。

---

### GET /api/articles/{article_id}

获取单篇文章完整内容（含 blocks）。

**Response:**

```json
{
  "article_id": "techflow:30949",
  "source_key": "techflow",
  "title": "文章标题",
  "author": "作者",
  "source": "深潮 TechFlow",
  "publish_time": "2026-04-01 18:00",
  "original_url": "https://...",
  "cover_src": "https://...",
  "blocks": [
    {"type": "p", "text": "正文段落"},
    {"type": "h2", "text": "小标题"},
    {"type": "img", "src": "https://...", "alt": "图片"}
  ],
  "published": true,
  "abstract": "...",
  "cover_image": "https://..."
}
```

---

### POST /api/articles

手动创建文章。

**Request Body:**

```json
{
  "title": "文章标题",
  "source_key": "techflow",
  "blocks": [{"type": "p", "text": "正文"}],
  "cover_src": "",
  "abstract": "",
  "author": "",
  "source": "",
  "original_url": ""
}
```

---

### PUT /api/articles/{article_id}

更新文章。

**Request Body:**

```json
{
  "title": "新标题",
  "blocks": [{"type": "p", "text": "新正文"}],
  "cover_src": "",
  "abstract": "",
  "author": ""
}
```

---

### DELETE /api/articles/{article_id}

删除文章文件并从发布状态中移除。

---

## 2. Pipeline 管道

### POST /api/run

触发管道运行（后台线程）。

**Request Body:**

```json
{
  "source": "all",
  "dry_run": false,
  "skip_fetch": false,
  "since_today_0700": false
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `source` | string | `"all"` | 信源：`stcn` / `techflow` / `blockbeats` / `chaincatcher` / `all` |
| `dry_run` | bool | `false` | 模拟运行，不实际发布 |
| `skip_fetch` | bool | `false` | 跳过抓取，只发布本地已有文章 |
| `since_today_0700` | bool | `false` | 仅发布今日 07:00 后的 STCN 文章 |

---

### POST /api/refetch

按 URL 重新抓取指定文章。

**Request Body:**

```json
{
  "source": "blockbeats",
  "blockbeats_urls": ["https://www.theblockbeats.info/news/12345"],
  "chaincatcher_urls": ["https://www.chaincatcher.com/article/12345"],
  "stcn_urls": [],
  "techflow_ids": [],
  "republish": false
}
```

---

## 3. Status 状态

### GET /api/status

获取系统状态。

**Response:**

```json
{
  "running": false,
  "started_at": null,
  "last_result": { "ok": true, "published": [...], "skipped": [...], "failed": [...] },
  "total_published": 100,
  "last_updated": "2026-04-02T10:38:22",
  "schedules": {
    "stcn": { "source_key": "stcn", "enabled": false, "interval_minutes": 60, "next_run_time": null },
    "techflow": { ... }
  },
  "sources": {
    "stcn": { "enabled": true, "authors": ["沐阳", "周乐"] },
    "techflow": { "enabled": true }
  }
}
```

---

### GET /api/state

获取完整去重状态（published_ids 列表）。

---

### DELETE /api/state/{article_id}

从发布状态中移除指定文章（允许重新发布）。

---

## 4. Scheduler 调度

### GET /api/schedules

获取所有信源的调度配置。

---

### PUT /api/schedules/{source_key}

更新指定信源的调度配置。

**Request Body:**

```json
{
  "enabled": true,
  "interval_minutes": 60
}
```

---

## 5. Logs 日志

### GET /api/logs

获取最近日志。

**Parameters:** `lines` (int, default 100, max 1000)

---

### GET /api/logs/stream

SSE 实时日志流。客户端用 `EventSource` 连接。

---

## 6. Memory 内存

### GET /api/memory/info

获取进程和系统内存使用情况。

**Response:**

```json
{
  "process": { "rss_mb": 85.32, "vms_mb": 210.5 },
  "system": { "total_mb": 2048.0, "available_mb": 980.0, "percent": 52.1 }
}
```

---

### POST /api/memory/clear

清除所有内存缓存，释放 RAM。

---

## 通用说明

- 所有接口返回 JSON
- 错误响应格式：`{"detail": "error message"}`
- Pipeline 运行中时，`/api/run` 和 `/api/refetch` 返回 `409`
- 文章 ID 格式：`{source_key}:{raw_id}`（如 `stcn:3722246`）
