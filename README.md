# Article Publisher

**[中文](#中文) | [English](#english)**

---

<a id="english"></a>

## Overview

Automated multi-source article fetching, cleaning, and publishing system for Web3/Crypto news. Supports 4 sources with one-click or scheduled publishing to ChainThink CMS.

## Data Sources

| Source | Key | Format | Notes |
|--------|-----|--------|-------|
| 券商中国 STCN | `stcn` | HTML scraping | Author filter (沐阳, 周乐) |
| 深潮 TechFlow | `techflow` | JSON API | Full content + cover |
| 律动 BlockBeats | `blockbeats` | SPA (Nuxt.js) | Regex extraction from `__NUXT__` |
| 链捕手 ChainCatcher | `chaincatcher` | SPA (Vue.js) | `.rich_text_content` extraction |

## Architecture

```
Frontend (React 19 + Vite 6)
    │  REST / SSE
    ▼
Backend (FastAPI)
    ├── routes/        — API endpoints (articles, pipeline, scheduler, logs, memory)
    ├── services/      — PipelineService, ArticleStore, Publisher
    ├── pipelines/     — BaseScraper → STCN / TechFlow / BlockBeats / ChainCatcher
    └── utils/         — COSUploader, LogBroadcaster, LogRotation
```

## Project Structure

```
article-publisher/
├── backend/
│   ├── api.py                  # FastAPI entry point
│   ├── cli.py                  # CLI mode
│   ├── pipelines/
│   │   ├── base.py             # BaseScraper abstract class
│   │   ├── stcn.py             # STCN scraper (HTML)
│   │   ├── techflow.py         # TechFlow scraper (JSON API)
│   │   ├── blockbeats.py       # BlockBeats scraper (SPA)
│   │   └── chaincatcher.py     # ChainCatcher scraper (SPA)
│   ├── services/
│   │   ├── pipeline_service.py # Pipeline orchestration & scheduling
│   │   ├── article_store.py    # Article CRUD + pagination
│   │   └── publisher.py        # COS upload + CMS publish
│   ├── routes/
│   │   ├── articles.py         # Article CRUD + pagination
│   │   ├── pipeline.py         # Run / refetch
│   │   ├── scheduler.py        # Per-source scheduling
│   │   ├── status.py           # Status / state
│   │   ├── logs.py             # Logs + SSE stream
│   │   └── memory.py           # Memory monitoring & cleanup
│   └── utils/
│       ├── cos.py              # Tencent COS uploader
│       ├── log_broadcaster.py  # SSE log broadcasting
│       └── logging_config.py   # Logging setup
├── frontend/
│   └── src/
│       ├── App.jsx             # Main SPA (Dashboard / Articles / Logs)
│       ├── api.js              # API client
│       └── i18n.js             # i18n translations
├── database/                   # SQLAlchemy models (optional DB mode)
├── deploy/                     # Deployment scripts & nginx config
├── docs/                       # Documentation
├── test/                       # Local test scripts (not unit tests)
├── config.yaml                 # Runtime configuration
└── requirements.txt
```

## Quick Start

```bash
git clone https://github.com/rye-whisky/article-publisher.git
cd article-publisher

# Install dependencies
pip install -r requirements.txt

# Build frontend
cd frontend && npm install && npm run build && cd ..

# Configure
cp config.yaml.example config.yaml
# Edit config.yaml — fill in ChainThink token, COS credentials, etc.

# Run
cd backend && python api.py
# Open http://localhost:8000
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite 6, Pure CSS |
| Backend | FastAPI, Pydantic, Uvicorn |
| Parsing | BeautifulSoup4 |
| Upload | Tencent COS (pre-signed URL) |
| Hash | CRC-64/ECMA-182 |

## License

MIT

---

<a id="中文"></a>

## 概述

多信源加密资讯自动抓取、清洗与发布系统，支持 4 个信源，一键或定时发布到 ChainThink CMS。

## 数据源

| 来源 | Key | 格式 | 备注 |
|------|-----|------|------|
| 券商中国 STCN | `stcn` | HTML 抓取 | 作者过滤（沐阳、周乐） |
| 深潮 TechFlow | `techflow` | JSON API | 全文+封面 |
| 律动 BlockBeats | `blockbeats` | SPA (Nuxt.js) | 从 `__NUXT__` 提取文章ID |
| 链捕手 ChainCatcher | `chaincatcher` | SPA (Vue.js) | `.rich_text_content` 提取 |

## 项目结构

```
article-publisher/
├── backend/           # FastAPI 后端
├── frontend/          # React 前端
├── database/          # 数据库模型（可选）
├── deploy/            # 部署脚本 & nginx 配置
├── docs/              # 项目文档
├── test/              # 本地测试脚本（非单元测试）
├── config.yaml        # 运行配置
└── requirements.txt   # Python 依赖
```

> `test/` 目录中的脚本是用于本地调试和验证各 pipeline 的独立脚本，不是自动化测试套件。

## 快速开始

```bash
git clone https://github.com/rye-whisky/article-publisher.git
cd article-publisher
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
cp config.yaml.example config.yaml   # 编辑填入 token
cd backend && python api.py
# 打开 http://localhost:8000
```

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19, Vite 6, 纯 CSS |
| 后端 | FastAPI, Pydantic, Uvicorn |
| 解析 | BeautifulSoup4 |
| 上传 | 腾讯 COS 预签名 URL |
| 哈希 | CRC-64/ECMA-182 |

## 许可证

MIT
