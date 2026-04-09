# -*- coding: utf-8 -*-
"""
Multi-model LLM service: task-based routing to different model providers.

Each AI task (abstract, edit, translate, etc.) can be configured with a
different LLM provider. Config is stored in DB settings with the key pattern:
    llm_{task}_{field}   where field ∈ {factory, api_url, api_key, model}
Legacy keys (llm_api_url, llm_api_key, llm_model) map to the "abstract" task.
"""

import logging
from typing import Optional

from openai import OpenAI

log = logging.getLogger("pipeline")

# ───────────────────────── Factory registry ─────────────────────────

# Known providers with default API base URLs.
FACTORY_DEFAULTS = {
    "OpenAI":             "https://api.openai.com/v1",
    "DeepSeek":           "https://api.deepseek.com/v1",
    "Tongyi-Qianwen":     "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "ZHIPU-AI":           "https://open.bigmodel.cn/api/paas/v4",
    "Moonshot":           "https://api.moonshot.cn/v1",
    "Anthropic":          "https://api.anthropic.com/v1",
    "Groq":               "https://api.groq.com/openai/v1",
    "xAI":                "https://api.x.ai/v1",
    "SILICONFLOW":        "https://api.siliconflow.cn/v1",
    "Ollama":             "",
    "OpenAI-Compatible":  "",
}

# Built-in task definitions.
TASKS = {
    "abstract": "摘要生成",
    "edit":     "编辑正文",
}

# ───────────────────────── ModelProvider ─────────────────────────

class ModelProvider:
    """Wraps a single LLM endpoint using the OpenAI-compatible API."""

    def __init__(self, factory: str, api_key: str, model_name: str,
                 api_base: str = "", timeout: int = 180):
        self.factory = factory
        self.model_name = model_name
        base = api_base or FACTORY_DEFAULTS.get(factory, "")
        self.client = OpenAI(
            api_key=api_key or "no-key",
            base_url=base or None,
            timeout=timeout,
        )

    def chat(self, system_prompt: str, user_message: str,
             max_tokens: int = 4096, temperature: float = 0.3) -> str:
        """Synchronous chat call. Returns assistant text or raises."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0] if resp.choices else None
        if not choice or not choice.message:
            return ""
        return (choice.message.content or "").strip()

    def test(self) -> dict:
        """Quick connectivity test. Returns {"ok": bool, "reply": str, "error": str}."""
        try:
            reply = self.chat("", "Hi, reply with just OK.", max_tokens=8)
            return {"ok": True, "reply": reply[:100], "error": ""}
        except Exception as e:
            return {"ok": False, "reply": "", "error": str(e)[:200]}


# ───────────────────────── LLMService ─────────────────────────

class LLMService:
    """Multi-model manager. Routes tasks to their configured provider."""

    def __init__(self, db):
        self.db = db
        self._providers: dict[str, ModelProvider] = {}

    # ── config helpers ──

    def _get_task_config(self, task: str) -> dict:
        """Read all settings for a task, with legacy fallback."""
        fields = ("factory", "api_url", "api_key", "model")
        cfg = {}
        for f in fields:
            # New key pattern: llm_{task}_{field}
            val = (self.db.get_setting(f"llm_{task}_{f}") or "").strip()
            if val:
                cfg[f] = val

        # Legacy fallback for "abstract" task
        if task == "abstract":
            legacy_map = {
                "api_url": "llm_api_url",
                "api_key": "llm_api_key",
                "model":   "llm_model",
            }
            for field, legacy_key in legacy_map.items():
                if field not in cfg:
                    val = (self.db.get_setting(legacy_key) or "").strip()
                    if val:
                        cfg[field] = val

        # Fallback to abstract task for any other unconfigured task
        if task != "abstract" and not cfg.get("model"):
            abs_cfg = self._get_task_config("abstract")
            for f in fields:
                if f not in cfg and f in abs_cfg:
                    cfg[f] = abs_cfg[f]

        return cfg

    # ── provider management ──

    def get_provider(self, task: str) -> Optional[ModelProvider]:
        """Get (or create) a cached provider for the given task."""
        cfg = self._get_task_config(task)
        cache_key = (task, cfg.get("factory", ""), cfg.get("api_url", ""),
                     cfg.get("api_key", ""), cfg.get("model", ""))

        if cache_key in self._providers:
            return self._providers[cache_key]

        if not cfg.get("api_key") or not cfg.get("model"):
            return None

        provider = ModelProvider(
            factory=cfg.get("factory", "OpenAI"),
            api_key=cfg["api_key"],
            model_name=cfg["model"],
            api_base=cfg.get("api_url", ""),
        )
        self._providers[cache_key] = provider
        return provider

    def invalidate(self, task: str = None):
        """Clear cached provider(s). Call after settings update."""
        if task:
            self._providers = {k: v for k, v in self._providers.items() if k[0] != task}
        else:
            self._providers.clear()

    # ── high-level API ──

    def chat(self, task: str, system_prompt: str, user_message: str,
             **kwargs) -> Optional[str]:
        """Route to the provider for *task* and call chat."""
        provider = self.get_provider(task)
        if provider is None:
            log.warning("[LLMService] task=%s not configured", task)
            return None
        return provider.chat(system_prompt, user_message, **kwargs)

    def test_connection(self, task: str) -> dict:
        """Test the provider for *task*. Returns {"ok": bool, ...}."""
        provider = self.get_provider(task)
        if provider is None:
            return {"ok": False, "reply": "", "error": f"任务 '{task}' 未配置模型"}
        return provider.test()

    def get_task_settings(self, task: str) -> dict:
        """Return the resolved settings dict for a task (api_key masked)."""
        cfg = self._get_task_config(task)
        key = cfg.get("api_key", "")
        if key and len(key) > 4:
            cfg["api_key"] = "*" * (len(key) - 4) + key[-4:]
        return cfg

    @staticmethod
    def list_factories() -> dict:
        """Return factory name → default api_base mapping."""
        return dict(FACTORY_DEFAULTS)

    @staticmethod
    def list_tasks() -> dict:
        """Return task name → display name mapping."""
        return dict(TASKS)
