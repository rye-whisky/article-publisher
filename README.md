# Article Publisher

**[中文](README_CN.md) | [English](README_EN.md)**

> Automated article fetching, cleaning, and publishing pipeline for STCN and TechFlow sources.

## Quick Start

```bash
git clone https://github.com/rye-whisky/article-publisher.git
cd article-publisher
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
cp config.yaml.example config.yaml   # then edit config.yaml
cd backend && python api.py
# Open http://localhost:8000
```

## Features

- **Multi-source fetching** — STCN (Securities Times) and TechFlow articles
- **Online editor** — Create, edit, delete articles with cover image support
- **Cover upload** — CRC64 hash dedup, pre-signed URL upload to Tencent COS
- **One-click publish** — Publish to ChainThink CMS with deduplication
- **Scheduler** — Configurable interval-based auto-publishing
- **i18n & themes** — Chinese/English, dark/light mode

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite 6 |
| Backend | FastAPI, Pydantic |
| Parsing | BeautifulSoup4 |
| Upload | urllib3, Tencent COS |
| Hash | CRC-64/ECMA-182 (crcmod) |

## License

MIT
