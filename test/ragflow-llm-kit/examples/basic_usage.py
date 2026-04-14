"""
Basic usage examples for the ragflow-llm-kit module.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import LLMKit


def example_chat():
    """Chat model usage."""
    # ── OpenAI ──
    kit = LLMKit({
        "factory": "OpenAI",
        "api_key": "sk-your-key",
        "model_name": "gpt-4o",
        "api_base": "https://api.openai.com/v1",
    })

    # Sync chat
    answer, tokens = kit.chat("What is RAG?")
    print(f"Answer: {answer}")
    print(f"Tokens: {tokens}")

    # Streaming
    for chunk in kit.chat_stream("Explain transformers."):
        if isinstance(chunk, int):
            print(f"\n[Total tokens: {chunk}]")
        else:
            print(chunk, end="", flush=True)


def example_deepseek():
    """DeepSeek chat."""
    kit = LLMKit({
        "factory": "DeepSeek",
        "api_key": "sk-your-key",
        "model_name": "deepseek-chat",
    })
    answer, tokens = kit.chat("Hello!")
    print(answer)


def example_ollama():
    """Ollama local model."""
    kit = LLMKit({
        "factory": "Ollama",
        "api_key": "ollama",
        "model_name": "qwen2.5:7b",
        "api_base": "http://localhost:11434",
    })
    answer, tokens = kit.chat("Hello!")
    print(answer)


def example_embedding():
    """Embedding model usage."""
    kit = LLMKit({
        "factory": "OpenAI",
        "api_key": "sk-your-key",
        "model_name": "text-embedding-3-small",
    })

    vectors, tokens = kit.encode(["Hello world", "Goodbye world"])
    print(f"Embedding shape: {vectors.shape}")  # (2, 1536)
    print(f"Tokens: {tokens}")

    # Single query
    query_vec, tokens = kit.encode_queries("What is machine learning?")
    print(f"Query vector shape: {query_vec.shape}")


def example_rerank():
    """Rerank model usage."""
    kit = LLMKit({
        "factory": "Jina",
        "api_key": "jina_your-key",
        "model_name": "jina-reranker-v2-base-multilingual",
    })

    scores, tokens = kit.rerank(
        query="What is deep learning?",
        texts=["Deep learning is a subset of machine learning.",
                "The weather is nice today.",
                "Neural networks power deep learning systems."],
    )
    print(f"Relevance scores: {scores}")
    print(f"Tokens: {tokens}")


def example_list_factories():
    """List all supported providers."""
    factories = LLMKit.list_supported_factories()
    for model_type, names in factories.items():
        print(f"\n{model_type}:")
        for name in names:
            print(f"  - {name}")


if __name__ == "__main__":
    print("=== Supported Factories ===")
    example_list_factories()
