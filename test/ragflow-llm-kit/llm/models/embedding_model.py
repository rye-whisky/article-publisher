"""
Embedding model implementations with plugin-based factory registration.
Base class provides encode / encode_queries interface.

Extracted from ragflow/rag/llm/embedding_model.py
"""
import json
import logging
import os
from abc import ABC
from urllib.parse import urljoin

import numpy as np
import requests
from openai import OpenAI

from llm.utils import num_tokens_from_string, truncate


class Base(ABC):
    def __init__(self, key, model_name):
        pass

    def encode(self, texts: list):
        raise NotImplementedError("Please implement encode method!")

    def encode_queries(self, text: str):
        raise NotImplementedError("Please implement encode_queries method!")

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


# ───────────────────────── OpenAI-compatible (most common) ─────────────────────────

class OpenAIEmbed(Base):
    _FACTORY_NAME = "OpenAI"
    def __init__(self, key, model_name="text-embedding-ada-002", base_url="https://api.openai.com/v1"):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        texts = [truncate(t, 8191) for t in texts]
        ress, total_tokens = [], 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i:i+batch_size], model=self.model_name)
            ress.extend([d.embedding for d in res.data])
            total_tokens += self.total_token_count(res)
        return np.array(ress), total_tokens

    def encode_queries(self, text):
        res = self.client.embeddings.create(input=[truncate(text, 8191)], model=self.model_name)
        return np.array(res.data[0].embedding), self.total_token_count(res)


# ───────────────────────── Other providers ─────────────────────────

class LocalAIEmbed(Base):
    _FACTORY_NAME = "LocalAI"
    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("Local embedding model url cannot be None")
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key="empty", base_url=base_url)
        self.model_name = model_name.split("___")[0]

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i:i+batch_size], model=self.model_name)
            ress.extend([d.embedding for d in res.data])
        return np.array(ress), 1024

    def encode_queries(self, text):
        embds, cnt = self.encode([text])
        return np.array(embds[0]), cnt


class AzureEmbed(OpenAIEmbed):
    _FACTORY_NAME = "Azure-OpenAI"
    def __init__(self, key, model_name, **kwargs):
        from openai.lib.azure import AzureOpenAI
        api_key = json.loads(key).get("api_key", "")
        api_version = json.loads(key).get("api_version", "2024-02-01")
        self.client = AzureOpenAI(api_key=api_key, azure_endpoint=kwargs["base_url"], api_version=api_version)
        self.model_name = model_name


class BaiChuanEmbed(OpenAIEmbed):
    _FACTORY_NAME = "BaiChuan"
    def __init__(self, key, model_name="Baichuan-Text-Embedding", base_url="https://api.baichuan-ai.com/v1"):
        if not base_url:
            base_url = "https://api.baichuan-ai.com/v1"
        super().__init__(key, model_name, base_url)


class QWenEmbed(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"
    def __init__(self, key, model_name="text_embedding_v2", **kwargs):
        self.key = key
        self.model_name = model_name

    def encode(self, texts: list):
        import dashscope
        batch_size = 4
        res, token_count = [], 0
        texts = [truncate(t, 2048) for t in texts]
        for i in range(0, len(texts), batch_size):
            resp = dashscope.TextEmbedding.call(model=self.model_name, input=texts[i:i+batch_size], api_key=self.key, text_type="document")
            embds = [[] for _ in range(len(resp["output"]["embeddings"]))]
            for e in resp["output"]["embeddings"]:
                embds[e["text_index"]] = e["embedding"]
            res.extend(embds)
            token_count += self.total_token_count(resp)
        return np.array(res), token_count

    def encode_queries(self, text):
        import dashscope
        resp = dashscope.TextEmbedding.call(model=self.model_name, input=text[:2048], api_key=self.key, text_type="query")
        return np.array(resp["output"]["embeddings"][0]["embedding"]), self.total_token_count(resp)


class ZhipuEmbed(Base):
    _FACTORY_NAME = "ZHIPU-AI"
    def __init__(self, key, model_name="embedding-2", **kwargs):
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        arr, tks_num = [], 0
        max_len = 512 if self.model_name.lower() == "embedding-2" else 3072
        texts = [truncate(t, max_len) for t in texts]
        for txt in texts:
            res = self.client.embeddings.create(input=txt, model=self.model_name)
            arr.append(res.data[0].embedding)
            tks_num += self.total_token_count(res)
        return np.array(arr), tks_num

    def encode_queries(self, text):
        res = self.client.embeddings.create(input=text, model=self.model_name)
        return np.array(res.data[0].embedding), self.total_token_count(res)


class OllamaEmbed(Base):
    _FACTORY_NAME = "Ollama"
    def __init__(self, key, model_name, **kwargs):
        from ollama import Client
        self.client = Client(host=kwargs["base_url"]) if not key or key == "x" else Client(host=kwargs["base_url"], headers={"Authorization": f"Bearer {key}"})
        self.model_name = model_name

    def encode(self, texts: list):
        arr, tks_num = [], 0
        for txt in texts:
            res = self.client.embeddings(prompt=txt, model=self.model_name, options={"use_mmap": True})
            arr.append(res["embedding"])
            tks_num += 128
        return np.array(arr), tks_num

    def encode_queries(self, text):
        res = self.client.embeddings(prompt=text, model=self.model_name, options={"use_mmap": True})
        return np.array(res["embedding"]), 128


class XinferenceEmbed(Base):
    _FACTORY_NAME = "Xinference"
    def __init__(self, key, model_name="", base_url=""):
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress, total_tokens = [], 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i:i+batch_size], model=self.model_name)
            ress.extend([d.embedding for d in res.data])
            total_tokens += self.total_token_count(res)
        return np.array(ress), total_tokens

    def encode_queries(self, text):
        res = self.client.embeddings.create(input=[text], model=self.model_name)
        return np.array(res.data[0].embedding), self.total_token_count(res)


class JinaEmbed(Base):
    _FACTORY_NAME = "Jina"
    def __init__(self, key, model_name="jina-embeddings-v3", base_url="https://api.jina.ai/v1/embeddings"):
        self.base_url = base_url or "https://api.jina.ai/v1/embeddings"
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name

    def encode(self, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        batch_size = 16
        ress, token_count = [], 0
        for i in range(0, len(texts), batch_size):
            data = {"model": self.model_name, "input": texts[i:i+batch_size], "encoding_type": "float"}
            res = requests.post(self.base_url, headers=self.headers, json=data).json()
            ress.extend([d["embedding"] for d in res["data"]])
            token_count += self.total_token_count(res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        embds, cnt = self.encode([text])
        return np.array(embds[0]), cnt


class MistralEmbed(Base):
    _FACTORY_NAME = "Mistral"
    def __init__(self, key, model_name="mistral-embed", base_url=None):
        from mistralai.client import MistralClient
        self.client = MistralClient(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        import time, random
        texts = [truncate(t, 8196) for t in texts]
        ress, token_count = [], 0
        for i in range(0, len(texts), 16):
            for _ in range(5):
                try:
                    res = self.client.embeddings(input=texts[i:i+16], model=self.model_name)
                    ress.extend([d.embedding for d in res.data])
                    token_count += self.total_token_count(res)
                    break
                except Exception:
                    time.sleep(random.uniform(20, 60))
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embeddings(input=[truncate(text, 8196)], model=self.model_name)
        return np.array(res.data[0].embedding), self.total_token_count(res)


class GeminiEmbed(Base):
    _FACTORY_NAME = "Gemini"
    def __init__(self, key, model_name="models/text-embedding-004", **kwargs):
        import google.generativeai as genai
        self.key = key
        self.model_name = "models/" + model_name

    def encode(self, texts: list):
        import google.generativeai as genai
        texts = [truncate(t, 2048) for t in texts]
        genai.configure(api_key=self.key)
        ress = []
        for i in range(0, len(texts), 16):
            result = genai.embed_content(model=self.model_name, content=texts[i:i+16], task_type="retrieval_document")
            ress.extend(result["embedding"])
        return np.array(ress), sum(num_tokens_from_string(t) for t in texts)

    def encode_queries(self, text):
        import google.generativeai as genai
        genai.configure(api_key=self.key)
        result = genai.embed_content(model=self.model_name, content=truncate(text, 2048), task_type="retrieval_document")
        return np.array(result["embedding"]), num_tokens_from_string(text)


class NvidiaEmbed(Base):
    _FACTORY_NAME = "NVIDIA"
    def __init__(self, key, model_name, base_url="https://integrate.api.nvidia.com/v1/embeddings"):
        self.api_key = key
        self.base_url = base_url or "https://integrate.api.nvidia.com/v1/embeddings"
        self.model_name = model_name
        self.headers = {"accept": "application/json", "Content-Type": "application/json", "authorization": f"Bearer {key}"}

    def encode(self, texts: list):
        ress, token_count = [], 0
        for i in range(0, len(texts), 16):
            payload = {"input": texts[i:i+16], "input_type": "query", "model": self.model_name, "encoding_format": "float", "truncate": "END"}
            res = requests.post(self.base_url, headers=self.headers, json=payload).json()
            ress.extend([d["embedding"] for d in res["data"]])
            token_count += self.total_token_count(res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        embds, cnt = self.encode([text])
        return np.array(embds[0]), cnt


class LMStudioEmbed(LocalAIEmbed):
    _FACTORY_NAME = "LM-Studio"
    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key="lm-studio", base_url=base_url)
        self.model_name = model_name


class VLLMEmbed(OpenAIEmbed):
    _FACTORY_NAME = ["VLLM", "OpenAI-API-Compatible"]
    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("url cannot be None")
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name.split("___")[0]


class CohereEmbed(Base):
    _FACTORY_NAME = "Cohere"
    def __init__(self, key, model_name, base_url=None):
        from cohere import Client
        self.client = Client(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        ress, token_count = [], 0
        for i in range(0, len(texts), 16):
            res = self.client.embed(texts=texts[i:i+16], model=self.model_name, input_type="search_document", embedding_types=["float"])
            ress.extend([d for d in res.embeddings.float])
            token_count += res.meta.billed_units.input_tokens
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embed(texts=[text], model=self.model_name, input_type="search_query", embedding_types=["float"])
        return np.array(res.embeddings.float[0]), int(res.meta.billed_units.input_tokens)


class TogetherAIEmbed(OpenAIEmbed):
    _FACTORY_NAME = "TogetherAI"
    def __init__(self, key, model_name, base_url="https://api.together.xyz/v1"):
        super().__init__(key, model_name, base_url=base_url or "https://api.together.xyz/v1")


class SILICONFLOWEmbed(Base):
    _FACTORY_NAME = "SILICONFLOW"
    def __init__(self, key, model_name, base_url="https://api.siliconflow.cn/v1/embeddings"):
        self.base_url = base_url or "https://api.siliconflow.cn/v1/embeddings"
        self.headers = {"accept": "application/json", "content-type": "application/json", "authorization": f"Bearer {key}"}
        self.model_name = model_name

    def encode(self, texts: list):
        ress, token_count = [], 0
        for i in range(0, len(texts), 16):
            payload = {"model": self.model_name, "input": texts[i:i+16], "encoding_format": "float"}
            res = requests.post(self.base_url, json=payload, headers=self.headers).json()
            ress.extend([d["embedding"] for d in res["data"]])
            token_count += self.total_token_count(res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        payload = {"model": self.model_name, "input": text, "encoding_format": "float"}
        res = requests.post(self.base_url, json=payload, headers=self.headers).json()
        return np.array(res["data"][0]["embedding"]), self.total_token_count(res)


class BedrockEmbed(Base):
    _FACTORY_NAME = "Bedrock"
    def __init__(self, key, model_name, **kwargs):
        import boto3
        key_dict = json.loads(key)
        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=key_dict.get("bedrock_region", ""),
            aws_access_key_id=key_dict.get("bedrock_ak", ""),
            aws_secret_access_key=key_dict.get("bedrock_sk", ""),
        )
        self.model_name = model_name
        self.is_amazon = self.model_name.split(".")[0] == "amazon"
        self.is_cohere = self.model_name.split(".")[0] == "cohere"

    def encode(self, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        embeddings, token_count = [], 0
        for text in texts:
            body = {"inputText": text} if self.is_amazon else {"texts": [text], "input_type": "search_document"}
            response = self.client.invoke_model(modelId=self.model_name, body=json.dumps(body))
            model_response = json.loads(response["body"].read())
            embeddings.extend([model_response["embedding"]])
            token_count += num_tokens_from_string(text)
        return np.array(embeddings), token_count

    def encode_queries(self, text):
        body = {"inputText": truncate(text, 8196)} if self.is_amazon else {"texts": [truncate(text, 8196)], "input_type": "search_query"}
        response = self.client.invoke_model(modelId=self.model_name, body=json.dumps(body))
        return np.array(json.loads(response["body"].read())["embedding"]), num_tokens_from_string(text)


class VolcEngineEmbed(OpenAIEmbed):
    _FACTORY_NAME = "VolcEngine"
    def __init__(self, key, model_name, base_url="https://ark.cn-beijing.volces.com/api/v3"):
        ark_api_key = json.loads(key).get("ark_api_key", "")
        endpoint_id = json.loads(key).get("endpoint_id", "")
        super().__init__(ark_api_key, endpoint_id, base_url or "https://ark.cn-beijing.volces.com/api/v3")


class GPUStackEmbed(OpenAIEmbed):
    _FACTORY_NAME = "GPUStack"
    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("url cannot be None")
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name


class HuggingFaceEmbed(Base):
    _FACTORY_NAME = "HuggingFace"
    def __init__(self, key, model_name, base_url=None):
        self.model_name = model_name.split("___")[0]
        self.base_url = base_url or "http://127.0.0.1:8080"

    def encode(self, texts: list):
        embeddings = []
        for text in texts:
            response = requests.post(f"{self.base_url}/embed", json={"inputs": text}, headers={"Content-Type": "application/json"})
            embeddings.append(response.json()[0])
        return np.array(embeddings), sum(num_tokens_from_string(t) for t in texts)

    def encode_queries(self, text):
        response = requests.post(f"{self.base_url}/embed", json={"inputs": text}, headers={"Content-Type": "application/json"})
        return np.array(response.json()[0]), num_tokens_from_string(text)


class NovitaEmbed(SILICONFLOWEmbed):
    _FACTORY_NAME = "NovitaAI"
    def __init__(self, key, model_name, base_url="https://api.novita.ai/v3/openai/embeddings"):
        super().__init__(key, model_name, base_url or "https://api.novita.ai/v3/openai/embeddings")


class GiteeEmbed(SILICONFLOWEmbed):
    _FACTORY_NAME = "GiteeAI"
    def __init__(self, key, model_name, base_url="https://ai.gitee.com/v1/embeddings"):
        super().__init__(key, model_name, base_url or "https://ai.gitee.com/v1/embeddings")


class DeepInfraEmbed(OpenAIEmbed):
    _FACTORY_NAME = "DeepInfra"
    def __init__(self, key, model_name, base_url="https://api.deepinfra.com/v1/openai"):
        super().__init__(key, model_name, base_url or "https://api.deepinfra.com/v1/openai")


class Ai302Embed(OpenAIEmbed):
    _FACTORY_NAME = "302.AI"
    def __init__(self, key, model_name, base_url="https://api.302.ai/v1"):
        super().__init__(key, model_name, base_url or "https://api.302.ai/v1")


class VoyageEmbed(Base):
    _FACTORY_NAME = "Voyage AI"
    def __init__(self, key, model_name, base_url=None):
        import voyageai
        self.client = voyageai.Client(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        ress, token_count = [], 0
        for i in range(0, len(texts), 16):
            res = self.client.embed(texts=texts[i:i+16], model=self.model_name, input_type="document")
            ress.extend(res.embeddings)
            token_count += res.total_tokens
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embed(texts=[text], model=self.model_name, input_type="query")
        return np.array(res.embeddings[0]), res.total_tokens


class BaiduYiyanEmbed(Base):
    _FACTORY_NAME = "BaiduYiyan"
    def __init__(self, key, model_name, base_url=None):
        import qianfan
        key_dict = json.loads(key)
        self.client = qianfan.Embedding(ak=key_dict.get("yiyan_ak"), sk=key_dict.get("yiyan_sk"))
        self.model_name = model_name

    def encode(self, texts: list, batch_size=16):
        res = self.client.do(model=self.model_name, texts=texts).body
        return np.array([r["embedding"] for r in res["data"]]), self.total_token_count(res)

    def encode_queries(self, text):
        res = self.client.do(model=self.model_name, texts=[text]).body
        return np.array([r["embedding"] for r in res["data"]]), self.total_token_count(res)
