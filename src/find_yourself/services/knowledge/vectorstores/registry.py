"""向量后端注册表（补齐包2 · A-向量库-01：注册表可查已接入后端）。

已接入：``sqlite_vec``（内嵌默认后端，实装）。

**P2 注册位（A-向量库-03~06，不随包2 实装）**：接口已就绪，落一个类 +
``register_vector_store`` 即接入，检索上层零改动::

    # LanceDB（本地文件列存）
    from lancedb import connect
    class LanceDBStore(VectorStore):
        name = "lancedb"
        def __init__(self, uri: str, *, dim: int, table_name: str = "kb_chunks"):
            self._db = connect(uri); self.dim = dim; self.table_name = table_name
        ...
    register_vector_store("lancedb", LanceDBStore)

    # Chroma（本地持久化服务）  register_vector_store("chroma", ChromaStore)
    # FAISS（进程内索引文件）    register_vector_store("faiss", FaissStore)
    # Qdrant（独立向量服务）     register_vector_store("qdrant", QdrantStore)

构造约定：注册表把 ``create_vector_store(name, **kwargs)`` 的 kwargs 原样透传
给后端构造器；调用方（search.py 混合检索）只保证 ``session`` 与 ``dim`` 两个
语义位。环境变量 ``FIND_YOURSELF_KB_VECTOR_BACKEND`` 可切换默认后端。
"""

from __future__ import annotations

import os
from typing import Any, Type

from .base import VectorStore
from .sqlite_vec_backend import HAS_SQLITE_VEC, SqliteVecStore

#: 内嵌默认后端
DEFAULT_VECTOR_BACKEND = "sqlite_vec"
#: 环境变量：按名选择向量后端
VECTOR_BACKEND_ENV = "FIND_YOURSELF_KB_VECTOR_BACKEND"

_REGISTRY: dict[str, Type[VectorStore]] = {}


def register_vector_store(
    name: str, store_cls: Type[VectorStore], *, override: bool = False
) -> None:
    """注册后端类。同名且未 ``override=True`` 时抛错（防并行包误覆盖）。"""
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("vector store name must be non-empty")
    if key in _REGISTRY and not override:
        raise ValueError(f"vector store '{key}' already registered; pass override=True")
    _REGISTRY[key] = store_cls


def get_vector_store_class(name: str) -> Type[VectorStore]:
    key = (name or "").strip().lower()
    store_cls = _REGISTRY.get(key)
    if store_cls is None:
        raise KeyError(f"unknown vector store '{name}'; known: {sorted(_REGISTRY)}")
    return store_cls


def list_vector_stores() -> list[str]:
    """已注册后端名单（注册表可查）。"""
    return sorted(_REGISTRY)


def create_vector_store(name: str | None = None, **kwargs: Any) -> VectorStore:
    """按名构建后端；缺省读 ``FIND_YOURSELF_KB_VECTOR_BACKEND``，再缺省 sqlite_vec。"""
    chosen = (name or os.getenv(VECTOR_BACKEND_ENV) or DEFAULT_VECTOR_BACKEND).strip().lower()
    return get_vector_store_class(chosen)(**kwargs)


def vector_backend_status() -> dict[str, dict[str, Any]]:
    """注册表状态（运维/诊断用）：类名 + 定义模块 + 内置后端附加信息。"""
    status: dict[str, dict[str, Any]] = {}
    for name, store_cls in sorted(_REGISTRY.items()):
        entry: dict[str, Any] = {
            "class": store_cls.__name__,
            "module": store_cls.__module__,
        }
        if name == DEFAULT_VECTOR_BACKEND:
            entry["extension_installed"] = HAS_SQLITE_VEC
            entry["default"] = True
        status[name] = entry
    return status


# 内置注册（import 即生效）
register_vector_store("sqlite_vec", SqliteVecStore, override=True)
