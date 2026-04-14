"""
LLM utility functions - token counting and text truncation.
Extracted from ragflow/rag/utils/__init__.py
"""
import tiktoken

_encoder = tiktoken.get_encoding("cl100k_base")


def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    try:
        return len(_encoder.encode(string))
    except Exception:
        return 0


def truncate(string: str, max_len: int) -> str:
    """Returns truncated text if the length of text exceed max_len."""
    return _encoder.decode(_encoder.encode(string)[:max_len])
