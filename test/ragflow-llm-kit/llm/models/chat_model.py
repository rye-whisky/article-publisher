"""
Chat model implementations with plugin-based factory registration.
Base class provides OpenAI-compatible interface with retry, error classification, and tool call support.

Extracted from ragflow/rag/llm/chat_model.py
"""
import json
import logging
import os
import random
import re
import time
from abc import ABC
from copy import deepcopy
from typing import Any, Protocol
from urllib.parse import urljoin

import json_repair
import openai
from openai import OpenAI
from openai.lib.azure import AzureOpenAI
from strenum import StrEnum

from llm.utils import num_tokens_from_string


# ───────────────────────── Error handling ─────────────────────────

class LLMErrorCode(StrEnum):
    ERROR_RATE_LIMIT = "RATE_LIMIT_EXCEEDED"
    ERROR_AUTHENTICATION = "AUTH_ERROR"
    ERROR_INVALID_REQUEST = "INVALID_REQUEST"
    ERROR_SERVER = "SERVER_ERROR"
    ERROR_TIMEOUT = "TIMEOUT"
    ERROR_CONNECTION = "CONNECTION_ERROR"
    ERROR_MODEL = "MODEL_ERROR"
    ERROR_MAX_ROUNDS = "ERROR_MAX_ROUNDS"
    ERROR_CONTENT_FILTER = "CONTENT_FILTERED"
    ERROR_QUOTA = "QUOTA_EXCEEDED"
    ERROR_MAX_RETRIES = "MAX_RETRIES_EXCEEDED"
    ERROR_GENERIC = "GENERIC_ERROR"


class ReActMode(StrEnum):
    FUNCTION_CALL = "function_call"
    REACT = "react"


ERROR_PREFIX = "**ERROR**"
LENGTH_NOTIFICATION_CN = "......\n由于大模型的上下文窗口大小限制，回答已经被大模型截断。"
LENGTH_NOTIFICATION_EN = "...\nThe answer is truncated by your chosen LLM due to its limitation on context length."


class ToolCallSession(Protocol):
    def tool_call(self, name: str, arguments: dict[str, Any]) -> str: ...


# ───────────────────────── Base class ─────────────────────────

class Base(ABC):
    def __init__(self, key, model_name, base_url, **kwargs):
        timeout = int(os.environ.get("LM_TIMEOUT_SECONDS", 600))
        self.client = OpenAI(api_key=key, base_url=base_url, timeout=timeout)
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        self.max_rounds = kwargs.get("max_rounds", 5)
        self.is_tools = False
        self.tools = []
        self.toolcall_session = None

    # ── internal helpers ──

    def _get_delay(self):
        return self.base_delay * random.uniform(10, 150)

    def _classify_error(self, error):
        error_str = str(error).lower()
        keywords_mapping = [
            (["quota", "capacity", "credit", "billing", "balance"], LLMErrorCode.ERROR_QUOTA),
            (["rate limit", "429", "tpm limit", "too many requests"], LLMErrorCode.ERROR_RATE_LIMIT),
            (["auth", "key", "apikey", "401", "forbidden"], LLMErrorCode.ERROR_AUTHENTICATION),
            (["invalid", "bad request", "400", "format", "malformed"], LLMErrorCode.ERROR_INVALID_REQUEST),
            (["server", "503", "502", "504", "500"], LLMErrorCode.ERROR_SERVER),
            (["timeout", "timed out"], LLMErrorCode.ERROR_TIMEOUT),
            (["connect", "network", "unreachable", "dns"], LLMErrorCode.ERROR_CONNECTION),
            (["filter", "content", "policy", "blocked"], LLMErrorCode.ERROR_CONTENT_FILTER),
            (["model", "not found", "does not exist"], LLMErrorCode.ERROR_MODEL),
        ]
        for words, code in keywords_mapping:
            if re.search("({})".format("|".join(words)), error_str):
                return code
        return LLMErrorCode.ERROR_GENERIC

    def _clean_conf(self, gen_conf):
        gen_conf.pop("max_tokens", None)
        return gen_conf

    def _length_stop(self, ans):
        if any('\u4e00' <= c <= '\u9fff' for c in ans):
            return ans + LENGTH_NOTIFICATION_CN
        return ans + LENGTH_NOTIFICATION_EN

    def _exceptions(self, e, attempt):
        logging.exception("LLM error")
        error_code = self._classify_error(e)
        if attempt == self.max_retries:
            error_code = LLMErrorCode.ERROR_MAX_RETRIES
        should_retry = error_code in (LLMErrorCode.ERROR_RATE_LIMIT, LLMErrorCode.ERROR_SERVER)
        if not should_retry:
            return f"{ERROR_PREFIX}: {error_code} - {str(e)}"
        delay = self._get_delay()
        logging.warning(f"Error: {error_code}. Retrying in {delay:.2f}s... ({attempt + 1}/{self.max_retries})")
        time.sleep(delay)

    def _verbose_tool_use(self, name, args, res):
        return "ྲྀ" + json.dumps({"name": name, "args": args, "result": res}, ensure_ascii=False, indent=2) + " Leistungen"

    def _append_history(self, hist, tool_call, tool_res):
        hist.append({
            "role": "assistant",
            "tool_calls": [{
                "index": tool_call.index,
                "id": tool_call.id,
                "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments},
                "type": "function",
            }],
        })
        if isinstance(tool_res, dict):
            tool_res = json.dumps(tool_res, ensure_ascii=False)
        hist.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(tool_res)})
        return hist

    # ── core chat methods ──

    def _chat(self, history, gen_conf, **kwargs):
        logging.info("[HISTORY]" + json.dumps(history, ensure_ascii=False, indent=2))
        if self.model_name.lower().find("qwen3") >= 0:
            kwargs["extra_body"] = {"enable_thinking": False}
        response = self.client.chat.completions.create(model=self.model_name, messages=history, **gen_conf, **kwargs)
        if any([not response.choices, not response.choices[0].message, not response.choices[0].message.content]):
            return "", 0
        ans = response.choices[0].message.content.strip()
        if response.choices[0].finish_reason == "length":
            ans = self._length_stop(ans)
        return ans, self.total_token_count(response)

    def _chat_streamly(self, history, gen_conf, **kwargs):
        for resp in self.client.chat.completions.create(model=self.model_name, messages=history, stream=True, **gen_conf, stop=kwargs.get("stop")):
            if not resp.choices:
                continue
            if not resp.choices[0].delta.content:
                resp.choices[0].delta.content = ""
            ans = resp.choices[0].delta.content
            tol = self.total_token_count(resp)
            if not tol:
                tol = num_tokens_from_string(resp.choices[0].delta.content)
            if resp.choices[0].finish_reason == "length":
                ans += self._length_stop("")
            yield ans, tol

    def total_token_count(self, resp):
        try:
            return resp.usage.total_tokens
        except Exception:
            pass
        try:
            return resp["usage"]["total_tokens"]
        except Exception:
            pass
        return 0

    # ── public API ──

    def bind_tools(self, toolcall_session, tools):
        if not (toolcall_session and tools):
            return
        self.is_tools = True
        self.toolcall_session = toolcall_session
        self.tools = tools

    def chat_with_tools(self, system: str, history: list, gen_conf: dict = {}):
        gen_conf = self._clean_conf(gen_conf)
        if system:
            history.insert(0, {"role": "system", "content": system})
        ans, tk_count, hist = "", 0, deepcopy(history)
        for attempt in range(self.max_retries + 1):
            history = hist
            try:
                for _ in range(self.max_rounds + 1):
                    response = self.client.chat.completions.create(model=self.model_name, messages=history, tools=self.tools, tool_choice="auto", **gen_conf)
                    tk_count += self.total_token_count(response)
                    if not hasattr(response.choices[0].message, "tool_calls") or not response.choices[0].message.tool_calls:
                        ans += response.choices[0].message.content or ""
                        if response.choices[0].finish_reason == "length":
                            ans = self._length_stop(ans)
                        return ans, tk_count
                    for tool_call in response.choices[0].message.tool_calls:
                        name = tool_call.function.name
                        try:
                            args = json_repair.loads(tool_call.function.arguments)
                            tool_response = self.toolcall_session.tool_call(name, args)
                            history = self._append_history(history, tool_call, tool_response)
                            ans += self._verbose_tool_use(name, args, tool_response)
                        except Exception as e:
                            history.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(e)})
                return ans, tk_count
            except Exception as e:
                e = self._exceptions(e, attempt)
                if e:
                    return e, tk_count

    def chat(self, system, history, gen_conf={}, **kwargs):
        if system:
            history.insert(0, {"role": "system", "content": system})
        gen_conf = self._clean_conf(gen_conf)
        for attempt in range(self.max_retries + 1):
            try:
                return self._chat(history, gen_conf, **kwargs)
            except Exception as e:
                e = self._exceptions(e, attempt)
                if e:
                    return e, 0

    def chat_streamly(self, system, history, gen_conf: dict = {}, **kwargs):
        if system:
            history.insert(0, {"role": "system", "content": system})
        gen_conf = self._clean_conf(gen_conf)
        total_tokens = 0
        try:
            for delta_ans, tol in self._chat_streamly(history, gen_conf, **kwargs):
                yield delta_ans
                total_tokens += tol
        except openai.APIError as e:
            yield "\n**ERROR**: " + str(e)
        yield total_tokens


# ───────────────────────── Factory implementations ─────────────────────────

class GptTurbo(Base):
    _FACTORY_NAME = "OpenAI"
    def __init__(self, key, model_name="gpt-3.5-turbo", base_url="https://api.openai.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class MoonshotChat(Base):
    _FACTORY_NAME = "Moonshot"
    def __init__(self, key, model_name="moonshot-v1-8k", base_url="https://api.moonshot.cn/v1", **kwargs):
        if not base_url:
            base_url = "https://api.moonshot.cn/v1"
        super().__init__(key, model_name, base_url)


class XinferenceChat(Base):
    _FACTORY_NAME = "Xinference"
    def __init__(self, key=None, model_name="", base_url="", **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key, model_name, base_url, **kwargs)


class DeepSeekChat(Base):
    _FACTORY_NAME = "DeepSeek"
    def __init__(self, key, model_name="deepseek-chat", base_url="https://api.deepseek.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.deepseek.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class AzureChat(Base):
    _FACTORY_NAME = "Azure-OpenAI"
    def __init__(self, key, model_name, base_url, **kwargs):
        api_key = json.loads(key).get("api_key", "")
        api_version = json.loads(key).get("api_version", "2024-02-01")
        super().__init__(key, model_name, base_url, **kwargs)
        self.client = AzureOpenAI(api_key=api_key, azure_endpoint=base_url, api_version=api_version)
        self.model_name = model_name


class BaiChuanChat(Base):
    _FACTORY_NAME = "BaiChuan"
    def __init__(self, key, model_name="Baichuan3-Turbo", base_url="https://api.baichuan-ai.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.baichuan-ai.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class QWenChat(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"
    def __init__(self, key, model_name="qwen-turbo", base_url=None, **kwargs):
        if not base_url:
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        super().__init__(key, model_name, base_url=base_url, **kwargs)


class ZhipuChat(Base):
    _FACTORY_NAME = "ZHIPU-AI"
    def __init__(self, key, model_name="glm-3-turbo", base_url=None, **kwargs):
        from zhipuai import ZhipuAI
        super().__init__(key, model_name, base_url=base_url, **kwargs)
        self.client = ZhipuAI(api_key=key)
        self.model_name = model_name

    def _clean_conf(self, gen_conf):
        for k in ["max_tokens", "presence_penalty", "frequency_penalty"]:
            gen_conf.pop(k, None)
        return gen_conf


class OllamaChat(Base):
    _FACTORY_NAME = "Ollama"
    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key or "ollama", model_name, base_url, **kwargs)


class LocalAIChat(Base):
    _FACTORY_NAME = "LocalAI"
    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key, model_name.split("___")[0], base_url, **kwargs)


class VolcEngineChat(Base):
    _FACTORY_NAME = "VolcEngine"
    def __init__(self, key, model_name, base_url="https://ark.cn-beijing.volces.com/api/v3", **kwargs):
        if not base_url:
            base_url = "https://ark.cn-beijing.volces.com/api/v3"
        ark_api_key = json.loads(key).get("ark_api_key", "")
        endpoint_id = json.loads(key).get("endpoint_id", "")
        super().__init__(ark_api_key, endpoint_id, base_url, **kwargs)


class MiniMaxChat(Base):
    _FACTORY_NAME = "MiniMax"
    def __init__(self, key, model_name="abab6.5s-chat", base_url="https://api.minimax.chat/v1", **kwargs):
        if not base_url:
            base_url = "https://api.minimax.chat/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class MistralChat(Base):
    _FACTORY_NAME = "Mistral"
    def __init__(self, key, model_name="open-mistral-7b", base_url="https://api.mistral.ai/v1", **kwargs):
        if not base_url:
            base_url = "https://api.mistral.ai/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class BedrockChat(Base):
    _FACTORY_NAME = "Bedrock"
    def __init__(self, key, model_name, base_url=None, **kwargs):
        import boto3
        key_dict = json.loads(key)
        self.client_bedrock = boto3.client(
            service_name="bedrock-runtime",
            region_name=key_dict.get("bedrock_region", ""),
            aws_access_key_id=key_dict.get("bedrock_ak", ""),
            aws_secret_access_key=key_dict.get("bedrock_sk", ""),
        )
        self.model_name = model_name
        # Override base class client to avoid OpenAI init errors
        self.max_retries = kwargs.get("max_retries", 3)

    def chat(self, system, history, gen_conf={}, **kwargs):
        # Bedrock uses its own client, simplified implementation
        raise NotImplementedError("Use Bedrock SDK directly or extend this class")


class GeminiChat(Base):
    _FACTORY_NAME = "Gemini"
    def __init__(self, key, model_name="gemini-pro", base_url=None, **kwargs):
        import google.generativeai as genai
        genai.configure(api_key=key)
        self.genai_model = genai.GenerativeModel(model_name)
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", 3)

    def chat(self, system, history, gen_conf={}, **kwargs):
        prompt = "\n".join([m.get("content", "") for m in history])
        if system:
            prompt = system + "\n" + prompt
        response = self.genai_model.generate_content(prompt)
        return response.text, 0


class GroqChat(Base):
    _FACTORY_NAME = "Groq"
    def __init__(self, key, model_name="mixtral-8x7b-32768", base_url="https://api.groq.com/openai/v1", **kwargs):
        if not base_url:
            base_url = "https://api.groq.com/openai/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class OpenRouterChat(Base):
    _FACTORY_NAME = "OpenRouter"
    def __init__(self, key, model_name, base_url="https://openrouter.ai/api/v1", **kwargs):
        if not base_url:
            base_url = "https://openrouter.ai/api/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class StepFunChat(Base):
    _FACTORY_NAME = "StepFun"
    def __init__(self, key, model_name, base_url="https://api.stepfun.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.stepfun.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class NVIDIAChat(Base):
    _FACTORY_NAME = "NVIDIA"
    def __init__(self, key, model_name, base_url="https://integrate.api.nvidia.com/v1", **kwargs):
        if not base_url:
            base_url = "https://integrate.api.nvidia.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class LMStudioChat(Base):
    _FACTORY_NAME = "LM-Studio"
    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__("lm-studio", model_name, base_url, **kwargs)


class VLLMChat(Base):
    _FACTORY_NAME = ["VLLM", "OpenAI-API-Compatible"]
    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key, model_name.split("___")[0], base_url, **kwargs)


class CohereChat(Base):
    _FACTORY_NAME = "Cohere"
    def __init__(self, key, model_name="command-r-plus", base_url=None, **kwargs):
        super().__init__(key, model_name, base_url, **kwargs)


class xAIChat(Base):
    _FACTORY_NAME = "xAI"
    def __init__(self, key, model_name="grok-3", base_url=None, **kwargs):
        if not base_url:
            base_url = "https://api.x.ai/v1"
        super().__init__(key, model_name, base_url=base_url, **kwargs)


class SILICONFLOWChat(Base):
    _FACTORY_NAME = "SILICONFLOW"
    def __init__(self, key, model_name, base_url="https://api.siliconflow.cn/v1", **kwargs):
        if not base_url:
            base_url = "https://api.siliconflow.cn/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class AnthropicChat(Base):
    _FACTORY_NAME = "Anthropic"
    def __init__(self, key, model_name="claude-3-sonnet-20240229", base_url="https://api.anthropic.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.anthropic.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class GoogleCloudChat(Base):
    _FACTORY_NAME = "Google Cloud"
    def __init__(self, key, model_name, base_url=None, **kwargs):
        key_dict = json.loads(key)
        import google.generativeai as genai
        genai.configure(api_key=key_dict.get("google_service_account_key", ""))
        self.genai_model = genai.GenerativeModel(model_name)
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", 3)


class GPUStackChat(Base):
    _FACTORY_NAME = "GPUStack"
    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key, model_name, base_url, **kwargs)


class DeepInfraChat(Base):
    _FACTORY_NAME = "DeepInfra"
    def __init__(self, key, model_name, base_url="https://api.deepinfra.com/v1/openai", **kwargs):
        if not base_url:
            base_url = "https://api.deepinfra.com/v1/openai"
        super().__init__(key, model_name, base_url, **kwargs)


class HuggingFaceChat(Base):
    _FACTORY_NAME = "HuggingFace"
    def __init__(self, key=None, model_name="", base_url="", **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key, model_name.split("___")[0], base_url, **kwargs)


class ModelScopeChat(Base):
    _FACTORY_NAME = "ModelScope"
    def __init__(self, key=None, model_name="", base_url="", **kwargs):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        super().__init__(key, model_name.split("___")[0], base_url, **kwargs)


class PPIOChat(Base):
    _FACTORY_NAME = "PPIO"
    def __init__(self, key, model_name, base_url="https://api.ppinfra.com/v1/openai", **kwargs):
        if not base_url:
            base_url = "https://api.ppinfra.com/v1/openai"
        super().__init__(key, model_name, base_url, **kwargs)


class TogetherAIChat(Base):
    _FACTORY_NAME = "TogetherAI"
    def __init__(self, key, model_name, base_url="https://api.together.xyz/v1", **kwargs):
        if not base_url:
            base_url = "https://api.together.xyz/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class LeptonAIChat(Base):
    _FACTORY_NAME = "LeptonAI"
    def __init__(self, key, model_name, base_url="https://llama2-7b.lepton.run/api/v1", **kwargs):
        if not base_url:
            base_url = "https://llama2-7b.lepton.run/api/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class PerfXCloudChat(Base):
    _FACTORY_NAME = "PerfXCloud"
    def __init__(self, key, model_name, base_url="https://cloud.perfxlab.cn/v1", **kwargs):
        if not base_url:
            base_url = "https://cloud.perfxlab.cn/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class UpstageChat(Base):
    _FACTORY_NAME = "Upstage"
    def __init__(self, key, model_name, base_url="https://api.upstage.ai/v1/solar", **kwargs):
        if not base_url:
            base_url = "https://api.upstage.ai/v1/solar"
        super().__init__(key, model_name, base_url, **kwargs)


class NovitaAIChat(Base):
    _FACTORY_NAME = "NovitaAI"
    def __init__(self, key, model_name, base_url="https://api.novita.ai/v3/openai", **kwargs):
        if not base_url:
            base_url = "https://api.novita.ai/v3/openai"
        super().__init__(key, model_name, base_url, **kwargs)


class YiAIChat(Base):
    _FACTORY_NAME = "01.AI"
    def __init__(self, key, model_name, base_url="https://api.lingyiwanwu.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.lingyiwanwu.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class GiteeAIChat(Base):
    _FACTORY_NAME = "GiteeAI"
    def __init__(self, key, model_name, base_url="https://ai.gitee.com/v1", **kwargs):
        if not base_url:
            base_url = "https://ai.gitee.com/v1"
        super().__init__(key, model_name, base_url, **kwargs)


class ReplicateChat(Base):
    _FACTORY_NAME = "Replicate"
    def __init__(self, key, model_name, base_url=None, **kwargs):
        from replicate.client import Client
        self.client = Client(api_token=key)
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", 3)


class XunFeiChat(Base):
    _FACTORY_NAME = "XunFei Spark"
    def __init__(self, key, model_name, base_url=None, **kwargs):
        super().__init__(key, model_name, base_url, **kwargs)


class TencentHunyuanChat(Base):
    _FACTORY_NAME = "Tencent Hunyuan"
    def __init__(self, key, model_name, base_url=None, **kwargs):
        key_dict = json.loads(key)
        from tencentcloud.common import credential
        cred = credential.Credential(key_dict.get("hunyuan_sid"), key_dict.get("hunyuan_sk"))
        self.cred = cred
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", 3)


class BaiduYiyanChat(Base):
    _FACTORY_NAME = "BaiduYiyan"
    def __init__(self, key, model_name, base_url=None, **kwargs):
        key_dict = json.loads(key)
        import qianfan
        self.client = qianfan.ChatCompletion(ak=key_dict.get("yiyan_ak"), sk=key_dict.get("yiyan_sk"))
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", 3)


class Ai302Chat(Base):
    _FACTORY_NAME = "302.AI"
    def __init__(self, key, model_name, base_url="https://api.302.ai/v1", **kwargs):
        if not base_url:
            base_url = "https://api.302.ai/v1"
        super().__init__(key, model_name, base_url, **kwargs)
