"""SQLite + sqlite-vec 内嵌向量后端（补齐包2 · A-向量库-02 · 默认后端）。

* ``kb_chunks_vec`` 是**运行时 CREATE VIRTUAL TABLE** 的 vec0 虚表——与
  ``search.py`` 的 FTS5 影子表同款惰性建表哲学：**不经 alembic、不碰
  ``db/models.py``**，单机零服务。
* ``owner_id`` 声明为 vec0 PARTITION KEY：KNN 天然按 owner 分区，跨 owner 的
  向量行**物理上不进候选**（不是查询后过滤，是查询时分区剪枝）。
* ``doc_id`` 为 metadata 列：KNN 内联 ``doc_id IN (...)`` 过滤（sqlite-vec
  0.1.9 已验证支持）。
* 维度在建表时固定；已存在表维度与请求不符时 **DROP 后重建**——向量表是可
  再生的派生索引（同 FTS5 影子表），重建后由 ``search.ensure_vector_index``
  全量回填，不丢任何源数据。
* sqlite-vec 包缺失 / 扩展加载失败 → ``is_available() is False``，检索层自动
  降级词法路；**降级不抛异常，参数错误才抛**。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from sqlalchemy import event, text as sql_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ....services.errors import ValidationFailed
from .base import (
    VectorFilterUnsupported,
    VectorHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
)

try:  # sqlite-vec 缺失不致命：本模块仍可导入，is_available() 会报告 False
    import sqlite_vec
    from sqlite_vec import serialize_float32

    HAS_SQLITE_VEC = True
except ImportError:  # pragma: no cover - 只有在无依赖环境才会走到
    sqlite_vec = None  # type: ignore[assignment]
    serialize_float32 = None  # type: ignore[assignment]
    HAS_SQLITE_VEC = False

#: vec0 虚表名（shadow index，运行时创建）
DEFAULT_TABLE = "kb_chunks_vec"
DEFAULT_DISTANCE_METRIC = "cosine"
MAX_QUERY_TOP_K = 200

_DIM_RE = re.compile(r"embedding\s+FLOAT\[(\d+)\]", re.IGNORECASE)

_extension_loader_installed = False


def _install_extension_loader() -> None:
    """每个新建 sqlite3 连接自动加载 sqlite-vec 扩展（幂等，失败静默）。

    全局 ``Engine`` 级 connect 事件 + isinstance 守卫：Postgres/其他方言连接
    不受影响；加载失败的连接按「vec 不可用」降级，不影响非向量功能。
    """
    global _extension_loader_installed
    if _extension_loader_installed or not HAS_SQLITE_VEC:
        return

    @event.listens_for(Engine, "connect")
    def _load_sqlite_vec(dbapi_connection: Any, _record: Any) -> None:  # noqa: ANN001
        try:
            import sqlite3

            if not isinstance(dbapi_connection, sqlite3.Connection):
                return
            dbapi_connection.enable_load_extension(True)
            sqlite_vec.load(dbapi_connection)
            dbapi_connection.enable_load_extension(False)
        except Exception:  # noqa: BLE001 — 扩展加载失败 = 该连接 vec 不可用
            pass

    _extension_loader_installed = True


class SqliteVecStore(VectorStore):
    """vec0 虚表后端。构造即绑定一个 SQLAlchemy ``Session``（与 kb 同库同事务）。"""

    name = "sqlite_vec"

    def __init__(
        self,
        session: Session,
        *,
        dim: int,
        table_name: str = DEFAULT_TABLE,
        distance_metric: str = DEFAULT_DISTANCE_METRIC,
    ):
        if int(dim) <= 0:
            raise ValueError(f"vector dim must be positive, got {dim}")
        self.s = session
        self.dim = int(dim)
        self.table_name = table_name
        self.distance_metric = distance_metric
        self._available: bool | None = None
        _install_extension_loader()

    # -- availability -------------------------------------------------------- #
    def is_available(self) -> bool:
        if not HAS_SQLITE_VEC:
            return False
        if self.s.get_bind().dialect.name != "sqlite":
            return False
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            self.s.execute(sql_text("SELECT vec_version()")).scalar()
            return True
        except Exception:  # noqa: BLE001 — 连接建立早于扩展加载器时的补载路径
            raw = self._raw_dbapi_connection()
            if raw is not None and HAS_SQLITE_VEC:
                try:
                    raw.enable_load_extension(True)
                    sqlite_vec.load(raw)
                    raw.enable_load_extension(False)
                except Exception:  # noqa: BLE001
                    return False
                try:
                    self.s.execute(sql_text("SELECT vec_version()")).scalar()
                    return True
                except Exception:  # noqa: BLE001
                    return False
            return False

    def _raw_dbapi_connection(self) -> Any | None:
        try:
            return getattr(self.s.connection().connection, "dbapi_connection", None)
        except Exception:  # noqa: BLE001
            return None

    # -- table lifecycle ----------------------------------------------------- #
    def _declared_dim(self) -> int | None:
        row = self.s.execute(
            sql_text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"
            ),
            {"t": self.table_name},
        ).scalar()
        if row is None:
            return None
        match = _DIM_RE.search(row)
        return int(match.group(1)) if match else -1  # -1：存在但不可识别

    def _ensure_table(self) -> bool:
        """建表（或维度漂移时重建）。False ⇒ 后端不可用，调用方降级。"""
        if not self.is_available():
            return False
        current = self._declared_dim()
        if current == self.dim:
            return True
        try:
            with self.s.begin_nested():
                if current is not None:  # 维度漂移 → 派生索引重建
                    self.s.execute(sql_text(f"DROP TABLE {self.table_name}"))
                self.s.execute(
                    sql_text(
                        f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.table_name} USING vec0("
                        "owner_id TEXT, chunk_id TEXT, doc_id TEXT, "
                        f"embedding FLOAT[{self.dim}] distance_metric={self.distance_metric})"
                    )
                )
            return True
        except Exception:  # noqa: BLE001 — SQLite 无 vec0 支持 → 降级
            self._available = False
            return False

    def _table_ok(self) -> bool:
        """只读路径用：表存在且维度匹配才查（不副作用建表/重建）。"""
        if not self.is_available():
            return False
        return self._declared_dim() == self.dim

    # -- write ---------------------------------------------------------------- #
    def add(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        material = [r for r in records]
        if not material:
            return 0
        if not owner_id:
            raise ValidationFailed("owner_required", "vector store add requires an owner_id")
        for r in material:
            if len(r.embedding) != self.dim:
                raise ValidationFailed(
                    "vector_dim_mismatch",
                    f"chunk {r.chunk_id}: embedding dim {len(r.embedding)} != table dim {self.dim}",
                )
        if not self._ensure_table():
            return 0  # 后端不可用 → 降级（is_available() 是权威信号）
        rows = [
            {
                "oid": owner_id,
                "cid": r.chunk_id,
                "did": r.doc_id,
                "emb": serialize_float32([float(x) for x in r.embedding]),
            }
            for r in material
        ]
        try:
            with self.s.begin_nested():
                self.s.execute(
                    sql_text(
                        f"INSERT INTO {self.table_name} (owner_id, chunk_id, doc_id, embedding) "
                        "VALUES (:oid, :cid, :did, :emb)"
                    ),
                    rows,
                )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"sqlite-vec insert failed: {exc}") from exc
        return len(rows)

    def delete(self, chunk_ids: Iterable[str], *, owner_id: str) -> int:
        ids = [c for c in chunk_ids if c]
        if not ids or not owner_id:
            return 0
        if not self._table_ok():
            return 0
        placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
        params: dict[str, Any] = {"oid": owner_id}
        params.update({f"id{i}": cid for i, cid in enumerate(ids)})
        try:
            with self.s.begin_nested():
                res = self.s.execute(
                    sql_text(
                        f"DELETE FROM {self.table_name} "
                        f"WHERE owner_id = :oid AND chunk_id IN ({placeholders})"
                    ),
                    params,
                )
                deleted = int(res.rowcount or 0)
            return max(deleted, 0)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"sqlite-vec delete failed: {exc}") from exc

    # -- read ------------------------------------------------------------------ #
    def query(
        self,
        embedding: Sequence[float],
        *,
        owner_id: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        if not owner_id:
            raise ValidationFailed("owner_required", "vector store query requires an owner_id")
        if len(embedding) != self.dim:
            raise ValidationFailed(
                "vector_dim_mismatch",
                f"query embedding dim {len(embedding)} != table dim {self.dim}",
            )
        params: dict[str, Any] = {
            "oid": owner_id,
            "emb": serialize_float32([float(x) for x in embedding]),
            "lim": max(1, min(int(top_k), MAX_QUERY_TOP_K)),
        }
        sql = (
            f"SELECT chunk_id, doc_id, distance FROM {self.table_name} "
            "WHERE owner_id = :oid AND embedding MATCH :emb"
        )
        if filters:
            # 参数校验先于一切可用性检查：非法过滤是调用方 bug，必须响亮报错
            unknown = set(filters) - {"doc_ids"}
            if unknown:
                raise VectorFilterUnsupported(
                    f"unsupported metadata filters: {sorted(unknown)}"
                )
            doc_ids = [d for d in (filters.get("doc_ids") or []) if d]
            if doc_ids:
                placeholders = ", ".join(f":doc{i}" for i in range(len(doc_ids)))
                sql += f" AND doc_id IN ({placeholders})"
                params.update({f"doc{i}": d for i, d in enumerate(doc_ids)})
        if not self._table_ok():
            return []
        sql += " ORDER BY distance LIMIT :lim"
        try:
            rows = self.s.execute(sql_text(sql), params).all()
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"sqlite-vec query failed: {exc}") from exc
        hits: list[VectorHit] = []
        for r in rows:
            distance = r[2]
            if distance is None:
                # cosine 距离对零向量返回 NULL：无方向即无证据，不进候选
                continue
            hits.append(
                VectorHit(
                    chunk_id=str(r[0]),
                    doc_id=str(r[1] or ""),
                    score=max(0.0, min(1.0, 1.0 - float(distance))),
                    distance=float(distance),
                    metadata={},
                )
            )
        return hits

    def count(self, *, owner_id: str | None = None) -> int:
        if not self._table_ok():
            return 0
        if owner_id:
            row = self.s.execute(
                sql_text(f"SELECT count(*) FROM {self.table_name} WHERE owner_id = :o"),
                {"o": owner_id},
            ).scalar()
        else:
            row = self.s.execute(
                sql_text(f"SELECT count(*) FROM {self.table_name}")
            ).scalar()
        return int(row or 0)

    def list_chunk_ids(self, *, owner_id: str) -> set[str]:
        if not owner_id or not self._table_ok():
            return set()
        rows = self.s.execute(
            sql_text(
                f"SELECT chunk_id FROM {self.table_name} WHERE owner_id = :o"
            ),
            {"o": owner_id},
        ).all()
        return {str(r[0]) for r in rows}
