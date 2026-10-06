"""LanceDB 嵌入式列存向量后端（P10 · A-向量库-03）。

* **零服务**：LanceDB 是嵌入式列存（Lance 格式），``connect(uri)`` 直接落本地
  目录；无守护进程、无端口、无 Docker——与仓内「单机零服务」装机哲学一致。
* ``owner_id`` 在 LanceDB 里是**过滤列**（不是 sqlite-vec 那样的分区键）：
  LanceDB 的 ``where`` 走标量索引预过滤，再在候选集上做向量近邻。语义上仍是
  「授权谓词先于排序」，与 ``base.VectorStore`` 契约一致。
* **写接口幂等**：``upsert`` 覆写 base 默认的「先删后写」，改用 LanceDB 原生
  ``merge_insert``（``on="chunk_id"``）——同 ``chunk_id`` 重复写不产生重复行，
  且是单次原子操作，不必承受「删成功但写失败」的中间态。
* **过滤键**：``doc_ids`` 映射为 ``where doc_id IN (...)``；其他键抛
  :class:`VectorFilterUnsupported`（宁可报错也不静默忽略）。
* 维度漂移：LanceDB 表的向量维度在建表时固定。已存在表维度与请求不符 →
  **DROP 后重建**——向量表是可再生的派生索引，源数据在 kb 主库，不丢数据。
* lancedb 包缺失 / 连接失败 → ``is_available() is False``，读接口返回空、
  写接口返回 0，**不抛异常**（降级是常态路径）；但调用方传参错误
  （维度不符、非法 filter）仍抛异常，那是 bug 不是降级。

分数换算：LanceDB 的 ``_distance`` 为 **L2 距离**（默认 ``l2`` metric）；
``score`` 统一换算为 ``[0,1]`` 相似度，与 base 契约一致。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from ....services.errors import ValidationFailed
from .base import (
    VectorFilterUnsupported,
    VectorHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
)

try:  # lancedb 缺失不致命：本模块仍可导入，is_available() 会报告 False
    import lancedb  # type: ignore[import-not-found]

    HAS_LANCEDB = True
except ImportError:  # pragma: no cover - 只有在无依赖环境才会走到
    lancedb = None  # type: ignore[assignment]
    HAS_LANCEDB = False

try:  # 维度推断需要 numpy，但 numpy 是 lancedb 的传递依赖，缺失时按不可用处理
    import numpy as np

    HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False


#: 默认表名。**必须带后端身份前缀**——LanceDB 的表与 SQLite 的 vec0 虚表虽在
#: 不同存储介质，但部署方可能把 LanceDB 的 uri 指到与 kb 主库同目录，届时
#: 裸 ``kb_chunks_vec`` 会与 sqlite-vec 的虚表**同名**，运维对账时无法区分
#: 两个后端的表归属。带前缀后跨后端同库也不撞名。
DEFAULT_TABLE = "lancedb_kb_chunks_vec"
DEFAULT_DISTANCE_METRIC = "l2"
MAX_QUERY_TOP_K = 200

#: 只支持这一个元数据过滤键，与 base 契约一致
_SUPPORTED_FILTERS = frozenset({"doc_ids"})

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    """表名合法化：LanceDB 的 SQL-ish 接口不接受任意字符串做表名。"""
    if not _TABLE_NAME_RE.match(name or ""):
        raise ValidationFailed(
            "invalid_table_name",
            f"lancedb table name must match {_TABLE_NAME_RE.pattern}, got {name!r}",
        )
    return name


def _quote_literal(value: str) -> str:
    """SQL 字面量转义：单引号翻倍。防止 doc_id 注入 where 子句。"""
    return "'" + str(value).replace("'", "''") + "'"


class LanceDBStore(VectorStore):
    """LanceDB 嵌入式列存后端。

    构造约定（与 registry 透传规则一致）：``create_vector_store("lancedb",
    uri=..., dim=...)`` 的 kwargs 原样进本构造器。
    """

    name = "lancedb"

    def __init__(
        self,
        *,
        dim: int,
        uri: str = ".find_yourself/vectordb",
        table_name: str = DEFAULT_TABLE,
        distance_metric: str = DEFAULT_DISTANCE_METRIC,
        session: Any = None,
        **kwargs: Any,
    ):
        if int(dim) <= 0:
            raise ValueError(f"vector dim must be positive, got {dim}")
        self.dim = int(dim)
        self.uri = uri
        self.table_name = _quote_ident(table_name)
        self.distance_metric = distance_metric
        #: 额外连接参数原样透传给 lancedb.connect（如 storage_options）
        self._connect_kwargs = kwargs
        self._db: Any = None
        self._available: bool | None = None
        # ``session`` 是 search.py 的通用调用签名（所有后端都收到它），但本后端
        # 自带独立存储、不共享 SQLAlchemy 事务。显式接收并忽略，而不是靠
        # ``**kwargs`` 静默吞掉——静默吞掉会让「以为传了 session」的调用方误判。
        self.session = session

    # -- availability -------------------------------------------------------- #
    def is_available(self) -> bool:
        if not HAS_LANCEDB or self._available is False:
            return False
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            self._db = lancedb.connect(self.uri, **self._connect_kwargs)
            return True
        except Exception:  # noqa: BLE001 — 目录不可写 / 缺依赖 → 降级
            return False

    def _db_or_none(self) -> Any:
        if not self.is_available():
            return None
        return self._db

    # -- table lifecycle ----------------------------------------------------- #
    def _table_names(self) -> list[str]:
        db = self._db_or_none()
        if db is None:
            return []
        try:
            names = db.table_names()
        except Exception:  # noqa: BLE001
            return []
        # lancedb 新版本返回 list[str]；老版本 / 异步封装可能返回 (names, ...)
        if isinstance(names, tuple):
            names = names[0]
        try:
            return [str(n) for n in names]
        except TypeError:  # pragma: no cover
            return []

    def _declared_dim(self) -> int | None:
        """读表 schema 推断向量维度。表不存在 → None；读到但无向量列 → -1。"""
        db = self._db_or_none()
        if db is None or self.table_name not in self._table_names():
            return None
        try:
            tbl = db.open_table(self.table_name)
            schema = tbl.schema
            field = schema.field("vector")
            # pyarrow ListType(float32) → list_size
            return int(field.type.list_size)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — 存在但结构不可识别
            return -1

    def _ensure_table(self, sample_embedding: Sequence[float]) -> bool:
        """建表（或维度漂移时重建）。False ⇒ 后端不可用，调用方降级。"""
        db = self._db_or_none()
        if db is None:
            return False
        current = self._declared_dim()
        if current == self.dim:
            return True
        try:
            if current is not None:  # 维度漂移 → 派生索引重建
                db.drop_table(self.table_name)
            db.create_table(self.table_name, data=[self._row_template(sample_embedding)])
            # 建表后立刻清空刚插入的模板行：只借 schema，不留数据
            self._open().delete(f"chunk_id = {_quote_literal('__schema_probe__')}")
            return True
        except Exception:  # noqa: BLE001 — LanceDB 建表失败 → 降级
            self._available = False
            return False

    def _row_template(self, embedding: Sequence[float]) -> dict[str, Any]:
        return {
            "chunk_id": "__schema_probe__",
            "doc_id": "__schema_probe__",
            "owner_id": "__schema_probe__",
            "vector": [float(x) for x in embedding],
            "metadata_json": "{}",
        }

    def _table_ok(self) -> bool:
        """只读路径用：表存在且维度匹配才查（不副作用建表/重建）。"""
        if not self.is_available():
            return False
        return self._declared_dim() == self.dim

    def _open(self) -> Any:
        return self._db_or_none().open_table(self.table_name)

    # -- write ---------------------------------------------------------------- #
    def add(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        material = list(records)
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
        if not self._ensure_table(material[0].embedding):
            return 0  # 后端不可用 → 降级（is_available() 是权威信号）
        rows = [
            {
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "owner_id": owner_id,
                "vector": [float(x) for x in r.embedding],
                "metadata_json": _dump_metadata(r.metadata),
            }
            for r in material
        ]
        try:
            self._open().add(rows)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"lancedb add failed: {exc}") from exc
        return len(rows)

    def upsert(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        """原生 merge_insert 幂等写入（覆写 base 的「先删后写」默认实现）。"""
        material = list(records)
        if not material:
            return 0
        if not owner_id:
            raise ValidationFailed("owner_required", "vector store upsert requires an owner_id")
        for r in material:
            if len(r.embedding) != self.dim:
                raise ValidationFailed(
                    "vector_dim_mismatch",
                    f"chunk {r.chunk_id}: embedding dim {len(r.embedding)} != table dim {self.dim}",
                )
        if not self._ensure_table(material[0].embedding):
            return 0
        rows = [
            {
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "owner_id": owner_id,
                "vector": [float(x) for x in r.embedding],
                "metadata_json": _dump_metadata(r.metadata),
            }
            for r in material
        ]
        tbl = self._open()
        try:
            # 优先原生 merge_insert（单次原子）；老版本无此 API 时退化为 delete+add
            merge = getattr(tbl, "merge_insert", None)
            if merge is not None:
                (
                    merge("chunk_id")
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(rows)
                )
            else:  # pragma: no cover - 仅兼容老版本 lancedb
                tbl.delete(
                    "owner_id = "
                    + _quote_literal(owner_id)
                    + " AND chunk_id IN ("
                    + ", ".join(_quote_literal(r.chunk_id) for r in material)
                    + ")"
                )
                tbl.add(rows)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"lancedb upsert failed: {exc}") from exc
        return len(rows)

    def delete(self, chunk_ids: Iterable[str], *, owner_id: str) -> int:
        ids = [c for c in chunk_ids if c]
        if not ids or not owner_id:
            return 0
        if not self._table_ok():
            return 0
        before = self.count(owner_id=owner_id)
        predicate = (
            "owner_id = "
            + _quote_literal(owner_id)
            + " AND chunk_id IN ("
            + ", ".join(_quote_literal(c) for c in ids)
            + ")"
        )
        try:
            self._open().delete(predicate)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"lancedb delete failed: {exc}") from exc
        after = self.count(owner_id=owner_id)
        return max(before - after, 0)

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
        # 参数校验先于一切可用性检查：非法过滤是调用方 bug，必须响亮报错
        where = f"owner_id = {_quote_literal(owner_id)}"
        if filters:
            unknown = set(filters) - _SUPPORTED_FILTERS
            if unknown:
                raise VectorFilterUnsupported(
                    f"unsupported metadata filters: {sorted(unknown)}"
                )
            doc_ids = [d for d in (filters.get("doc_ids") or []) if d]
            if doc_ids:
                where += " AND doc_id IN (" + ", ".join(_quote_literal(d) for d in doc_ids) + ")"
        if not self._table_ok():
            return []
        limit = max(1, min(int(top_k), MAX_QUERY_TOP_K))
        try:
            search = self._open().search([float(x) for x in embedding]).where(
                where, prefilter=True
            )
            metric = getattr(search, "distance_type", None)
            if metric is not None and self.distance_metric:
                search = search.distance_type(self.distance_metric)
            rows = search.limit(limit).to_list()
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"lancedb query failed: {exc}") from exc
        hits: list[VectorHit] = []
        for r in rows:
            distance = r.get("_distance")
            if distance is None:
                continue
            distance = float(distance)
            hits.append(
                VectorHit(
                    chunk_id=str(r.get("chunk_id") or ""),
                    doc_id=str(r.get("doc_id") or ""),
                    score=_distance_to_score(distance, self.distance_metric),
                    distance=distance,
                    metadata=_load_metadata(r.get("metadata_json")),
                )
            )
        return hits

    def count(self, *, owner_id: str | None = None) -> int:
        if not self._table_ok():
            return 0
        try:
            tbl = self._open()
            if owner_id:
                return int(tbl.count_rows(f"owner_id = {_quote_literal(owner_id)}"))
            return int(tbl.count_rows())
        except Exception:  # noqa: BLE001
            return 0

    def list_chunk_ids(self, *, owner_id: str) -> set[str]:
        if not owner_id or not self._table_ok():
            return set()
        try:
            rows = (
                self._open()
                .search()
                .where(f"owner_id = {_quote_literal(owner_id)}", prefilter=True)
                .select(["chunk_id"])
                .limit(1_000_000)
                .to_list()
            )
        except Exception:  # noqa: BLE001
            return set()
        return {str(r.get("chunk_id")) for r in rows if r.get("chunk_id")}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _distance_to_score(distance: float, metric: str) -> float:
    """距离 → [0,1] 相似度，与 base 契约（越大越近）对齐。

    * ``l2``：LanceDB 默认。用 ``1 / (1 + d)`` 单调递减映射，d=0 → 1.0。
    * ``cosine``：``1 - d``（d ∈ [0,2]），截断到 [0,1]。
    * ``dot``：点积不是距离，越大越近，直接截断到 [0,1]。
    """
    if metric == "cosine":
        return max(0.0, min(1.0, 1.0 - distance))
    if metric == "dot":
        return max(0.0, min(1.0, distance))
    return max(0.0, min(1.0, 1.0 / (1.0 + max(distance, 0.0))))


def _dump_metadata(metadata: dict[str, Any]) -> str:
    if not metadata:
        return "{}"
    import json

    try:
        return json.dumps(metadata, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 — 不可序列化的元数据不阻断写入
        return "{}"


def _load_metadata(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}
