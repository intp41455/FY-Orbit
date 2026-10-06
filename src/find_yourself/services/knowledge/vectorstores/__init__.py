"""向量后端可插拔层（补齐包2 · A-向量库-01/02）。

公开面：

* :class:`.base.VectorStore` / :class:`.base.VectorRecord` /
  :class:`.base.VectorHit` —— 引擎无关契约（owner 隔离在后端内部强制）
* :class:`.sqlite_vec_backend.SqliteVecStore` —— SQLite+FTS5+sqlite-vec 内嵌
  默认后端（vec0 虚表运行时建表，不经 alembic）
* :mod:`.registry` —— ``register_vector_store`` / ``create_vector_store`` /
  ``list_vector_stores`` / ``vector_backend_status``（03~06 LanceDB/Chroma/
  FAISS/Qdrant 为 P2 注册位，见 registry docstring 注册示例）
"""

from .base import (
    VectorFilterUnsupported,
    VectorHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
)
from .registry import (
    DEFAULT_VECTOR_BACKEND,
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
    "DEFAULT_VECTOR_BACKEND",
    "VECTOR_BACKEND_ENV",
    "create_vector_store",
    "get_vector_store_class",
    "list_vector_stores",
    "register_vector_store",
    "vector_backend_status",
]
