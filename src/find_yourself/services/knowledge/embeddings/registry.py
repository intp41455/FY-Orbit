"""Embedding 注册表（补齐包2 · A-向量库-07①：Embedding 可插拔）。

已内置：``hash``（离线确定性，默认）、``openai``（远程，无 key 自动禁用）。

注册新 provider 两行接入::

    from find_yourself.services.knowledge.embeddings import register_embedding

    register_embedding("bge-local", lambda: BgeLocalEmbedding())
    # 之后 FIND_YOURSELF_KB_EMBEDDING=bge-local 即全局生效（或
    # resolve_default_embedding(preferred="bge-local") 按调用点指定）。

``resolve_default_embedding`` 的降级链（对应「无 key 自动禁用」验收）：
环境变量指定的 provider 不可用（无 key / 探测维度失败）→ 回退 ``hash``。
"""

from __future__ import annotations

import os
from typing import Callable

from .base import EmbeddingProvider
from .hash_embedding import HashingEmbedding
from .openai_compatible import OpenAICompatibleEmbedding

#: 全局默认嵌入（离线零依赖，测试与无外部服务的部署都用它）
DEFAULT_EMBEDDING = "hash"
#: 环境变量：按名选择 embedding provider
EMBEDDING_ENV = "FIND_YOURSELF_KB_EMBEDDING"

_FACTORY_REGISTRY: dict[str, Callable[[], EmbeddingProvider]] = {}


def register_embedding(
    name: str, factory: Callable[[], EmbeddingProvider], *, override: bool = False
) -> None:
    """注册 provider 工厂。同名且未 ``override=True`` 时抛错（防并行包误覆盖）。"""
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("embedding provider name must be non-empty")
    if key in _FACTORY_REGISTRY and not override:
        raise ValueError(f"embedding provider '{key}' already registered; pass override=True")
    _FACTORY_REGISTRY[key] = factory


def get_embedding(name: str) -> EmbeddingProvider:
    """按名构建一个 provider 实例（每次新建，无全局单例状态）。"""
    key = (name or "").strip().lower()
    factory = _FACTORY_REGISTRY.get(key)
    if factory is None:
        raise KeyError(f"unknown embedding provider '{name}'; known: {sorted(_FACTORY_REGISTRY)}")
    return factory()


def list_embeddings() -> list[str]:
    """已注册 provider 名单（注册表可查）。"""
    return sorted(_FACTORY_REGISTRY)


def create_embedding(name: str | None = None) -> EmbeddingProvider:
    """按名构建；缺省读 ``FIND_YOURSELF_KB_EMBEDDING``，再缺省 ``hash``。"""
    chosen = (name or os.getenv(EMBEDDING_ENV) or DEFAULT_EMBEDDING).strip().lower()
    return get_embedding(chosen)


def resolve_default_embedding(preferred: str | None = None) -> EmbeddingProvider:
    """环境选择 → 不可用（无 key / 维度探测失败）→ ``hash`` 兜底。

    返回值保证 ``is_available() and dim > 0``——可直接用于向量召回与建表。
    """
    choice = (preferred or os.getenv(EMBEDDING_ENV) or "").strip().lower()
    if choice and choice != DEFAULT_EMBEDDING:
        try:
            provider = create_embedding(choice)
        except Exception:  # noqa: BLE001 — 未知名/构建失败都走兜底
            provider = None
        if (
            provider is not None
            and provider.is_available()
            and provider.ensure_dim() > 0
        ):
            return provider
    return create_embedding(DEFAULT_EMBEDDING)


# 内置注册（import 即生效；override 允许测试替换）
register_embedding("hash", HashingEmbedding, override=True)
register_embedding("openai", OpenAICompatibleEmbedding, override=True)
