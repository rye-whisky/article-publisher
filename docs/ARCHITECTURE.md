# X-LLM 项目架构说明文档

## 一、项目概述

X-LLM 是一个企业级智能体（Agent）服务平台，提供了完整的 LLM 应用开发框架。项目采用分层架构设计，支持多模型接入、多模态处理、知识图谱检索、向量检索等核心功能。

### 核心特性
- **多模型统一接入**：支持 Qwen、GPT、Claude 等多种 LLM
- **多模态处理**：支持图片、文档、文本的综合处理
- **智能检索增强**：集成知识图谱、向量数据库、知识网络检索
- **工作流编排**：基于 LangGraph 的复杂任务编排
- **流式响应**：完整的 SSE 流式输出支持
- **持久化记忆**：基于 MongoDB 的对话历史管理

---

## 二、整体架构设计

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                        应用入口层                             │
│                     (apps/)                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  agent_app   │  │    x_app     │  │  app_base    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        服务层                                │
│                    (xllm/service/)                           │
│  ┌──────────────────────┐  ┌──────────────────────┐        │
│  │   agent_service/     │  │     x_service/       │        │
│  │  - routes/           │  │  - routes/           │        │
│  │  - app.py            │  │  - app.py            │        │
│  └──────────────────────┘  └──────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        业务核心层                            │
│                     (xllm/core/)                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ agent_builder│  │  mod_agent   │  │ vector_rag   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   graph/     │  │    plan/     │  │    deer/     │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              基础设施层 / 数据访问层                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │    proxy/    │  │   network/   │  │    confs/    │      │
│  │  (多模型代理) │  │  (数据模型)   │  │  (配置管理)   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构说明

```
x-llm-1/
├── apps/                        # 应用入口层
│   ├── agent_app.py            # Agent 服务应用入口
│   ├── x_app.py                # X 服务应用入口
│   └── app_base.py             # 基础应用框架（动态加载）
│
├── confs/                       # 配置管理层
│   ├── conf_loader.py          # XConf 配置加载器（核心）
│   ├── model_loader.py         # 模型配置加载
│   ├── apps/                   # 应用配置
│   │   └── apps.yaml           # 应用端口、模块配置
│   └── providers/              # 提供商配置
│       ├── chat_provider.yaml      # LLM 配置
│       ├── embedding_provider.yaml # Embedding 配置
│       └── mcp_servers.yaml        # MCP 服务器配置
│
├── xllm/                       # 核心业务层
│   ├── core/                  # 核心模块
│   │   ├── agent_builder.py       # Agent 构建器
│   │   ├── mod_agent.py           # Agent 模型定义（核心数据模型）
│   │   ├── vector_rag.py          # 向量 RAG
│   │   ├── mod_graph_v2.py        # 图谱模型定义
│   │   ├── knowledge_network/     # 知识网络
│   │   ├── graph/                 # 图谱相关
│   │   └── plan/                  # 规划模块
│   │
│   ├── deer/                  # Deer 工作流框架
│   │   ├── workflow.py           # 工作流定义
│   │   ├── chat/                 # 聊天工作流
│   │   │   ├── builder.py        # 图构建器
│   │   │   ├── nodes.py          # 节点定义
│   │   │   └── state.py          # 状态定义
│   │   └── agents/               # 特定 Agent
│   │
│   ├── service/               # 服务层
│   │   ├── agent_service/        # Agent 服务
│   │   │   ├── app.py            # FastAPI 应用
│   │   │   └── routes/           # API 路由
│   │   │       ├── agent/        # Agent 相关 API
│   │   │       ├── vector/       # 向量检索 API
│   │   │       ├── graph/        # 图谱 API
│   │   │       └── optimize/     # 优化 API
│   │   └── x_service/            # X 服务
│   │
│   ├── proxy/                 # 代理层（多模型统一接入）
│   │   └── proxy.py              # 统一代理实现
│   │
│   └── network/               # 网络层
│       ├── models/               # API 数据模型
│       └── error_code.py         # 错误码定义
│
├── tools/                      # 工具模块
├── workers/                    # 后台工作
└── common/                     # 通用模块
```

---

## 三、核心组件详解

### 3.1 配置管理系统（XConf）

**位置**: `confs/conf_loader.py`

XConf 是一个强大的 YAML 配置加载系统，支持动态标签和缓存机制。

#### 核心功能

```python
# 基本用法
config = XConf("config.yaml")  # 加载配置
config = XConf("chat_provider.yaml", "providers", reload=True)  # 强制重载
```

#### 自定义标签

| 标签 | 功能 | 示例 |
|------|------|------|
| `!env` | 环境变量注入 | `!env API_KEY:=default_value` |
| `!file` | 文件内容引用 | `!file path/to/file.txt` |
| `!time` | 时间格式化 | `!time %Y%m%d` |
| `!include` | 配置文件包含 | `!include other.yaml::key$string` |

#### 配置示例

```yaml
# apps/apps.yaml
agent_app:
  port: 8000
  module: xllm.service.agent_service.app
  graph_retriever:
    webhook: !env GRAPH_RETRIEVER_WEBHOOK_URL:=http://localhost:8080

# providers/chat_provider.yaml
Provider:
  qwen:
    configs:
      base_url: !env LLM_QWEN_CHAT_SERVER:=https://dashscope.aliyuncs.com
      api_key: !env LLM_QWEN_CHAT_API_KEY:=your-key
```

#### 缓存机制

```python
_XConf_Cache = {}  # 配置缓存字典

# 缓存逻辑：如果配置已加载且不强制重载，则返回缓存
if target_path in _XConf_Cache and not reload:
    return _XConf_Cache[target_path]
```

**可复用经验**:
1. 使用自定义标签实现配置的灵活性
2. 缓存机制减少重复 I/O
3. 环境变量默认值机制提高部署便利性

---

### 3.2 Agent 核心模型系统

**位置**: `xllm/core/mod_agent.py`

这是整个项目的核心数据模型定义，定义了 Agent 构建所需的所有参数和事件类型。

#### 核心数据模型

```python
class XAgentBuilderParams(BaseModel):
    """智能体构建参数"""
    agent_card: XAgentCard          # Agent 身份卡片
    llm_config: LLMConfig            # LLM 配置
    feature_config: FeatureConfig    # 功能配置（检索、搜索等）
    memory_config: MemoryConfig      # 记忆配置
    system_prompt: str               # 系统提示词
    conversation_id: str             # 对话 ID
    initial_args: dict               # 初始参数
```

#### 功能配置（FeatureConfig）

```python
class FeatureConfig(BaseModel):
    search_enable: bool                    # 互联网搜索开关
    graph_search: list[GraphParam]          # 图谱检索配置
    vector_search: list[VectorParam]        # 向量检索配置
    knowledge_network_search: list[...]     # 知识网络检索
    image_recognize: bool                   # 图片识别
    include_recommend_questions: bool       # 推荐问题
    need_speak: bool                        # 语音合成
    mcp_servers: dict                       # MCP 服务器列表
    add_reference_tag: bool                 # 引用标签
```

#### 事件系统

```python
class XEventType(Enum):
    XEVENT_TYPE_DATA = "data"           # 流数据
    XEVENT_TYPE_THINK = "think"         # 思考过程
    XEVENT_TYPE_TOOL = "tool"           # 工具调用
    XEVENT_TYPE_TOOL_PREPARE = "tool_prepare"  # 工具准备
    XEVENT_TYPE_ERROR = "error"         # 错误事件

class XEventItem(BaseModel):
    status: XEventItemStatus            # 事件状态
    event_type: XEventType              # 事件类型
    event_name: str                     # 事件名称
    event_detail: Union[...]            # 事件详情
```

#### 缓冲区处理（XBuffer）

用于处理流式数据的状态机：

```python
class XBuffer:
    """处理思考、内容、工具准备的缓冲"""
    def buffer(self, content, think, tool_prepare) -> list[XBufferStep]:
        # 状态流转：UNKNOWN -> THINK -> CONTENT -> TOOL_PREPARE -> END
        # 返回状态变化列表
```

**可复用经验**:
1. 使用 Pydantic 进行数据验证和序列化
2. 清晰的事件类型定义便于前端处理
3. 缓冲区状态机处理复杂的流式数据转换

---

### 3.3 应用启动框架（AppBase）

**位置**: `apps/app_base.py`

#### 核心机制

```python
class AppBase:
    def __init__(self, app_name: str):
        # 1. 从配置文件加载服务配置
        self.service_config = XConf("apps.yaml", "apps").get(app_name)

        # 2. 动态导入模块
        module = importlib.import_module(self.service_config.get('module'))

        # 3. 获取 FastAPI 应用实例
        self.app = getattr(module, "app")
```

#### 配置驱动的模块加载

```yaml
# apps/apps.yaml
apps:
  agent_app:
    port: 8000
    module: xllm.service.agent_service.app
  x_app:
    port: 8001
    module: xllm.service.x_service.app
```

**可复用经验**:
1. 配置驱动的模块加载实现灵活的服务部署
2. 动态导入支持模块的独立开发和测试
3. 统一的启动框架简化新服务的添加

---

### 3.4 服务层架构

**位置**: `xllm/service/`

#### API 路由组织

```
agent_service/routes/
├── agent/
│   ├── agent_chat_api.py         # 聊天 API（核心）
│   ├── agent_history_api.py      # 历史记录
│   └── session_summary_api.py    # 会话总结
├── vector/
│   ├── rag_get_api.py           # RAG 检索
│   └── rag_upload_api.py        # 文档上传
├── graph/
│   ├── get_industry_nodes_api.py # 行业图谱
│   └── get_knowledge_graph_api.py # 知识图谱
└── optimize/
    └── optimize_description_api.py # 描述优化
```

#### 统一的 API 处理模式

```python
@router.post("/x-llm/chat")
async def agent_chat(request: Request, params: AgentChatModel):
    # 1. 参数校验
    if not params.builder_params.agent_card.agent_name:
        return EventSourceResponse(content=error_stream(...))

    # 2. 多媒体处理
    media_content = await process_media_content(params.media_contents)

    # 3. 构建 Agent
    x_agent = XAgent(params.builder_params, params.query)

    # 4. 流式响应
    out = x_agent.astream_4sse(...)
    async for o in out:
        yield ServerSentEvent(**o)
```

**可复用经验**:
1. 按功能模块组织路由，便于维护
2. 统一的错误处理和响应格式
3. SSE 流式响应支持实时数据推送

---

### 3.5 代理层（Proxy）

**位置**: `xllm/proxy/proxy.py`

统一的 LLM 代理层，实现多模型统一接入。

#### 核心功能

```python
class Proxy:
    async def astream(self, data: dict, query_params: dict):
        # 1. 统一的流式处理逻辑
        # 2. 统一的错误处理
        # 3. 统一的 token 统计
```

**可复用经验**:
1. 代理模式隔离底层模型差异
2. 统一接口便于切换和扩展模型
3. 集中的 token 统计便于成本控制

---

## 四、工作流架构（Deer 框架）

### 4.1 基于 LangGraph 的状态机

**位置**: `xllm/deer/chat/builder.py`

```python
def _build_base_graph():
    builder = StateGraph(State)
    builder.add_edge(START, "coordinator")
    builder.add_node("coordinator", coordinator_node)
    builder.add_node("background_investigator", background_investigation_node)
    builder.add_node("planner", planner_node)
    builder.add_node("researcher", researcher_node)
    builder.add_node("coder", coder_node)
    return builder.compile()
```

### 4.2 状态管理

```python
class State(TypedDict):
    messages: list[BaseMessage]
    current_step: str
    research_result: dict
    code_result: dict
    # ... 其他状态字段
```

### 4.3 节点类型

| 节点 | 功能 |
|------|------|
| coordinator | 任务协调和分发 |
| background_investigator | 背景调研 |
| planner | 任务规划 |
| researcher | 研究执行 |
| coder | 代码生成 |

**可复用经验**:
1. 使用状态图管理复杂工作流
2. 节点化的设计便于复用和测试
3. 持久化记忆支持长时间任务

---

## 五、数据流设计

### 5.1 请求处理流程

```
用户请求
    │
    ▼
API 路由层 (agent_chat_api.py)
    │
    ▼
参数校验 + 多媒体处理
    │
    ▼
XAgent 构建 (agent_builder.py)
    │
    ▼
┌─────────────────────────────────┐
│  检索增强生成 (RAG)              │
│  ├─ 知识图谱检索                 │
│  ├─ 向量检索                     │
│  ├─ 知识网络检索                 │
│  └─ 互联网搜索                   │
└─────────────────────────────────┘
    │
    ▼
Proxy 代理层 (统一 LLM 调用)
    │
    ▼
XBuffer 缓冲处理
    │
    ▼
SSE 流式响应
```

### 5.2 事件流

```
XEVENT_TYPE_THINK (思考开始)
    │
    ├─ XEVENT_ITEM_STATUS_BEGIN
    ├─ XEVENT_ITEM_STATUS_BUFFERING
    └─ XEVENT_ITEM_STATUS_END
    │
    ▼
XEVENT_TYPE_TOOL_PREPARE (工具准备)
    │
    ▼
XEVENT_TYPE_DATA (内容生成)
    │
    ▼
XEVENT_TYPE_FULL_DATA (完整数据)
```

---

## 六、技术栈

### 6.1 核心框架

| 类别 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI | API 服务 |
| ASGI 服务器 | uvicorn | 异步服务 |
| 流式传输 | sse-starlette | Server-Sent Events |
| AI 框架 | LangChain | LLM 应用开发 |
| 工作流 | LangGraph | 状态机编排 |
| 数据验证 | Pydantic | 数据模型 |
| 配置 | PyYAML | YAML 处理 |

### 6.2 数据存储

| 存储 | 用途 |
|------|------|
| MongoDB | 消息存储、对话历史 |
| Neo4j | 知识图谱 |
| ChromaDB | 向量数据库 |
| Redis | 缓存和会话 |

### 6.3 HTTP 客户端

- **httpx**: 现代化的异步 HTTP 客户端

---

## 七、设计模式应用

### 7.1 分层架构模式
- 表现层（API）→ 服务层 → 业务层 → 数据层
- 职责分离，便于维护和扩展

### 7.2 代理模式
- Proxy 层统一多模型接入
- 隔离底层差异，提供统一接口

### 7.3 工厂模式
- 图谱检索器工厂
- 支持多种检索类型的统一创建

### 7.4 建造者模式
- XAgentBuilderParams
- 分步骤构建复杂的 Agent 对象

### 7.5 观察者模式
- 事件流系统
- SSE 推送机制

---

## 八、可复用的架构经验

### 8.1 配置管理

1. **环境变量默认值机制**
   ```yaml
   api_key: !env API_KEY:=default_value
   ```
   - 提高部署灵活性
   - 支持多环境配置

2. **配置缓存**
   - 减少文件 I/O
   - 支持热重载

3. **配置包含**
   ```yaml
   common_config: !include common.yaml::settings$string
   ```
   - 避免配置重复
   - 统一管理共享配置

### 8.2 Agent 设计

1. **参数化构建**
   - 所有配置通过参数传入
   - 支持动态功能开关

2. **功能模块化**
   - 检索功能独立配置
   - 便于功能组合

3. **事件驱动**
   - 清晰的事件类型定义
   - 支持复杂的状态流转

### 8.3 API 设计

1. **统一响应格式**
   ```python
   class XEventItem(BaseModel):
       status: XEventItemStatus
       event_type: XEventType
       event_detail: Union[...]
   ```

2. **流式响应**
   - 使用 SSE 实现实时推送
   - 支持 buffer 状态管理

3. **错误处理**
   - 统一的错误码定义
   - 优雅的降级处理

### 8.4 工作流编排

1. **状态图设计**
   - 清晰的状态定义
   - 明确的状态转换

2. **节点复用**
   - 独立的节点实现
   - 支持并行执行

### 8.5 性能优化

1. **流式处理**
   - 避免大对象序列化
   - 减少内存占用

2. **异步执行**
   - 全面使用 async/await
   - 提高并发处理能力

3. **缓存机制**
   - 配置缓存
   - 模型缓存
   - 数据缓存

---

## 九、部署架构建议

### 9.1 服务拆分

```
┌─────────────────────────────────────────────┐
│              API Gateway / Nginx             │
└─────────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   ┌────────┐  ┌────────┐  ┌────────┐
   │ Agent  │  │  X     │  │Worker │
   │ Service│  │ Service│  │ Service│
   └────────┘  └────────┘  └────────┘
        │           │           │
        └───────────┼───────────┘
                    ▼
        ┌───────────────────────┐
        │   Data Layer          │
        │ ├─ MongoDB            │
        │ ├─ Neo4j              │
        │ ├─ ChromaDB           │
        │ └─ Redis              │
        └───────────────────────┘
```

### 9.2 配置管理

- 使用环境变量管理敏感配置
- 配置文件按环境分离（dev/test/prod）
- 密钥管理使用专门的密钥服务

### 9.3 监控与日志

- 统一的日志格式
- Token 使用统计
- API 调用链追踪
- 性能指标监控

---

## 十、快速开始指南

### 10.1 项目初始化

```bash
# 1. 克隆项目
git clone <repository_url>
cd x-llm-1

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入必要的配置

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动服务
python apps/app_base.py -a agent_app
```

### 10.2 添加新的 Agent

1. 在 `xllm/core/` 下定义 Agent 逻辑
2. 在 `xllm/service/agent_service/routes/` 下添加 API
3. 在 `confs/` 下添加配置
4. 更新 `apps/apps.yaml`

### 10.3 添加新的检索方式

1. 在 `xllm/core/` 下定义检索参数模型
2. 在 `FeatureConfig` 中添加配置字段
3. 在 `XAgent` 中集成检索逻辑
4. 在 API 路由中暴露配置接口

---

## 十一、核心代码索引

| 功能 | 文件路径 |
|------|---------|
| 配置加载 | `confs/conf_loader.py` |
| Agent 模型 | `xllm/core/mod_agent.py` |
| Agent 构建 | `xllm/core/agent_builder.py` |
| 应用框架 | `apps/app_base.py` |
| 聊天 API | `xllm/service/agent_service/routes/agent/agent_chat_api.py` |
| 图谱检索 | `xllm/core/graph/tools/` |
| 向量检索 | `xllm/core/vector_rag.py` |
| 代理层 | `xllm/proxy/proxy.py` |
| 工作流 | `xllm/deer/chat/builder.py` |

---

## 十二、总结

X-LLM 项目架构的核心优势：

1. **模块化设计**：清晰的分层和模块划分，职责明确
2. **可扩展性**：支持多种模型、检索方式、存储后端的无缝切换
3. **高性能**：流式处理、异步执行、缓存机制
4. **可维护性**：统一的错误处理、配置管理、代码规范
5. **多模态支持**：图片、文档、文本的综合处理能力
6. **工作流编排**：基于 LangGraph 的复杂任务处理能力

这套架构适合作为企业级 AI 应用开发的基础框架，可以根据具体业务需求进行裁剪和扩展。
