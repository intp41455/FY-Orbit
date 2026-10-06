"""向量后端注册表（补齐包2 · A-向量库-01：注册表可查已接入后端）。

已接入：

* ``sqlite_vec`` —— 内嵌默认后端（补齐包2 · A-向量库-02，实装）
* ``lancedb`` —— 嵌入式列存（P10 · A-向量库-03）
* ``chroma`` —— 本地持久化 / 服务模式（P10 · A-向量库-04）
* ``faiss`` —— 进程内索引文件（P10 · A-向量库-05）
* ``qdrant`` —— 本地嵌入式 / 远端服务（P10 · A-向量库-06）

**P10 注册（A-向量库-03~06）**：接口就绪后按原设计「落一个类 +
``register_vector_store`` 即接入，检索上层零改动」实装——四个后端各自独立
模块，缺失依赖时模块仍可导入、``is_available()`` 报 False、检索层自动降级，
**不改变 ``create_vector_store`` 与 ``vector_backend_status`` 的签名**。

构造约定：注册表把 ``create_vector_store(name, **kwargs)`` 的 kwargs 原样透传
给后端构造器；调用方（search.py 混合检索）只保证 ``session`` 与 ``dim`` 两个
语义位。环境变量 ``FIND_YOURSELF_KB_VECTOR_BACKEND`` 可切换默认后端。

构造参数差异（kwargs 透传，各后端自解释）：

* ``sqlite_vec``：``session``（必需）、``dim``、``table_name``、``distance_metric``
* ``lancedb``：``dim``、``uri``、``table_name``、``distance_metric``
* ``chroma``：``dim``、``path`` 或 ``host``/``port``、``collection_prefix``、``space``
* ``faiss``：``dim``、``index_dir``、``metric``（``ip``|``l2``）
* ``qdrant``：``dim``、``path`` 或 ``url``/``api_key``、``collection_name``、``distance``
"""

from __future__ import annotations

import os
from typing import Any, Type

from .base import VectorStore
from .chroma_backend import HAS_CHROMADB, ChromaStore
from .faiss_backend import HAS_FAISS, FaissStore
from .lancedb_backend import HAS_LANCEDB, LanceDBStore
from .qdrant_backend import HAS_QDRANT, QdrantStore
from .sqlite_vec_backend import HAS_SQLITE_VEC, SqliteVecStore

#: 内嵌默认后端
DEFAULT_VECTOR_BACKEND = "sqlite_vec"
#: 环境变量：按名选择向量后端
VECTOR_BACKEND_ENV = "FIND_YOURSELF_KB_VECTOR_BACKEND"

#: 可选后端的依赖可用性（运维/诊断用，也供测试 skipif 引用）
OPTIONAL_BACKEND_DEPS: dict[str, bool] = {
    "lancedb": HAS_LANCEDB,
    "chroma": HAS_CHROMADB,
    "faiss": HAS_FAISS,
    "qdrant": HAS_QDRANT,
}

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
    """注册表状态（运维/诊断用）：类名 + 定义模块 + 内置后端附加信息。

    ``dependency_installed``：依赖包是否可用（False ⇒ 该后端会降级，不影响其他）。
    """
    status: dict[str, dict[str, Any]] = {}
    for name, store_cls in sorted(_REGISTRY.items()):
        entry: dict[str, Any] = {
            "class": store_cls.__name__,
            "module": store_cls.__module__,
            "dependency_installed": (
                HAS_SQLITE_VEC
                if name == DEFAULT_VECTOR_BACKEND
                else OPTIONAL_BACKEND_DEPS.get(name, True)
            ),
        }
        if name == DEFAULT_VECTOR_BACKEND:
            entry["extension_installed"] = HAS_SQLITE_VEC
            entry["default"] = True
        status[name] = entry
    return status


# 内置注册（import 即生效）
register_vector_store("sqlite_vec", SqliteVecStore, override=True)
register_vector_store("lancedb", LanceDBStore, override=True)
register_vector_store("chroma", ChromaStore, override=True)
register_vector_store("faiss", FaissStore, override=True)
register_vector_store("qdrant", QdrantStore, override=True)

