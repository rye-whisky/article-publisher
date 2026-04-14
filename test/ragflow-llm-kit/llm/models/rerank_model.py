"""
Rerank model implementations with plugin-based factory registration.
Base class provides similarity(query, texts) interface.

Extracted from ragflow/rag/llm/rerank_model.py
"""
import json
import logging
from abc import ABC
from urllib.parse import urljoin

import numpy as np
import requests

from llm.utils import num_tokens_from_string, truncate


class Base(ABC):
    def __init__(self, key, model_name):
        pass

    def similarity(self, query: str, texts: list):
        raise NotImplementedError("Please implement similarity method!")

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


# ───────────────────────── Jina-compatible rerank ─────────────────────────

class JinaRerank(Base):
    _FACTORY_NAME = "Jina"
    def __init__(self, key, model_name="jina-reranker-v2-base-multilingual", base_url="https://api.jina.ai/v1/rerank"):
        self.base_url = base_url or "https://api.jina.ai/v1/rerank"
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        data = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as e:
            logging.exception(f"Jina rerank error: {e}")
        return rank, self.total_token_count(res)


# ───────────────────────── Xinference rerank ─────────────────────────

class XinferenceRerank(Base):
    _FACTORY_NAME = "Xinference"
    def __init__(self, key="x", model_name="", base_url=""):
        if base_url.find("/v1") == -1:
            base_url = urljoin(base_url, "/v1/rerank")
        if base_url.find("/rerank") == -1:
            base_url = urljoin(base_url, "/v1/rerank")
        self.model_name = model_name
        self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "accept": "application/json"}
        if key and key != "x":
            self.headers["Authorization"] = f"Bearer {key}"

    def similarity(self, query: str, texts: list):
        if not texts:
            return np.array([]), 0
        pairs = [(query, truncate(t, 4096)) for t in texts]
        token_count = sum(num_tokens_from_string(t) for _, t in pairs)
        data = {"model": self.model_name, "query": query, "return_documents": "true", "return_len": "true", "documents": texts}
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as e:
            logging.exception(f"Xinference rerank error: {e}")
        return rank, token_count


# ───────────────────────── Local rerank ─────────────────────────

class LocalAIRerank(Base):
    _FACTORY_NAME = "LocalAI"
    def __init__(self, key, model_name, base_url):
        if base_url.find("/rerank") == -1:
            self.base_url = urljoin(base_url, "/rerank")
        else:
            self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        texts = [truncate(t, 500) for t in texts]
        data = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        token_count = sum(num_tokens_from_string(t) for t in texts)
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as e:
            logging.exception(f"LocalAI rerank error: {e}")
        min_r, max_r = np.min(rank), np.max(rank)
        if max_r - min_r != 0:
            rank = (rank - min_r) / (max_r - min_r)
        else:
            rank = np.zeros_like(rank)
        return rank, token_count


# ───────────────────────── NVIDIA rerank ─────────────────────────

class NvidiaRerank(Base):
    _FACTORY_NAME = "NVIDIA"
    def __init__(self, key, model_name, base_url="https://ai.api.nvidia.com/v1/retrieval/nvidia/"):
        self.model_name = model_name
        self.base_url = urljoin(base_url or "https://ai.api.nvidia.com/v1/retrieval/nvidia/", "reranking")
        self.headers = {"accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {key}"}

    def similarity(self, query: str, texts: list):
        token_count = num_tokens_from_string(query) + sum(num_tokens_from_string(t) for t in texts)
        data = {"model": self.model_name, "query": {"text": query}, "passages": [{"text": t} for t in texts], "truncate": "END", "top_n": len(texts)}
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["rankings"]:
                rank[d["index"]] = d["logit"]
        except Exception as e:
            logging.exception(f"NVIDIA rerank error: {e}")
        return rank, token_count


# ───────────────────────── OpenAI-compatible rerank ─────────────────────────

class OpenAIAPIRerank(Base):
    _FACTORY_NAME = "OpenAI-API-Compatible"
    def __init__(self, key, model_name, base_url):
        if base_url.find("/rerank") == -1:
            self.base_url = urljoin(base_url, "/rerank")
        else:
            self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        texts = [truncate(t, 500) for t in texts]
        data = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        token_count = sum(num_tokens_from_string(t) for t in texts)
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as e:
            logging.exception(f"OpenAI-API rerank error: {e}")
        return rank, token_count


# ───────────────────────── Cohere / VLLM rerank ─────────────────────────

class CohereRerank(Base):
    _FACTORY_NAME = ["Cohere", "VLLM"]
    def __init__(self, key, model_name, base_url=None):
        from cohere import Client
        self.client = Client(api_key=key, base_url=base_url)
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        token_count = num_tokens_from_string(query) + sum(num_tokens_from_string(t) for t in texts)
        res = self.client.rerank(model=self.model_name, query=query, documents=texts, top_n=len(texts), return_documents=False)
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res.results:
                rank[d.index] = d.relevance_score
        except Exception as e:
            logging.exception(f"Cohere rerank error: {e}")
        return rank, token_count


# ───────────────────────── SILICONFLOW rerank ─────────────────────────

class SILICONFLOWRerank(Base):
    _FACTORY_NAME = "SILICONFLOW"
    def __init__(self, key, model_name, base_url="https://api.siliconflow.cn/v1/rerank"):
        self.model_name = model_name
        self.base_url = base_url or "https://api.siliconflow.cn/v1/rerank"
        self.headers = {"accept": "application/json", "content-type": "application/json", "authorization": f"Bearer {key}"}

    def similarity(self, query: str, texts: list):
        payload = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts), "return_documents": False, "max_chunks_per_doc": 1024, "overlap_tokens": 80}
        response = requests.post(self.base_url, json=payload, headers=self.headers).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in response["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as e:
            logging.exception(f"SILICONFLOW rerank error: {e}")
        return rank, response["meta"]["tokens"]["input_tokens"] + response["meta"]["tokens"]["output_tokens"]


# ───────────────────────── QWen rerank ─────────────────────────

class QWenRerank(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"
    def __init__(self, key, model_name="gte-rerank", base_url=None, **kwargs):
        import dashscope
        self.api_key = key
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        from http import HTTPStatus
        import dashscope
        resp = dashscope.TextReRank.call(api_key=self.api_key, model=self.model_name, query=query, documents=texts, top_n=len(texts), return_documents=False)
        rank = np.zeros(len(texts), dtype=float)
        if resp.status_code == HTTPStatus.OK:
            try:
                for r in resp.output.results:
                    rank[r.index] = r.relevance_score
            except Exception as e:
                logging.exception(f"QWen rerank error: {e}")
            return rank, resp.usage.total_tokens
        else:
            raise ValueError(f"QWenRerank error: {resp.status_code} - {resp.text}")


# ───────────────────────── HuggingFace rerank ─────────────────────────

class HuggingFaceRerank(Base):
    _FACTORY_NAME = "HuggingFace"
    def __init__(self, key, model_name="BAAI/bge-reranker-v2-m3", base_url="http://127.0.0.1"):
        self.model_name = model_name.split("___")[0]
        self.base_url = base_url or "http://127.0.0.1"

    def similarity(self, query: str, texts: list):
        if not texts:
            return np.array([]), 0
        token_count = sum(num_tokens_from_string(t) for t in texts)
        batch_size = 8
        scores = [0.0] * len(texts)
        for i in range(0, len(texts), batch_size):
            res = requests.post(
                f"http://{self.base_url}/rerank",
                headers={"Content-Type": "application/json"},
                json={"query": query, "texts": texts[i:i+batch_size], "raw_scores": False, "truncate": True},
            )
            for o in res.json():
                scores[o["index"] + i] = o["score"]
        return np.array(scores), token_count


# ───────────────────────── GPUStack rerank ─────────────────────────

class GPUStackRerank(Base):
    _FACTORY_NAME = "GPUStack"
    def __init__(self, key, model_name, base_url):
        from yarl import URL
        self.model_name = model_name
        self.base_url = str(URL(base_url) / "v1" / "rerank")
        self.headers = {"accept": "application/json", "content-type": "application/json", "authorization": f"Bearer {key}"}

    def similarity(self, query: str, texts: list):
        payload = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        response = requests.post(self.base_url, json=payload, headers=self.headers)
        response.raise_for_status()
        response_json = response.json()
        rank = np.zeros(len(texts), dtype=float)
        token_count = sum(num_tokens_from_string(t) for t in texts)
        try:
            for result in response_json["results"]:
                rank[result["index"]] = result["relevance_score"]
        except Exception as e:
            logging.exception(f"GPUStack rerank error: {e}")
        return rank, token_count


# ───────────────────────── Novita / Gitee / 302.AI (Jina-compatible) ─────────────────────────

class NovitaRerank(JinaRerank):
    _FACTORY_NAME = "NovitaAI"
    def __init__(self, key, model_name, base_url="https://api.novita.ai/v3/openai/rerank"):
        super().__init__(key, model_name, base_url or "https://api.novita.ai/v3/openai/rerank")


class GiteeRerank(JinaRerank):
    _FACTORY_NAME = "GiteeAI"
    def __init__(self, key, model_name, base_url="https://ai.gitee.com/v1/rerank"):
        super().__init__(key, model_name, base_url or "https://ai.gitee.com/v1/rerank")


class BaiduYiyanRerank(Base):
    _FACTORY_NAME = "BaiduYiyan"
    def __init__(self, key, model_name, base_url=None):
        from qianfan.resources import Reranker
        key_dict = json.loads(key)
        self.client = Reranker(ak=key_dict.get("yiyan_ak"), sk=key_dict.get("yiyan_sk"))
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        res = self.client.do(model=self.model_name, query=query, documents=texts, top_n=len(texts)).body
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as e:
            logging.exception(f"BaiduYiyan rerank error: {e}")
        return rank, self.total_token_count(res)


class VoyageRerank(Base):
    _FACTORY_NAME = "Voyage AI"
    def __init__(self, key, model_name, base_url=None):
        import voyageai
        self.client = voyageai.Client(api_key=key)
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        rank = np.zeros(len(texts), dtype=float)
        if not texts:
            return rank, 0
        res = self.client.rerank(query=query, documents=texts, model=self.model_name, top_k=len(texts))
        try:
            for r in res.results:
                rank[r.index] = r.relevance_score
        except Exception as e:
            logging.exception(f"Voyage rerank error: {e}")
        return rank, res.total_tokens
