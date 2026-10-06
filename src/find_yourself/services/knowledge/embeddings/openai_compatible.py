"""OpenAI 兼容远程嵌入（补齐包2 · A-向量库-07①：无 key 自动禁用）。

环境变量（运行期直读，**不碰 config.py**——那是包1 的独占文件）：

* ``FIND_YOURSELF_EMBEDDING_API_KEY``（回退 ``OPENAI_API_KEY``）——缺失时
  ``is_available() is False``，registry / 检索层自动降级 hash 嵌入或词法路；
* ``FIND_YOURSELF_EMBEDDING_BASE_URL`` —— 默认 ``https://api.openai.com/v1``，
  可指向任意 OpenAI 兼容网关（vLLM / OneAPI / 网关中台等）；
* ``FIND_YOURSELF_EMBEDDING_MODEL`` —— 默认 ``text-embedding-3-small``；
* ``FIND_YOURSELF_EMBEDDING_DIM`` —— 建议配置：本地向量后端建表要固定维度；
  未配置时由 ``ensure_dim()`` 首次探测（发一条真实 embedding 请求）并缓存，
  探测失败返回 0 → 上层禁用该 provider。

实现走 ``httpx``（项目既有依赖，无新增依赖）。``transport`` 参数允许测试注入
``httpx.MockTransport``，不打真网。
"""

from __future__ import annotations

import os
from typing import Any

from .base import EmbeddingProvider, EmbeddingUnavailable

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_TIMEOUT_SECONDS = 15.0


class OpenAICompatibleEmbedding(EmbeddingProvider):
    """OpenAI ``POST {base_url}/embeddings`` 兼容 provider。"""

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Any | None = None,
    ):
        self.api_key = (
            api_key
            or os.getenv("FIND_YOURSELF_EMBEDDING_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        self.base_url = (
            base_url or os.getenv("FIND_YOURSELF_EMBEDDING_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("FIND_YOURSELF_EMBEDDING_MODEL") or DEFAULT_MODEL
        if dim is None:
            raw = os.getenv("FIND_YOURSELF_EMBEDDING_DIM", "")
            try:
                dim = int(raw) if raw else None
            except ValueError:
                dim = None
        self.dim = int(dim or 0)
        self.timeout = float(timeout)
        self._transport = transport  # 测试注入点（httpx.MockTransport）

    def is_available(self) -> bool:
        """有 key 即视为可用（真正打网失败由调用方按 EmbeddingUnavailable 降级）。"""
        return bool(self.api_key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.is_available():
            raise EmbeddingUnavailable(
                "openai embedding disabled: FIND_YOURSELF_EMBEDDING_API_KEY not set"
            )
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": [str(t) for t in texts]},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        if ordered and not self.dim:
            self.dim = len(ordered[0].get("embedding") or [])
        return [[float(x) for x in d.get("embedding") or []] for d in ordered]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def ensure_dim(self) -> int:
        if self.dim:
            return self.dim
        if not self.is_available():
            return 0
        try:
            self.embed_query("dim probe")
        except Exception:  # noqa: BLE001 — 探测失败=不可用，绝不抛出主路径
            return 0
        return self.dim
