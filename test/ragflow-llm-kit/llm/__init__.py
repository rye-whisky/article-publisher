"""
LLM module - unified interface for multi-provider LLM access.

Usage:
    from llm import LLMKit

    kit = LLMKit(config)
    chat_model = kit.get_chat_model()
    answer, tokens = chat_model.chat("You are helpful.", [{"role": "user", "content": "Hi"}])
"""
from llm.services.llm_service import LLMKit

__all__ = ["LLMKit"]
