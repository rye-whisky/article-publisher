# LLM 应用架构最佳实践指南

> 基于 X-LLM 项目实战经验提炼的通用架构指导文档

---

## 一、核心理念

### 1.1 设计原则

| 原则 | 说明 | 价值 |
|------|------|------|
| **分层解耦** | 表现层-服务层-业务层-数据层 | 降低维护成本，提高可测试性 |
| **参数化构建** | 核心对象通过参数配置构建 | 提高灵活性，支持动态调整 |
| **事件驱动** | 统一的事件模型处理流式数据 | 解耦前后端，支持实时推送 |
| **配置驱动** | 关键行为由配置文件决定 | 零代码变更即可调整功能 |
| **异步优先** | 全面使用 async/await | 提高并发处理能力 |

### 1.2 架构愿景

```
┌──────────────────────────────────────────────────────┐
│                   目标：可扩展的 LLM 应用平台          │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ 多模型   │  │ 多模态   │  │ 多检索   │          │
│  │ 统一接入 │  │ 统一处理 │  │ 无缝切换 │          │
│  └──────────┘  └──────────┘  └──────────┘          │
│                                                       │
│  ┌──────────────────────────────────────────────┐  │
│  │           流式响应 + 状态管理 + 持久化        │  │
│  └──────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## 二、通用配置系统设计

### 2.1 核心模式：可扩展的 YAML 配置加载器

#### 为什么需要？

1. **环境差异**：开发/测试/生产环境配置不同
2. **敏感信息**：密钥不应硬编码
3. **配置复用**：避免重复定义相同配置
4. **动态调整**：不重启服务即可变更配置

#### 实现模板

```python
# config_loader.py
import os
import yaml
from typing import Any

# 配置缓存
_CONFIG_CACHE = {}

def load_config(config_path: str, *sub_dirs, reload: bool = False) -> dict:
    """
    加载 YAML 配置文件

    Args:
        config_path: 配置文件名
        *sub_dirs: 配置文件所在子目录
        reload: 是否强制重新加载
    """
    # 构建完整路径
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, *sub_dirs, config_path)

    # 缓存检查
    if not reload and full_path in _CONFIG_CACHE:
        return _CONFIG_CACHE[full_path]

    # 加载配置
    with open(full_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        _CONFIG_CACHE[full_path] = config
        return config


# 自定义标签处理器
class ConfigLoader(yaml.SafeLoader):
    """扩展的 YAML 加载器，支持自定义标签"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_constructor('!env', self._env_constructor)
        self.add_constructor('!file', self._file_constructor)
        self.add_constructor('!include', self._include_constructor)

    @staticmethod
    def _env_constructor(loader, node):
        """
        环境变量标签: !env VAR_NAME:=default_value
        """
        value = loader.construct_scalar(node)
        if ':=' in value:
            var_name, default = value.split(':=', 1)
            return os.getenv(var_name.strip(), default.strip())
        return os.getenv(value.strip(), '')

    @staticmethod
    def _file_constructor(loader, node):
        """
        文件内容标签: !file relative/path/to/file.txt
        """
        file_path = loader.construct_scalar(node)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(base_dir, file_path)

        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def _include_constructor(loader, node):
        """
        配置包含标签: !include other.yaml::key
        """
        value = loader.construct_scalar(node)
        if '::' in value:
            file_path, key = value.split('::', 1)
            config = load_config(file_path.strip())
            return config.get(key.strip(), {})
        return load_config(value.strip())


# 使用扩展的加载器
yaml.SafeLoader = ConfigLoader
```

#### 配置文件示例

```yaml
# config/services.yaml
services:
  llm_service:
    base_url: !env LLM_BASE_URL:=https://api.openai.com
    api_key: !env LLM_API_KEY:=
    model: !env LLM_MODEL:=gpt-4
    timeout: 60

  database:
    mongodb:
      url: !env MONGO_URL:=mongodb://localhost:27017
      db_name: !env MONGO_DB_NAME:=myapp

  features:
    # 引用其他配置文件
    search_config: !include config/search.yaml::enabled

# config/search.yaml
enabled:
  web_search: true
  vector_search: true
  graph_search: false
```

#### 使用示例

```python
from config_loader import load_config

# 加载配置
config = load_config('services.yaml', 'config')
llm_config = config['services']['llm_service']

# 强制重新加载
config = load_config('services.yaml', 'config', reload=True)
```

### 2.2 配置管理最佳实践

| 实践 | 说明 | 示例 |
|------|------|------|
| 环境变量默认值 | 使用 `:=` 提供默认值 | `!env PORT:=8000` |
| 敏感信息外部化 | 密钥通过环境变量注入 | `!env API_KEY` |
| 配置分层 | 按环境/模块分离配置文件 | `config/dev/`, `config/prod/` |
| 配置验证 | 使用 Pydantic 验证配置格式 | 见下节 |
| 配置缓存 | 避免重复读取 | 使用缓存字典 |

---

## 三、数据模型设计

### 3.1 核心模式：参数化的构建器

#### 设计目标

1. **类型安全**：编译期类型检查
2. **文档自描述**：类型即文档
3. **验证自动化**：自动校验数据格式
4. **序列化友好**：轻松转为 JSON/YAML

#### 实现模板

```python
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Union
from enum import Enum


# ============ 基础配置模型 ============

class LLMConfig(BaseModel):
    """LLM 服务配置"""
    name: str = Field(description="模型名称")
    base_url: str = Field(default="", description="API 地址")
    api_key: str = Field(default="", description="API 密钥")
    temperature: float = Field(default=0.7, ge=0, le=2, description="温度参数")
    max_tokens: int = Field(default=2000, gt=0, description="最大输出 tokens")


class MemoryConfig(BaseModel):
    """记忆配置"""
    memory_type: Literal["mongodb", "redis", "memory"] = "mongodb"
    max_history: int = Field(default=10, gt=0, description="最大历史条数")
    collection_name: str = Field(default="chat_history", description="集合名称")


# ============ 功能配置模型 ============

class SearchConfig(BaseModel):
    """搜索配置"""
    enabled: bool = False
    max_results: int = Field(default=5, ge=1, le=20)
    timeout: int = Field(default=10, ge=1, description="超时时间(秒)")


class RetrievalConfig(BaseModel):
    """检索配置"""
    enabled: bool = False
    top_k: int = Field(default=3, ge=1, le=10)
    score_threshold: float = Field(default=0.7, ge=0, le=1)


class FeatureConfig(BaseModel):
    """功能配置总览"""
    search: SearchConfig = Field(default_factory=SearchConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    image_recognition: bool = False
    voice_output: bool = False

    class Config:
        # 支持别名
        allow_population_by_field_name = True
        # 支持额外字段
        extra = "ignore"


# ============ Agent 构建参数 ============

class AgentCard(BaseModel):
    """Agent 身份卡片"""
    name: str = Field(description="Agent 名称")
    description: str = Field(default="", description="Agent 描述")
    version: str = Field(default="1.0.0", description="版本号")
    tags: List[str] = Field(default_factory=list, description="标签")


class AgentBuilderParams(BaseModel):
    """Agent 构建参数 - 核心数据模型"""
    agent_card: AgentCard
    llm_config: LLMConfig
    feature_config: FeatureConfig = Field(default_factory=FeatureConfig)
    memory_config: MemoryConfig = Field(default_factory=MemoryConfig)
    system_prompt: str = Field(default="你是一个有用的AI助手。")
    conversation_id: str = Field(default="")

    class Config:
        # JSON 序列化时的别名
        allow_population_by_field_name = True
        # 验证赋值
        validate_assignment = True
```

#### 使用示例

```python
# 从字典创建
params = AgentBuilderParams(**{
    "agent_card": {"name": "客服助手", "description": "智能客服"},
    "llm_config": {"name": "gpt-4", "api_key": "sk-xxx"}
})

# 从 JSON 创建
import json
params = AgentBuilderParams.parse_raw(json_string)

# 验证并转换
try:
    params = AgentBuilderParams(**data)
except ValidationError as e:
    print(f"参数验证失败: {e}")

# 导出为字典
data_dict = params.dict()
# 导出为 JSON
json_str = params.json()
```

### 3.2 事件系统设计

#### 模板

```python
from enum import Enum
from typing import Union, Any
from pydantic import BaseModel
import time


class EventType(str, Enum):
    """事件类型"""
    DATA = "data"           # 数据内容
    THINKING = "thinking"   # 思考过程
    TOOL_CALL = "tool_call" # 工具调用
    ERROR = "error"         # 错误信息
    STATUS = "status"       # 状态更新


class EventStatus(str, Enum):
    """事件状态"""
    BEGIN = "begin"
    PROGRESS = "progress"
    END = "end"


class BaseEventDetail(BaseModel):
    """事件详情基类"""
    pass


class ContentEventDetail(BaseEventDetail):
    """内容事件详情"""
    content: str = ""
    is_complete: bool = False


class ErrorEventDetail(BaseEventDetail):
    """错误事件详情"""
    message: str
    code: int = 0


class ToolCallEventDetail(BaseEventDetail):
    """工具调用详情"""
    tool_name: str
    arguments: dict
    result: Any = None


class StreamEvent(BaseModel):
    """流式事件模型"""
    event_type: EventType
    status: EventStatus = EventStatus.PROGRESS
    event_name: str = ""
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    detail: Union[ContentEventDetail, ErrorEventDetail, ToolCallEventDetail, dict] = {}

    def to_sse(self) -> str:
        """转换为 SSE 格式"""
        return f"event: {self.event_type}\ndata: {self.json()}\n\n"


# ============ 使用示例 ============

# 创建事件
event = StreamEvent(
    event_type=EventType.DATA,
    status=EventStatus.BEGIN,
    detail=ContentEventDetail(content="你好，我是AI助手")
)

# SSE 输出
print(event.to_sse())
# event: data
# data: {"event_type":"data","status":"begin",...}
```

---

## 四、流式响应架构

### 4.1 核心模式：状态机缓冲器

#### 设计目标

1. **状态追踪**：清晰处理思考、内容、工具调用的状态流转
2. **增量构建**：支持流式数据的逐步组装
3. **类型安全**：明确的状态类型定义

#### 实现模板

```python
from enum import Enum
from typing import Optional, List


class ProcessStep(str, Enum):
    """处理步骤"""
    UNKNOWN = "unknown"
    THINKING_BEGIN = "thinking_begin"
    THINKING_BUFFERING = "thinking_buffering"
    THINKING_END = "thinking_end"
    CONTENT_BEGIN = "content_begin"
    CONTENT_BUFFERING = "content_buffering"
    CONTENT_END = "content_end"
    TOOL_PREPARE = "tool_prepare"
    COMPLETE = "complete"


class StreamBuffer:
    """流式缓冲器 - 状态机实现"""

    def __init__(self):
        self._step = ProcessStep.UNKNOWN
        self._thinking_buffer = ""
        self._content_buffer = ""
        self._tool_buffer: List[dict] = []

    @property
    def step(self) -> ProcessStep:
        return self._step

    @property
    def thinking(self) -> str:
        return self._thinking_buffer

    @property
    def content(self) -> str:
        return self._content_buffer

    @property
    def tools(self) -> List[dict]:
        return self._tool_buffer

    def buffer(
        self,
        content: str = "",
        thinking: str = "",
        tool_call: Optional[dict] = None
    ) -> List[ProcessStep]:
        """
        缓冲流式数据，返回状态变化列表

        Returns:
            状态变化列表，用于触发相应事件
        """
        state_changes = []

        # 初始状态判断
        if self._step == ProcessStep.UNKNOWN:
            if thinking:
                self._step = ProcessStep.THINKING_BUFFERING
                self._thinking_buffer += thinking
                return [ProcessStep.THINKING_BEGIN, ProcessStep.THINKING_BUFFERING]
            elif content:
                self._step = ProcessStep.CONTENT_BUFFERING
                self._content_buffer += content
                return [ProcessStep.CONTENT_BEGIN, ProcessStep.CONTENT_BUFFERING]
            elif tool_call:
                self._step = ProcessStep.TOOL_PREPARE
                self._tool_buffer.append(tool_call)
                return [ProcessStep.TOOL_PREPARE]
            return state_changes

        # 思考状态
        if self._step == ProcessStep.THINKING_BUFFERING:
            if tool_call:
                self._tool_buffer.append(tool_call)
                self._step = ProcessStep.TOOL_PREPARE
                state_changes.extend([
                    ProcessStep.THINKING_END,
                    ProcessStep.TOOL_PREPARE
                ])
            elif content:
                self._content_buffer += content
                self._step = ProcessStep.CONTENT_BUFFERING
                state_changes.extend([
                    ProcessStep.THINKING_END,
                    ProcessStep.CONTENT_BEGIN,
                    ProcessStep.CONTENT_BUFFERING
                ])
            else:
                self._thinking_buffer += thinking

        # 内容状态
        elif self._step == ProcessStep.CONTENT_BUFFERING:
            if tool_call:
                self._tool_buffer.append(tool_call)
                self._step = ProcessStep.TOOL_PREPARE
                state_changes.extend([
                    ProcessStep.CONTENT_END,
                    ProcessStep.TOOL_PREPARE
                ])
            else:
                self._content_buffer += content

        # 工具准备状态
        elif self._step == ProcessStep.TOOL_PREPARE:
            if tool_call:
                self._tool_buffer.append(tool_call)

        return state_changes

    def reset(self):
        """重置缓冲器"""
        self._step = ProcessStep.UNKNOWN
        self._thinking_buffer = ""
        self._content_buffer = ""
        self._tool_buffer = []
```

#### 使用示例

```python
buffer = StreamBuffer()

# 模拟流式输入
chunks = [
    {"thinking": "让我思考一下..."},
    {"thinking": "这是一个有趣的问题。"},
    {"content": "根据我的分析，"},
    {"content": "答案是这样的..."},
    {"tool_call": {"name": "search", "args": {"query": "xxx"}}},
    {"content": "综合以上信息..."},
]

for chunk in chunks:
    changes = buffer.buffer(**chunk)
    for change in changes:
        print(f"状态变化: {change}")
        # 根据状态触发相应事件
        if change == ProcessStep.THINKING_BEGIN:
            yield StreamEvent(event_type=EventType.THINKING, status=EventStatus.BEGIN)
        elif change == ProcessStep.THINKING_BUFFERING:
            yield StreamEvent(
                event_type=EventType.THINKING,
                detail=ContentEventDetail(content=buffer.thinking)
            )
        # ... 其他状态处理

print(f"最终思考: {buffer.thinking}")
print(f"最终内容: {buffer.content}")
print(f"工具调用: {buffer.tools}")
```

### 4.2 FastAPI 流式响应

```python
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
import asyncio

app = FastAPI()


@app.post("/chat")
async def chat_stream(request: Request, params: AgentBuilderParams):
    """流式聊天接口"""

    async def event_generator():
        """事件生成器"""
        buffer = StreamBuffer()

        try:
            # 模拟 LLM 流式输出
            async for chunk in llm_stream(prompt=params.system_prompt):
                changes = buffer.buffer(content=chunk)

                for change in changes:
                    # 根据状态变化生成事件
                    if "thinking" in change.value:
                        event = StreamEvent(
                            event_type=EventType.THINKING,
                            detail=ContentEventDetail(content=buffer.thinking)
                        )
                    elif "content" in change.value:
                        event = StreamEvent(
                            event_type=EventType.DATA,
                            detail=ContentEventDetail(content=buffer.content)
                        )
                    else:
                        continue

                    yield event.to_sse()

            # 完成事件
            complete_event = StreamEvent(
                event_type=EventType.STATUS,
                status=EventStatus.END,
                detail=ContentEventDetail(
                    content=buffer.content,
                    is_complete=True
                )
            )
            yield complete_event.to_sse()

        except Exception as e:
            error_event = StreamEvent(
                event_type=EventType.ERROR,
                detail=ErrorEventDetail(message=str(e), code=500)
            )
            yield error_event.to_sse()

    return EventSourceResponse(event_generator())
```

---

## 五、代理模式：多模型统一接入

### 5.1 设计目标

1. **统一接口**：不同模型使用相同的调用方式
2. **透明切换**：更换模型不需要修改业务代码
3. **统一监控**：集中记录使用量、成本

### 5.2 实现模板

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
import httpx
import json


class BaseLLMProvider(ABC):
    """LLM 提供商基类"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = httpx.AsyncClient(timeout=config.timeout)

    @abstractmethod
    async def astream(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式生成"""
        pass

    @abstractmethod
    async def ainvoke(self, messages: list[dict]) -> str:
        """单次调用"""
        pass


class OpenAIProvider(BaseLLMProvider):
    """OpenAI 兼容的提供商"""

    async def astream(self, messages: list[dict]) -> AsyncIterator[str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.config.name,
            "messages": messages,
            "stream": True,
            "temperature": self.config.temperature
        }

        async with self.client.stream(
            "POST",
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]
                    if "content" in delta:
                        yield delta["content"]


class QwenProvider(BaseLLMProvider):
    """通义千问提供商"""

    async def astream(self, messages: list[dict]) -> AsyncIterator[str]:
        # 类似实现，根据千问 API 调整
        pass


# ============ 统一代理 ============

class LLMProxy:
    """LLM 统一代理"""

    # 提供商注册表
    _providers = {
        "openai": OpenAIProvider,
        "qwen": QwenProvider,
        # ... 其他提供商
    }

    @classmethod
    def register_provider(cls, name: str, provider_class: type):
        """注册新的提供商"""
        cls._providers[name] = provider_class

    @classmethod
    def create(cls, config: LLMConfig) -> BaseLLMProvider:
        """根据配置创建提供商实例"""
        # 从模型名称推断提供商类型
        provider_name = cls._infer_provider(config.name)
        provider_class = cls._providers.get(provider_name, OpenAIProvider)
        return provider_class(config)

    @staticmethod
    def _infer_provider(model_name: str) -> str:
        """从模型名称推断提供商"""
        if "gpt" in model_name.lower():
            return "openai"
        elif "qwen" in model_name.lower():
            return "qwen"
        return "openai"  # 默认


# ============ 使用示例 ============

# 创建代理
config = LLMConfig(
    name="gpt-4",
    base_url="https://api.openai.com",
    api_key="sk-xxx"
)
proxy = LLMProxy.create(config)

# 统一调用
async for chunk in proxy.astream([{"role": "user", "content": "你好"}]):
    print(chunk, end="")
```

---

## 六、应用启动框架

### 6.1 设计目标

1. **配置驱动**：通过配置文件决定加载哪个服务
2. **模块解耦**：各服务独立开发、测试、部署
3. **统一管理**：统一的启动、停止、重启逻辑

### 6.2 实现模板

```python
import importlib
import asyncio
import uvicorn
from typing import Optional


class ApplicationBase:
    """应用基础类"""

    def __init__(self, app_name: str, config_dir: str = "config"):
        self.app_name = app_name
        self.config = self._load_config(app_name, config_dir)
        self.app = self._load_app()

    def _load_config(self, app_name: str, config_dir: str) -> dict:
        """加载应用配置"""
        from config_loader import load_config
        apps_config = load_config('apps.yaml', config_dir)
        return apps_config['apps'].get(app_name, {})

    def _load_app(self):
        """动态加载应用模块"""
        module_path = self.config.get('module')
        if not module_path:
            raise ValueError(f"未配置 module 路径: {self.app_name}")

        module = importlib.import_module(module_path)
        app = getattr(module, 'app', None)
        if not app:
            raise ValueError(f"模块 {module_path} 中未找到 app 对象")

        return app

    async def _run_server(self):
        """运行服务器"""
        config = uvicorn.Config(
            app=self.app,
            host=self.config.get('host', '0.0.0.0'),
            port=self.config.get('port', 8000),
            reload=self.config.get('reload', False),
            log_level=self.config.get('log_level', 'info')
        )
        server = uvicorn.Server(config)
        await server.serve()

    def run(self, directly: bool = True):
        """
        运行应用

        Args:
            directly: True=直接运行，False=返回 app 对象（用于测试）
        """
        print(f"\n{'='*50}")
        print(f"启动应用: {self.app_name}")
        print(f"配置: {self.config}")
        print(f"{'='*50}\n")

        if directly:
            asyncio.run(self._run_server())
        return self.app


# ============ 命令行入口 ============

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="应用启动器")
    parser.add_argument(
        '-a', '--app',
        type=str,
        required=True,
        help='应用名称（对应 apps.yaml 中的 key）'
    )
    parser.add_argument(
        '--config-dir',
        type=str,
        default='config',
        help='配置文件目录'
    )

    args = parser.parse_args()

    app = ApplicationBase(args.app, args.config_dir)
    app.run()
```

### 6.3 配置文件示例

```yaml
# config/apps.yaml
apps:
  api_service:
    module: services.api.app
    host: 0.0.0.0
    port: 8000
    reload: true
    log_level: info

  worker_service:
    module: services.worker.app
    host: 0.0.0.0
    port: 8001
    reload: false
    log_level: warning
```

### 6.4 使用示例

```bash
# 启动 API 服务
python app_base.py -a api_service

# 启动 Worker 服务
python app_base.py -a worker_service

# 指定配置目录
python app_base.py -a api_service --config-dir /etc/myapp/config
```

---

## 七、工作流编排

### 7.1 基于 LangGraph 的状态机

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from operator import add


# ============ 状态定义 ============

class WorkflowState(TypedDict):
    """工作流状态"""
    messages: Annotated[list, add]  # 消息列表（累加）
    current_step: str                # 当前步骤
    context: dict                    # 上下文数据
    result: dict                     # 最终结果


# ============ 节点定义 ============

async def coordinator_node(state: WorkflowState) -> WorkflowState:
    """协调节点：任务分析和分发"""
    print("协调器: 分析任务...")

    # 分析任务类型
    last_message = state['messages'][-1]
    task_type = analyze_task(last_message)

    state['context']['task_type'] = task_type
    state['current_step'] = 'coordinator'

    return state


async def researcher_node(state: WorkflowState) -> WorkflowState:
    """研究节点：信息收集"""
    print("研究者: 收集信息...")

    # 执行研究
    research_result = await do_research(state['context'])

    state['context']['research'] = research_result
    state['current_step'] = 'researcher'

    return state


async def writer_node(state: WorkflowState) -> WorkflowState:
    """写作节点：内容生成"""
    print("写作者: 生成内容...")

    # 生成内容
    content = await generate_content(state['context'])

    state['result']['content'] = content
    state['current_step'] = 'writer'

    return state


# ============ 路由逻辑 ============

def should_research(state: WorkflowState) -> str:
    """判断是否需要研究"""
    return state['context'].get('task_type') == 'research'


# ============ 构建图 ============

def build_workflow_graph():
    """构建工作流图"""
    builder = StateGraph(WorkflowState)

    # 添加节点
    builder.add_node("coordinator", coordinator_node)
    builder.add_node("researcher", researcher_node)
    builder.add_node("writer", writer_node)

    # 设置入口
    builder.set_entry_point("coordinator")

    # 添加边（流程）
    builder.add_conditional_edges(
        "coordinator",
        should_research,
        {
            True: "researcher",   # 需要研究
            False: "writer"       # 直接写作
        }
    )

    builder.add_edge("researcher", "writer")
    builder.add_edge("writer", END)

    return builder.compile()


# ============ 使用示例 ============

async def run_workflow(user_message: str):
    """运行工作流"""
    graph = build_workflow_graph()

    initial_state: WorkflowState = {
        'messages': [user_message],
        'current_step': '',
        'context': {},
        'result': {}
    }

    # 执行工作流
    async for state in graph.astream(initial_state):
        print(f"当前状态: {state}")

    return state
```

---

## 八、错误处理与监控

### 8.1 统一错误码

```python
from enum import Enum
from typing import NamedTuple


class ErrorCode(int, Enum):
    """错误码枚举"""
    # 通用错误 1xxx
    UNKNOWN_ERROR = 1000
    VALIDATION_ERROR = 1001
    NOT_FOUND = 1002

    # LLM 错误 2xxx
    LLM_API_ERROR = 2000
    LLM_TIMEOUT = 2001
    LLM_RATE_LIMIT = 2002

    # 数据库错误 3xxx
    DB_ERROR = 3000
    DB_CONNECTION_ERROR = 3001

    # 业务错误 4xxx
    AGENT_NOT_FOUND = 4000
    CONVERSATION_NOT_FOUND = 4001


class APIError(NamedTuple):
    """API 错误定义"""
    code: ErrorCode
    message: str
    http_status: int = 500


# 错误码映射
ERROR_MAP = {
    ErrorCode.VALIDATION_ERROR: APIError(
        ErrorCode.VALIDATION_ERROR,
        "参数验证失败",
        400
    ),
    ErrorCode.NOT_FOUND: APIError(
        ErrorCode.NOT_FOUND,
        "资源不存在",
        404
    ),
    ErrorCode.LLM_API_ERROR: APIError(
        ErrorCode.LLM_API_ERROR,
        "LLM 服务异常",
        500
    ),
    # ... 其他错误
}


def get_error(error_code: ErrorCode) -> APIError:
    """获取错误信息"""
    return ERROR_MAP.get(error_code, APIError(
        ErrorCode.UNKNOWN_ERROR,
        "未知错误",
        500
    ))
```

### 8.2 使用量监控

```python
import time
from collections import defaultdict
from typing import Callable
import functools


class UsageMonitor:
    """使用量监控"""

    def __init__(self):
        self._token_usage = defaultdict(lambda: {"input": 0, "output": 0})
        self._call_count = defaultdict(int)
        self._call_duration = defaultdict(list)

    def record(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        duration: float
    ):
        """记录使用情况"""
        self._token_usage[model_name]["input"] += input_tokens
        self._token_usage[model_name]["output"] += output_tokens
        self._call_count[model_name] += 1
        self._call_duration[model_name].append(duration)

    def get_stats(self, model_name: str = None) -> dict:
        """获取统计信息"""
        if model_name:
            return {
                "tokens": self._token_usage[model_name],
                "calls": self._call_count[model_name],
                "avg_duration": sum(self._call_duration[model_name]) /
                               len(self._call_duration[model_name])
                               if self._call_duration[model_name] else 0
            }
        return dict(self._token_usage)


# 装饰器
def monitor_llm_call(monitor: UsageMonitor):
    """LLM 调用监控装饰器"""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            model_name = kwargs.get('model', 'default')
            start_time = time.time()

            try:
                result = await func(*args, **kwargs)
                # 记录使用情况（假设 result 中有 token 信息）
                monitor.record(
                    model_name=model_name,
                    input_tokens=result.get('input_tokens', 0),
                    output_tokens=result.get('output_tokens', 0),
                    duration=time.time() - start_time
                )
                return result
            except Exception as e:
                # 记录错误
                return {"error": str(e)}

        return wrapper
    return decorator


# ============ 使用示例 ============

monitor = UsageMonitor()

@monitor_llm_call(monitor)
async def call_llm(prompt: str, model: str = "gpt-4"):
    # LLM 调用逻辑
    return {"input_tokens": 10, "output_tokens": 100}
```

---

## 九、项目模板

### 9.1 推荐目录结构

```
my_llm_app/
├── app.py                    # 应用启动入口
├── config/                   # 配置文件
│   ├── config_loader.py      # 配置加载器
│   ├── settings.yaml         # 主配置
│   └── services.yaml         # 服务配置
│
├── core/                     # 核心业务
│   ├── models.py             # 数据模型
│   ├── agent.py              # Agent 实现
│   └── workflow.py           # 工作流
│
├── api/                      # API 层
│   ├── app.py                # FastAPI 应用
│   └── routes/               # 路由
│       ├── chat.py
│       └── agent.py
│
├── services/                 # 服务层
│   ├── llm_proxy.py          # LLM 代理
│   └── rag_service.py        # RAG 服务
│
├── utils/                    # 工具
│   ├── logger.py             # 日志
│   └── monitor.py            # 监控
│
└── tests/                    # 测试
    ├── test_models.py
    └── test_api.py
```

### 9.2 依赖模板 (requirements.txt)

```
# Web 框架
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
sse-starlette>=1.8.0

# LLM
langchain>=0.1.0
langgraph>=0.0.20
openai>=1.0.0

# 数据
pydantic>=2.0.0
pymongo>=4.6.0
redis>=5.0.0

# 工具
httpx>=0.25.0
pyyaml>=6.0.0
python-dotenv>=1.0.0
```

---

## 十、快速开始检查清单

### 启动新项目时

- [ ] 创建配置加载器（支持 !env 等标签）
- [ ] 定义核心数据模型（使用 Pydantic）
- [ ] 实现 LLM 代理层（统一多模型接入）
- [ ] 设计事件系统（用于流式响应）
- [ ] 实现流式缓冲器（状态机模式）
- [ ] 创建应用启动框架（配置驱动）
- [ ] 定义错误码和错误处理
- [ ] 添加使用量监控
- [ ] 编写 API 路由（SSE 流式响应）
- [ ] 配置日志和监控

### 代码审查时

- [ ] 配置是否支持环境变量？
- [ ] 数据模型是否使用 Pydantic 验证？
- [ ] 流式响应是否正确处理状态？
- [ ] 错误是否统一处理？
- [ ] 是否记录使用量？
- [ ] API 是否有明确的请求/响应模型？

---

## 总结

本文档提炼了 X-LLM 项目的核心架构模式，提供了可直接复用的代码模板。关键要点：

1. **配置驱动**：通过配置文件控制行为，减少代码改动
2. **类型安全**：使用 Pydantic 进行数据验证
3. **状态机模式**：清晰处理流式数据的状态流转
4. **代理模式**：统一多模型/多服务的接入
5. **事件驱动**：统一的事件模型支持实时推送

这些模式可以组合使用，根据项目需求进行裁剪和扩展。
