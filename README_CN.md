# Article Publisher

> 自动化文章抓取、清洗与发布系统，支持 STCN（券商中国）和 TechFlow（深潮）文章源，一键发布到 ChainThink CMS 平台。

## 功能特性

### 数据源
- **券商中国（STCN）** — 抓取指定作者（沐阳、周乐）的文章列表，自动解析详情页
- **深潮 TechFlow** — 抓取最新文章列表，提取正文、封面图
- 支持增量抓取，自动去重已发布文章

### 文章编辑
- 可视化文章列表（卡片网格布局）
- 在线创建新文章（标题、封面、摘要、正文）
- 编辑已有文章内容
- 删除不需要的文章

### 封面图片上传
- 自动提取 TechFlow 文章封面
- CRC64 哈希去重，避免重复上传
- 预签名 URL 直传腾讯云 COS，安全高效
- 上传确认（confirm）机制，确保文件就绪

### 发布管道
- 一键运行全量或单源发布
- 模拟运行（Dry Run）预览
- 定时任务调度（可配置间隔）
- 发布状态追踪，失败重试

### 界面
- 深色/浅色主题切换
- 中/英文双语切换
- 响应式布局，适配移动端
- 实时日志查看

## 技术架构

```
Frontend (React + Vite)
    │
    ▼ REST API
Backend (FastAPI + Uvicorn)
    │
    ├── Pipeline Engine ──► 抓取 / 清洗 / 去重
    ├── Cover Upload ─────► CRC64 → COS 上传 → Confirm
    └── Publisher ─────────► ChainThink CMS API
```

| 层 | 技术 |
|---|---|
| 前端 | React 19, Vite 6, 纯 CSS |
| 后端 | FastAPI, Pydantic, Uvicorn |
| 文章解析 | BeautifulSoup4 |
| 封面上传 | urllib3 → 腾讯云 COS |
| 哈希算法 | CRC-64/ECMA-182 (crcmod) |
| 配置管理 | YAML + 环境变量 |

## 项目结构

```
article-publisher/
├── backend/
│   ├── api.py                  # FastAPI 入口，路由注册，静态文件
│   ├── pipeline.py             # 核心管道引擎
│   ├── crc64.py                # CRC-64 ECMA-182 纯 Python 实现
│   ├── crc64_js.py             # ChainThink 兼容 CRC64（crcmod）
│   ├── config/
│   │   └── loader.py           # YAML 配置加载 + 环境变量展开
│   ├── models/
│   │   └── schemas.py          # Pydantic 请求/响应模型
│   ├── routes/
│   │   ├── articles.py         # 文章 CRUD 端点
│   │   ├── logs.py             # 日志查询
│   │   ├── pipeline.py         # 管道运行控制
│   │   └── status.py           # 状态查询
│   ├── services/
│   │   └── pipeline_service.py # 管道服务封装
│   └── utils/
│       └── logging_config.py   # 日志配置
├── frontend/
│   └── src/
│       ├── App.jsx             # 主应用（Dashboard/Articles/Logs/Editor）
│       ├── api.js              # API 客户端
│       ├── i18n.js             # 国际化翻译
│       ├── index.css           # 全局样式 + 主题变量
│       ├── contexts.jsx        # React Context（主题/语言）
│       └── main.jsx            # 入口
├── config.yaml.example         # 配置文件模板
├── .env.example                # 环境变量模板
└── requirements.txt            # Python 依赖
```

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- npm

### 安装

```bash
# 克隆仓库
git clone https://github.com/rye-whisky/article-publisher.git
cd article-publisher

# 安装 Python 依赖
pip install -r requirements.txt

# 构建前端
cd frontend
npm install
npm run build
cd ..
```

### 配置

```bash
# 复制配置模板
cp config.yaml.example config.yaml

# 编辑配置，填入 ChainThink JWT token
# 获取方式：登录 https://admin.chainthink.cn
# F12 → Network → 任意请求 → 复制 x-token 的值
```

`config.yaml` 示例：

```yaml
chainthink:
  api_url: "https://api-v2.chainthink.cn/ccs/v1/admin/content/publish"
  upload_url: "https://api-v2.chainthink.cn/ccs/v1/admin/upload_file"
  token: "your_jwt_token_here"    # 或使用环境变量 ${CHAINTHINK_TOKEN}
  user_id: "83"
  app_id: "101"
```

### 启动

```bash
cd backend
python api.py
```

打开浏览器访问 http://localhost:8000

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态 |
| GET | `/api/articles` | 文章列表（支持 source 筛选） |
| GET | `/api/articles/{id}` | 文章详情 |
| POST | `/api/articles` | 创建文章 |
| PUT | `/api/articles/{id}` | 更新文章 |
| DELETE | `/api/articles/{id}` | 删除文章 |
| POST | `/api/run` | 触发管道运行 |
| POST | `/api/refetch` | 重新抓取指定文章 |
| GET | `/api/logs` | 查看日志 |
| DELETE | `/api/state/{id}` | 从发布状态中移除 |
| GET/POST | `/api/scheduler` | 定时任务管理 |

## 开发模式

```bash
# 后端（自动重载）
cd backend && python api.py

# 前端（热更新开发服务器）
cd frontend && npm run dev
```

## License

MIT
