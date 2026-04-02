# 配置参数说明 / Configuration Reference

配置文件：`config.yaml`（位于项目根目录）

---

## chainthink — CMS 发布配置

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `api_url` | string | 是 | ChainThink 内容发布 API 地址 |
| `upload_url` | string | 是 | ChainThink 文件上传 API 地址 |
| `token` | string | 是 | JWT Token，支持环境变量替换 `${CHAINTHINK_TOKEN}` |
| `user_id` | string | 是 | ChainThink 用户 ID |
| `app_id` | string | 是 | ChainThink 应用 ID |

> Token 获取方式：登录 `https://admin.chainthink.cn` → F12 → Network → 任意请求 → 复制 `x-token`

---

## database — 数据库配置（可选）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `url` | string | — | PostgreSQL 连接字符串 |
| `echo` | bool | `false` | SQLAlchemy echo 模式 |

> 不配置则使用本地 JSON 文件存储。

---

## sources — 信源配置

### stcn — 券商中国

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `list_url` | string | — | 文章列表页 URL |
| `detail_url_template` | string | — | 详情页 URL 模板，`{id}` 占位 |
| `allowed_authors` | list | `[]` | 白名单作者，为空则不过滤 |
| `enabled` | bool | `true` | 是否启用 |

### techflow — 深潮

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `list_url` | string | — | 列表页 URL |
| `detail_url_template` | string | — | 详情页 URL 模板 |
| `enabled` | bool | `true` | 是否启用 |

### blockbeats — 律动

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `list_url` | string | — | 精选文章页 URL |
| `enabled` | bool | `true` | 是否启用 |

### chaincatcher — 链捕手

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 |

---

## paths — 文件路径

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `state_file` | string | `data/state.json` | 去重状态文件 |
| `stcn_output` | string | `output/stcn_articles` | STCN 文章存储目录 |
| `techflow_output` | string | `output/techflow_articles` | TechFlow 文章存储目录 |
| `blockbeats_output` | string | `output/blockbeats_articles` | BlockBeats 文章存储目录 |
| `chaincatcher_output` | string | `output/chaincatcher_articles` | ChainCatcher 文章存储目录 |
| `log_file` | string | `logs/pipeline.log` | 日志文件路径 |

---

## retry — 重试配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_retries` | int | `3` | 最大重试次数 |
| `backoff_factor` | float | `1` | 退避因子（秒） |
| `status_forcelist` | list | `[500,502,503,504]` | 触发重试的 HTTP 状态码 |

---

## 环境变量

通过 `$$VAR_NAME` 语法在 config.yaml 中引用环境变量：

```yaml
chainthink:
  token: "$${CHAINTHINK_TOKEN}"
```

> 注意：双美元号 `$$` 用于转义，避免 YAML 解析问题。
