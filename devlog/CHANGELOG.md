# Article Publisher - 开发日志

> 本日志用于记录项目进展、交接开发工作。跨成员开发前请先阅读本文档。

最后更新：2026-04-09 (18:30)

---

## 项目概述

**ChainThink Article Publisher** — 多源文章抓取、清洗、AI 处理、自动发布系统

- **后端**: FastAPI + Python
- **前端**: React + Vite
- **数据库**: SQLite (backend/services/database.py)
- **部署**: 阿里云服务器 + systemd

### 核心流程

```
抓取 (pipelines/) → 存储 (services/database.py) → AI摘要/编辑 (services/llm.py → llm_service.py) → COS上传 (utils/cos.py) → CMS发布 (services/publisher.py)
```

### 支持的数据源

| 源 | Pipeline | 状态 |
|---|---|---|
| STCN (证券时报) | `stcn.py` | ✓ |
| TechFlow | `techflow.py` | ✓ |
| BlockBeats (律动) | `blockbeats.py` | ✓ |
| ChainCatcher | `chaincatcher.py` | ✓ |
| Odaily (星球日报) | `odaily.py` | ✓ |

---

## 2026-04-08 技术债清理 (已完成)

### 提交记录

- `11507e3` - fix: unify config env expansion, connect republish, secure deploy
- `35667a1` - fix: remove PostgreSQL dependency from deploy scripts

### P0 修复 (已上线)

| 问题 | 文件 | 改动 |
|------|------|------|
| 配置环境变量展开不一致 | `loader.py:12` | 支持 `${VAR}` 和 `$VAR` 两种格式 |
| /api/refetch republish 参数无效 | `pipeline.py:46`, `pipeline_service.py:287` | 接通参数，refetch 时可发布 |
| CLI 缺少 chaincatcher | `cli.py:16` | 新增 `--source chaincatcher`、`--refetch-chaincatcher-url`、`--republish-refetched` |
| 部署脚本硬编码路径 | `deploy_remote.py:18` | 移除 `"F:\\sshkey\\whisky.pem"` |

### P1 修复 (已上线)

| 问题 | 文件 | 改动 |
|------|------|------|
| memory.py 遗留端点 | `memory.py:39-53` | 删除错误的 `/clear-cache` 端点 |
| 日志 XSS 风险 | `App.jsx:706-711` | 添加 `escapeHtml()` 先转义再高亮 |
| 部署脚本 PostgreSQL 依赖 | `deploy.sh`, `update.sh` | 移除 `database/` 操作，SQLite 自动初始化 |
| 敏感文档误提交风险 | `.gitignore:44-46` | 忽略 `API_DOC.md`、测试脚本 |

### ChainCatcher 增强

- `chaincatcher.py:96-130` - 解析逻辑改进，支持早报格式的嵌套 div 结构

### 2026-04-08 新增 Odaily 信源

**后端** (`792e71d`):
- 新建 `backend/pipelines/odaily.py` - Odaily 抓取器
- 支持 `/zh-CN/post` 列表页和 `/zh-CN/post/{id}` 详情页
- CLI 添加 `--source odaily` 和 `--refetch-odaily-url`
- API 支持 odaily refetch

**前端** (`71af50d`):
- 仪表板新增 "仅星球日报" 按钮
- 新建文章可选择 Odaily 来源
- 文章列表可筛选 Odaily 文章
- Odaily 文章显示 "OD" 缩写和蓝色徽章
- 中英文名称翻译

**Bug 修复**:
- `ad6df43` - 跳过 AI 总结区域，只抓取真正的文章正文
- `9972439` - API source pattern 添加 odaily 支持（文章列表筛选）
- `0b0110f` - 图片抓取修复：段落无文字时也提取图片
- `56e27e4` - 正文图片上传 COS 解决跨域显示问题

**已部署**: http://120.24.177.45:8081

---

## 2026-04-09 多模型 LLM 框架 + AI 编辑 + 重新推送 (已完成)

### 1. 多模型 LLM 框架

**后端**:
- 新建 `backend/services/llm_service.py` — ModelProvider + LLMService
  - 按任务路由到不同 LLM 模型（abstract/edit/...）
  - DB settings 键名：`llm_{task}_{field}`
  - 旧 `llm_api_url/key/model` 自动映射到 abstract 任务
  - 内置 10+ factory 预设（OpenAI, DeepSeek, 通义千问, 智谱, Moonshot 等）
- 改造 `backend/services/llm.py`
  - `generate_abstract()` 改用 LLMService
  - 新增 `edit_article()` + `ai_edit_text()` — AI 编辑正文
  - 移除直接 httpx 调用，统一走 OpenAI SDK
- 改造 `backend/routes/settings.py`
  - `GET /settings/llm-tasks` — 任务列表 + factory 列表
  - `POST /settings/test-llm/{task}` — 按任务测试连接
- `requirements.txt` 新增 `openai>=1.0.0`

**前端**:
- 设置页改为 Tab 式多模型配置（摘要生成 / 编辑正文）
- 每个 Tab：factory 下拉 + API URL + API Key + 模型名 + 测试按钮
- 选择 factory 自动填充默认 API 地址

### 2. 提示词管理

- 侧边栏新增 "提示词" 页面（PromptPage）
- 两个 textarea：摘要提示词 (`prompt_abstract`)、编辑提示词 (`prompt_edit`)
- 存储到 DB settings

### 3. AI 编辑正文

- 文章编辑器新增 AI 编辑面板（右侧侧边栏）
  - 输入补充指令（Prompt 2）
  - 一键编辑 → Prompt 1 + Prompt 2 拼接为 system prompt
  - 编辑结果预览 → 应用到正文
- 后端 `POST /articles/{id}/ai-edit` — 接收双 prompt，返回编辑结果

### 4. 重新推送

- 文章详情页：已发布文章显示 "重新推送" 按钮
- 文章编辑器：新增 "保存并推送" 按钮
- 后端 `POST /articles/{id}/republish` — 清除状态 → 重新发布

### 5. Odaily 管线修复

- **根因**: Odaily 改为纯 SPA (Next.js)，服务端不渲染内容
- **修复**: 使用 `web-api.odaily.news` API
  - 列表：`GET /post/page`
  - 详情：`GET /post/detail/{id}`
  - API 返回完整 HTML，标题顺序正确
- 保留 HTML 解析作为 fallback

### 6. AI 编辑提示词增强 + 持久化

- **问题**: AI 编辑输出 Markdown 格式（`###`、`**`）而非 HTML
- **修复**: 重写 `EDIT_SYSTEM_PROMPT`
  - 明确禁止 Markdown 语法
  - 强制输出纯 HTML 格式
  - 添加输入/输出示例
- **提示词持久化**: `generate_abstract()`、`edit_article()`、`ai_edit_text()` 现在从数据库读取自定义提示词
  - `prompt_abstract` — 摘要生成提示词
  - `prompt_edit` — 编辑正文提示词
- 用户在设置页面自定义的提示词现在会永久保存，代码更新后不会丢失

---

## 待完成工作

### 高优先级

| 任务 | 描述 | 复杂度 |
|------|------|--------|
| **发布队列** | 可视化管理待发布文章，支持拖拽排序、批量操作 | 中 |
| **健康告警** | 抓取/发布失败时推送钉钉/企微通知 | 低 |
| **单测框架** | 补充最小回归测试集 (pytest + fixtures) | 中 |

### 中优先级

| 任务 | 描述 | 复杂度 |
|------|------|--------|
| **AI 翻译** | 文章中英互译 | 中 |
| **内容去重** | 检测相似文章，避免重复发布 | 中 |
| **抓取统计** | 各源成功率、耗时趋势 | 低 |

### 数据源扩展

| 源 | 价值 | 难度 | 状态 |
|----|------|------|------|
| Panews | 高 | 中 | - |
| ~~Odaily~~ | ~~高~~ | ~~中~~ | ✓ 完成 |
| 币乎 | 中 | 低 | - |
| RSS 通用源 | 高 | 中 | - |

---

## 项目结构

```
article-publisher/
├── backend/
│   ├── api.py              # FastAPI 入口
│   ├── config/             # 配置加载
│   ├── pipelines/          # 数据源抓取器
│   ├── routes/             # API 路由
│   ├── services/           # 业务逻辑
│   └── utils/              # 工具类
├── frontend/src/           # React 前端
├── deploy/                 # 部署脚本
├── config.yaml.example     # 配置模板
└── data/                   # SQLite 数据库位置
```

---

## 开发注意事项

### 安全规则 (严格遵守)

1. **NEVER 硬编码敏感信息**
   - 密码、token、IP、SSH 密钥 → `config.yaml` 或环境变量
   - `os.environ.get("KEY", "")` 默认值必须为空字符串
   - `test/`、`refer/`、`database/`、`deploy/` 尤其注意

2. **Git 提交前检查**
   - 运行 `git diff` 搜索敏感词：`password`、`token`、`ip`、`key`、`secret`
   - 确认 `config.yaml` 在 `.gitignore` 中

3. **不要提交的文件**
   - `API_DOC.md`、测试脚本、临时文件
   - 已在 `.gitignore` 配置

### 代码风格

- Python: 遵循 PEP 8
- 前端: 函数组件 + Hooks
- 提交信息: `feat:` / `fix:` / `refactor:` 前缀

### 测试

```bash
# 语法检查
python -m compileall backend

# CLI 测试
python backend/cli.py --help

# 运行服务
cd backend && python api.py
```

---

## 快速上手

### 1. 环境准备

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd frontend
npm install
npm run build
```

### 2. 配置

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入密钥和配置
```

### 3. 运行

```bash
# 开发模式
cd backend && python api.py

# 生产部署
./deploy/deploy.sh  # 服务器上
```

---

## 联系方式

- 项目位置: `H:\article-publisher`
- 部署服务器: 阿里云 (见 `config.yaml`)
- 文档位置: `devlog/` (本目录)

---

*下次更新: 完成高优先级任务后*
