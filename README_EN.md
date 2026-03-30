# Article Publisher

> Automated article fetching, cleaning, and publishing pipeline for STCN and TechFlow sources, with one-click publishing to ChainThink CMS.

## Features

### Data Sources
- **STCN** (Securities Times China) — Fetch articles by designated authors (Muyang, Zhoule), auto-parse detail pages
- **TechFlow** — Fetch latest articles, extract body text and cover images
- Incremental fetching with automatic deduplication of published articles

### Article Editor
- Visual article list with card grid layout
- Create new articles online (title, cover, abstract, body)
- Edit existing article content
- Delete unwanted articles

### Cover Image Upload
- Auto-extract TechFlow article covers
- CRC64 hash dedup to avoid redundant uploads
- Pre-signed URL direct upload to Tencent COS
- Upload confirmation mechanism to ensure file readiness

### Publishing Pipeline
- One-click full or single-source publishing
- Dry run preview
- Scheduled task execution (configurable interval)
- Publish status tracking with failure retry

### UI
- Dark / Light theme toggle
- Chinese / English i18n
- Responsive layout for mobile
- Real-time log viewer

## Architecture

```
Frontend (React + Vite)
    │
    ▼ REST API
Backend (FastAPI + Uvicorn)
    │
    ├── Pipeline Engine ──► Fetch / Clean / Dedup
    ├── Cover Upload ─────► CRC64 → COS Upload → Confirm
    └── Publisher ─────────► ChainThink CMS API
```

| Layer | Tech |
|-------|------|
| Frontend | React 19, Vite 6, Pure CSS |
| Backend | FastAPI, Pydantic, Uvicorn |
| Parsing | BeautifulSoup4 |
| Cover Upload | urllib3 → Tencent COS |
| Hash | CRC-64/ECMA-182 (crcmod) |
| Config | YAML + environment variables |

## Project Structure

```
article-publisher/
├── backend/
│   ├── api.py                  # FastAPI entry, routes, static files
│   ├── pipeline.py             # Core pipeline engine
│   ├── crc64.py                # CRC-64 ECMA-182 pure Python
│   ├── crc64_js.py             # ChainThink-compatible CRC64 (crcmod)
│   ├── config/
│   │   └── loader.py           # YAML config loader + env var expansion
│   ├── models/
│   │   └── schemas.py          # Pydantic request/response models
│   ├── routes/
│   │   ├── articles.py         # Article CRUD endpoints
│   │   ├── logs.py             # Log query
│   │   ├── pipeline.py         # Pipeline run control
│   │   └── status.py           # Status query
│   ├── services/
│   │   └── pipeline_service.py # Pipeline service wrapper
│   └── utils/
│       └── logging_config.py   # Logging configuration
├── frontend/
│   └── src/
│       ├── App.jsx             # Main app (Dashboard/Articles/Logs/Editor)
│       ├── api.js              # API client
│       ├── i18n.js             # Internationalization
│       ├── index.css           # Global styles + theme variables
│       ├── contexts.jsx        # React Context (theme/language)
│       └── main.jsx            # Entry point
├── config.yaml.example         # Config template
├── .env.example                # Environment variable template
└── requirements.txt            # Python dependencies
```

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- npm

### Install

```bash
git clone https://github.com/rye-whisky/article-publisher.git
cd article-publisher

# Install Python dependencies
pip install -r requirements.txt

# Build frontend
cd frontend
npm install
npm run build
cd ..
```

### Configuration

```bash
# Copy config template
cp config.yaml.example config.yaml

# Edit config, fill in your ChainThink JWT token
# How to get: Login https://admin.chainthink.cn
# F12 → Network → any request → copy x-token value
```

`config.yaml` example:

```yaml
chainthink:
  api_url: "https://api-v2.chainthink.cn/ccs/v1/admin/content/publish"
  upload_url: "https://api-v2.chainthink.cn/ccs/v1/admin/upload_file"
  token: "your_jwt_token_here"    # or use env var ${CHAINTHINK_TOKEN}
  user_id: "83"
  app_id: "101"
```

### Run

```bash
cd backend
python api.py
```

Open http://localhost:8000 in your browser.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | System status |
| GET | `/api/articles` | Article list (source filter supported) |
| GET | `/api/articles/{id}` | Article detail |
| POST | `/api/articles` | Create article |
| PUT | `/api/articles/{id}` | Update article |
| DELETE | `/api/articles/{id}` | Delete article |
| POST | `/api/run` | Trigger pipeline run |
| POST | `/api/refetch` | Refetch specific articles |
| GET | `/api/logs` | View logs |
| DELETE | `/api/state/{id}` | Remove from published state |
| GET/POST | `/api/scheduler` | Scheduler management |

## Development

```bash
# Backend (auto-reload)
cd backend && python api.py

# Frontend (hot reload dev server)
cd frontend && npm run dev
```

## License

MIT
