# ChainThink 文章发布管线 — 服务端部署 PRD

> 目标：将现有本地管线代码化、服务化，部署到 Linux 服务器上，通过 cron 定时运行，无需 OpenClaw。

---

## 一、现有资产清单

### 1.1 核心脚本
- **路径**：`C:\Users\whisky\.openclaw\workspace-creative\skills\stcn-chainthink-pipeline\scripts\stcn_chainthink_pipeline.py`
- **功能**：抓取 → 清洗 → 去重 → 发布，单文件 ~800 行
- **语言**：Python 3
- **依赖**：`requests`, `beautifulsoup4`，标准库 `hashlib/hmac/json/re/sub/os/tempfile/subprocess`
- **特殊依赖**：需要 Node.js 来计算 CRC64 哈希（从 `https://admin.chainthink.cn/crc64.js` 下载算法脚本）

### 1.2 状态文件
- **路径**：`data/stcn_chainthink_state.json`
- **内容**：`{"published_ids": ["stcn:3708814", "techflow:30883", ...], "updated_at": "..."}`

### 1.3 落地文件
- `output/stcn_articles/*.md` — 券商中国文章（Markdown）
- `output/techflow_articles/techflow_*.json` — 深潮文章（JSON，含 blocks）

### 1.4 完整技术文档
- **路径**：`H:\article-publisher\refer\pipeline-full-guide.md`

---

## 二、部署目标架构

```
Linux 服务器
├── /opt/chainthink-publisher/
│   ├── pipeline.py              # 主脚本（从现有脚本改造）
│   ├── config.yaml              # 配置文件（API token、作者过滤等外提）
│   ├── requirements.txt         # Python 依赖
│   ├── crc64.js                 # CRC64 算法（本地缓存，不再远程下载）
│   ├── data/
│   │   └── state.json           # 去重状态
│   ├── output/
│   │   ├── stcn_articles/       # 券商中国文章存档
│   │   └── techflow_articles/   # 深潮文章存档
│   └── logs/                    # 运行日志
│       └── pipeline.log
├── /etc/cron.d/chainthink       # 系统 cron 配置
└── Node.js >= 18                # CRC64 计算依赖
```

---

## 三、需要改造的点

### 3.1 配置外提（高优先级）

现有脚本中硬编码了以下内容，必须外提到配置文件或环境变量：

```python
# 这些需要从 config.yaml 或 .env 读取
API_TOKEN = "REDACTED_JWTs..."   # ChainThink JWT token
X_USER_ID = "83"                          # ChainThink 用户 ID
X_APP_ID = "101"                          # ChainThink 应用 ID
ALLOWED_AUTHORS = {"沐阳", "周乐"}          # 券商中国作者白名单
```

**建议配置文件格式** (`config.yaml`)：
```yaml
chainthink:
  api_url: "https://api-v2.chainthink.cn/ccs/v1/admin/content/publish"
  upload_url: "https://api-v2.chainthink.cn/ccs/v1/admin/upload_file"
  token: "${CHAINTHINK_TOKEN}"     # 从环境变量读取
  user_id: "83"
  app_id: "101"

sources:
  stcn:
    list_url: "https://www.stcn.com/article/wx/qszg.html"
    detail_url_template: "https://www.stcn.com/article/detail/{id}.html"
    allowed_authors: ["沐阳", "周乐"]
    enabled: true
  techflow:
    list_url: "https://www.techflowpost.com/zh-CN/article"
    detail_url_template: "https://www.techflowpost.com/zh-CN/article/{id}"
    enabled: true

paths:
  state_file: "data/state.json"
  stcn_output: "output/stcn_articles"
  techflow_output: "output/techflow_articles"
  log_file: "logs/pipeline.log"

crc64:
  js_path: "crc64.js"             # 本地缓存的 CRC64 算法文件
```

### 3.2 CRC64 哈希计算（中优先级）

**现状**：从 `https://admin.chainthink.cn/crc64.js` 下载 JS 脚本，用 Node.js 执行。

**改造方案**（二选一）：
1. **Python 原生实现**：用 Python 重写 CRC64 算法，彻底去掉 Node.js 依赖（推荐）
2. **保留 JS**：把 `crc64.js` 打包进项目，不再远程下载

推荐方案 1。需要从 `crc64.js` 中提取 CRC64 算法逻辑，翻译成纯 Python。CRC64-ECMA 或 CRC64-WE 都有现成的 Python 实现。

**验证方法**：对比同一文件的 JS CRC64 输出和 Python CRC64 输出，确保一致。

### 3.3 日志系统（中优先级）

**现状**：脚本只用 `print(json.dumps(...))` 输出 JSON 结果。

**改造**：
- 用 Python `logging` 模块，输出到文件 + stdout
- 日志格式：`{timestamp} [{level}] {message}`
- 记录每次运行的：抓取数、发布数、跳过数、失败数、耗时

### 3.4 错误处理增强（中优先级）

**现状**：
- HTTP 请求只有 `r.raise_for_status()`，没有重试
- SSL 错误（`requests.exceptions.SSLError`）会直接退出

**改造**：
- 给 `requests.Session` 加 `urllib3.util.retry.Retry`（重试 3 次，指数退避）
- 对 SSL 错误做 catch + 重试
- 单篇文章失败不影响其他文章（现有逻辑已做到）

### 3.5 深潮硬编码截止文本（低优先级）

```python
stop_text = '找到这些创始人——那些不符合本地 VC 体系优化出来的「标准简历模板」的人——是我们现在在做的事。'
```

这行是临时硬编码，需要移除或改为配置项。

### 3.6 COS 上传鉴权（低优先级，保持现有逻辑即可）

`put_file_to_cos()` 中的 HMAC-SHA1 签名逻辑已经稳定，无需改造。但要确保在 Linux 上也能正常运行（纯 Python 实现，应该没问题）。

---

## 四、部署步骤（给 Claude Code 的执行清单）

### Step 1：项目初始化
```bash
mkdir -p /opt/chainthink-publisher/{data,output/stcn_articles,output/techflow_articles,logs}
```

### Step 2：复制并改造脚本
1. 将 `stcn_chainthink_pipeline.py` 复制到 `/opt/chainthink-publisher/pipeline.py`
2. 创建 `config.yaml`（模板如上）
3. 创建 `requirements.txt`：
   ```
   requests>=2.28
   beautifulsoup4>=4.12
   pyyaml>=6.0
   ```
4. 创建 `.env` 模板：
   ```
   CHAINTHINK_TOKEN=your_jwt_token_here
   ```

### Step 3：CRC64 Python 化
1. 从 `https://admin.chainthink.cn/crc64.js` 获取算法逻辑
2. 用纯 Python 实现 CRC64
3. 写一个验证脚本，对比 JS 和 Python 输出一致性

### Step 4：改造脚本
1. 将硬编码配置改为从 `config.yaml` + 环境变量读取
2. 加 `logging` 模块
3. 给 HTTP 请求加重试（`requests.adapters.HTTPAdapter` + `Retry`）
4. 移除 `stop_text` 硬编码
5. 路径改为相对路径（相对于脚本所在目录）

### Step 5：测试
```bash
# 先测试抓取（不发）
python pipeline.py --source stcn --skip-fetch --dry-run

# 再测试完整流程
python pipeline.py --source stcn --incremental
python pipeline.py --source techflow --incremental
```

### Step 6：配置系统 cron
```cron
# /etc/cron.d/chainthink
# 每10分钟运行一次券商中国
*/10 * * * * cd /opt/chainthink-publisher && /usr/bin/python3 pipeline.py --source stcn --incremental >> logs/cron-stcn.log 2>&1

# 每10分钟运行一次深潮（偏移5分钟，避免同时跑）
5-59/10 * * * * cd /opt/chainthink-publisher && /usr/bin/python3 pipeline.py --source techflow --incremental >> logs/cron-techflow.log 2>&1
```

### Step 7：验证
1. 检查 `data/state.json` 是否正确更新
2. 检查 `output/` 目录是否有新文章
3. 检查 ChainThink 后台是否有新内容
4. 检查 `logs/` 日志是否正常

---

## 五、安全注意事项

1. **JWT Token**：必须通过环境变量注入，不能硬编码在代码或配置文件中
2. **.env 文件**：权限设为 `600`，仅 owner 可读
3. **config.yaml**：不含敏感信息，可以 `644`
4. **日志脱敏**：日志中不输出完整 token 或 API response body

---

## 六、现有脚本中需要移除/改造的硬编码清单

| 行号范围 | 硬编码内容 | 改造方式 |
|----------|-----------|----------|
| API_URL | `https://api-v2.chainthink.cn/ccs/v1/admin/content/publish` | config.yaml |
| API_TOKEN | JWT token 字符串 | 环境变量 CHAINTHINK_TOKEN |
| X_USER_ID | `"83"` | config.yaml |
| X_APP_ID | `"101"` | config.yaml |
| ALLOWED_AUTHORS | `{"沐阳", "周乐"}` | config.yaml |
| STCN_LIST_URL | `https://www.stcn.com/article/wx/qszg.html` | config.yaml |
| TECHFLOW_LIST_URL | `https://www.techflowpost.com/zh-CN/article` | config.yaml |
| ROOT | `Path(__file__).resolve().parents[3]` | 改为脚本所在目录 |
| STATE_FILE | `ROOT / "data" / ...` | config.yaml |
| stop_text | 深潮硬编码截止文本 | 移除或改为配置 |
| crc64.js 远程下载 | `session.get('https://admin.chainthink.cn/crc64.js')` | 本地打包或 Python 原生 |

---

## 七、ChainThink API 接口参考

### 7.1 发布接口
- **URL**: `POST https://api-v2.chainthink.cn/ccs/v1/admin/content/publish`
- **Headers**: `x-token`, `x-user-id`, `X-App-Id: 101`, `Content-Type: application/json`
- **Body**: 见 `pipeline-full-guide.md` 第八节

### 7.2 封面上传凭证
- **URL**: `POST https://api-v2.chainthink.cn/ccs/v1/admin/upload_file`
- **Body**: `{"file_name": "cover.jpg", "hash": "{CRC64}", "use_pre_sign_url": true/false, "confirm": false/true}`

### 7.3 COS 上传
- **URL**: `https://{bucket}.cos.{region}.myqcloud.com/{object_key}`
- **Auth**: HMAC-SHA1 签名（见 `pipeline-full-guide.md` 第 7.4 节）

---

## 八、CRC64 算法（从 crc64.js 提取的关键逻辑）

CRC64 算法需要与 ChainThink 后端一致。需要：
1. 先从浏览器或 curl 获取 `https://admin.chainthink.cn/crc64.js` 的完整内容
2. 分析其使用的 CRC64 变体（ECMA / WE / ISO）
3. 用 Python 实现等价算法
4. 验证：对同一文件，JS 和 Python 的输出必须完全一致

如果不想翻译 JS，也可以保留 Node.js 依赖，把 `crc64.js` 文件直接放在项目目录中。

---

## 九、文件交付清单

Claude Code 完成后应产出：

```
/opt/chainthink-publisher/
├── pipeline.py              # 改造后的主脚本
├── config.yaml.example      # 配置模板
├── .env.example             # 环境变量模板
├── requirements.txt         # Python 依赖
├── crc64.py                 # CRC64 Python 实现（如果选择 Python 化）
├── crc64.js                 # CRC64 JS 文件（如果保留 Node.js 依赖）
├── data/.gitkeep
├── output/stcn_articles/.gitkeep
├── output/techflow_articles/.gitkeep
├── logs/.gitkeep
└── README.md                # 使用说明
```
