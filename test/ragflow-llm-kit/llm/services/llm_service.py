"""
LLM Service Layer - simplified version of ragflow's TenantLLMService + LLMBundle.
Provides a unified entry point to instantiate and call any supported model.

Adapted from ragflow/api/db/services/llm_service.py
"""
import logging
import re
from enum import StrEnum
from typing import Generator

from llm.models import ChatModel, EmbeddingModel, RerankModel


class LLMType(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    SPEECH2TEXT = "speech2text"
    IMAGE2TEXT = "image2text"
    TTS = "tts"


class ModelConfig:
    """Holds configuration for a single model instance."""

    def __init__(self, factory: str, api_key: str, model_name: str,
                 api_base: str = "", max_tokens: int = 8192):
        self.factory = factory
        self.api_key = api_key
        self.model_name = model_name
        self.api_base = api_base
        self.max_tokens = max_tokens


class LLMKit:
    """
    Unified LLM access kit — instantiate any model by config dict or individual params.

    Example:
        # Quick usage
        kit = LLMKit({
            "factory": "OpenAI",
            "api_key": "sk-xxx",
            "model_name": "gpt-4o",
        })
        answer, tokens = kit.chat("Hello!")

        # With system prompt
        answer, tokens = kit.chat("Hello!", system="You are a helpful assistant.")

        # Streaming
        for chunk in kit.chat_stream("Hello!"):
            print(chunk, end="")

        # Embedding
        kit_emb = LLMKit({"factory": "OpenAI", "api_key": "sk-xxx", "model_name": "text-embedding-3-small"})
        vectors, tokens = kit_emb.encode(["Hello world"])

        # Rerank
        kit_rr = LLMKit({"factory": "Jina", "api_key": "jina_xxx", "model_name": "jina-reranker-v2-base-multilingual"})
        scores, tokens = kit_rr.rerank("What is AI?", ["AI is...", "Machine learning is..."])
    """

    def __init__(self, config: dict):
        """
        Args:
            config: dict with keys:
                - factory (str): provider name, e.g. "OpenAI", "DeepSeek", "Ollama"
                - api_key (str): API key
                - model_name (str): model name
                - api_base (str, optional): base URL override
                - max_tokens (int, optional): max context length
        """
        self.config = ModelConfig(
            factory=config.get("factory", ""),
            api_key=config.get("api_key", ""),
            model_name=config.get("model_name", ""),
            api_base=config.get("api_base", ""),
            max_tokens=config.get("max_tokens", 8192),
        )
        self._chat_model = None
        self._embedding_model = None
        self._rerank_model = None

    # ─── Chat ───

    def get_chat_model(self):
        """Get or create a chat model instance."""
        if self._chat_model is None:
            factory = self.config.factory
            if factory not in ChatModel:
                raise ValueError(f"Chat model from '{factory}' is not supported. Available: {list(ChatModel.keys())}")
            self._chat_model = ChatModel[factory](
                key=self.config.api_key,
                model_name=self.config.model_name,
                base_url=self.config.api_base,
            )
        return self._chat_model

    def chat(self, message: str, system: str = None, history: list = None,
             gen_conf: dict = None) -> tuple[str, int]:
        """
        Synchronous chat.

        Returns:
            (answer_text, total_tokens)
        """
        mdl = self.get_chat_model()
        history = history or []
        if not any(m.get("role") == "user" for m in history):
            history.append({"role": "user", "content": message})
        return mdl.chat(system, history, gen_conf or {})

    def chat_stream(self, message: str, system: str = None, history: list = None,
                    gen_conf: dict = None) -> Generator[str, None, None]:
        """Streaming chat. Yields text chunks, final yield is an int (token count)."""
        mdl = self.get_chat_model()
        history = history or []
        if not any(m.get("role") == "user" for m in history):
            history.append({"role": "user", "content": message})
        yield from mdl.chat_streamly(system, history, gen_conf or {})

    # ─── Embedding ───

    def get_embedding_model(self):
        """Get or create an embedding model instance."""
        if self._embedding_model is None:
            factory = self.config.factory
            if factory not in EmbeddingModel:
                raise ValueError(f"Embedding model from '{factory}' is not supported. Available: {list(EmbeddingModel.keys())}")
            self._embedding_model = EmbeddingModel[factory](
                key=self.config.api_key,
                model_name=self.config.model_name,
                base_url=self.config.api_base,
            )
        return self._embedding_model

    def encode(self, texts: list) -> tuple:
        """
        Encode texts to embeddings.

        Returns:
            (numpy_array_of_embeddings, total_tokens)
        """
        mdl = self.get_embedding_model()
        return mdl.encode(texts)

    def encode_queries(self, text: str) -> tuple:
        """Encode a single query text."""
        mdl = self.get_embedding_model()
        return mdl.encode_queries(text)

    # ─── Rerank ───

    def get_rerank_model(self):
        """Get or create a rerank model instance."""
        if self._rerank_model is None:
            factory = self.config.factory
            if factory not in RerankModel:
                raise ValueError(f"Rerank model from '{factory}' is not supported. Available: {list(RerankModel.keys())}")
            self._rerank_model = RerankModel[factory](
                key=self.config.api_key,
                model_name=self.config.model_name,
                base_url=self.config.api_base,
            )
        return self._rerank_model

    def rerank(self, query: str, texts: list) -> tuple:
        """
        Rerank texts by relevance to query.

        Returns:
            (numpy_array_of_scores, total_tokens)
        """
        mdl = self.get_rerank_model()
        return mdl.similarity(query, texts)

    # ─── Static helpers ───

    @staticmethod
    def split_model_name_and_factory(model_name: str) -> tuple[str, str | None]:
        """Parse 'model_name@factory' format."""
        arr = model_name.split("@")
        if len(arr) < 2:
            return model_name, None
        if len(arr) > 2:
            return "@".join(arr[:-1]), arr[-1]
        return arr[0], arr[-1]

    @staticmethod
    def list_supported_factories() -> dict:
        """List all supported factory names by model type."""
        return {
            "chat": sorted(ChatModel.keys()),
            "embedding": sorted(EmbeddingModel.keys()),
            "rerank": sorted(RerankModel.keys()),
        }
