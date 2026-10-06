"""向量后端可插拔层（补齐包2 · A-向量库-01/02；P10 · A-向量库-03~06）。

公开面：

* :class:`.base.VectorStore` / :class:`.base.VectorRecord` /
  :class:`.base.VectorHit` —— 引擎无关契约（owner 隔离在后端内部强制）
* :class:`.sqlite_vec_backend.SqliteVecStore` —— SQLite+FTS5+sqlite-vec 内嵌
  默认后端（vec0 虚表运行时建表，不经 alembic）
* :class:`.lancedb_backend.LanceDBStore` —— 嵌入式列存（P10；零服务）
* :class:`.chroma_backend.ChromaStore` —— 本地持久化 / 服务模式（P10）
* :class:`.faiss_backend.FaissStore` —— 进程内索引文件（P10；自管 id 映射）
* :class:`.qdrant_backend.QdrantStore` —— 本地嵌入式 / 远端服务（P10）
* :mod:`.registry` —— ``register_vector_store`` / ``create_vector_store`` /
  ``list_vector_stores`` / ``vector_backend_status``

可选后端（lancedb / chroma / faiss / qdrant）的依赖缺失时，模块仍可导入、
``is_available()`` 报 False、检索层自动降级——**不引入硬依赖**。
"""

from .base import (
    VectorFilterUnsupported,
    VectorHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
)
from .chroma_backend import HAS_CHROMADB, ChromaStore
from .faiss_backend import HAS_FAISS, FaissStore
from .lancedb_backend import HAS_LANCEDB, LanceDBStore
from .qdrant_backend import HAS_QDRANT, QdrantStore
from .registry import (
    DEFAULT_VECTOR_BACKEND,
    OPTIONAL_BACKEND_DEPS,
    VECTOR_BACKEND_ENV,
    create_vector_store,
    get_vector_store_class,
    list_vector_stores,
    register_vector_store,
    vector_backend_status,
)
from .sqlite_vec_backend import HAS_SQLITE_VEC, SqliteVecStore

__all__ = [
    "VectorStore",
    "VectorRecord",
    "VectorHit",
    "VectorStoreError",
    "VectorFilterUnsupported",
    "SqliteVecStore",
    "HAS_SQLITE_VEC",
    "LanceDBStore",
    "HAS_LANCEDB",
    "ChromaStore",
    "HAS_CHROMADB",
    "FaissStore",
    "HAS_FAISS",
    "QdrantStore",
    "HAS_QDRANT",
    "OPTIONAL_BACKEND_DEPS",
    "DEFAULT_VECTOR_BACKEND",
    "VECTOR_BACKEND_ENV",
    "create_vector_store",
    "get_vector_store_class",
    "list_vector_stores",
    "register_vector_store",
    "vector_backend_status",
]
