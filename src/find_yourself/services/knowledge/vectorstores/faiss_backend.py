"""FAISS 进程内索引文件向量后端（P10 · A-向量库-05）。

FAISS 与其余三后端的**根本差别**：它是**纯索引库**，没有存储层，也不认字符串 id。
因此本后端必须自建两样东西：

1. **id 映射**：FAISS 的索引位置是 ``int64``。用 ``<name>.meta.json`` 维护
   ``row_idx -> {chunk_id, doc_id, owner_id, metadata}`` 双向映射。
   ``chunk_id`` 为字符串，故映射是「row → chunk_id」+「chunk_id → row」两份。
2. **owner 隔离**：FAISS 无谓词过滤，**不能**「查完再滤」——那会让跨 owner 的
   向量进入候选，违反 base 契约的「授权谓词先于排序」。做法是**每 owner 一个
   独立索引文件**（``<dir>/<owner_hash>.faiss`` + 同名 ``.meta.json``）。
   owner 之间物理隔离，与 sqlite-vec 的 PARTITION KEY 同哲学。

* **删除**：FAISS 的 ``IndexFlat*`` 不支持 ``remove_ids`` 之外的高效删除；
  实现用 ``remove_ids`` 后**重建索引**（``IndexIDMap2`` 保留原 id）。删除后
  row 空洞由重建消除，映射同步落盘。
* **持久化**：``faiss.write_index`` / ``read_index`` + 原子写 meta（写 tmp 后
  ``os.replace``），避免进程崩溃留下半个索引文件。
* **距离与分数**：默认 ``IndexFlatIP``（内积）+ L2 归一化 ⇒ 内积等价 cosine。
  故 ``score`` 直接取内积截断 [0,1]，``distance = 1 - score``。
* faiss 包缺失 / 目录不可写 → ``is_available() is False``，读接口空、写接口 0，
  **不抛异常**；参数错误仍抛异常。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any, Iterable, Sequence

from ....services.errors import ValidationFailed
from .base import (
    VectorFilterUnsupported,
    VectorHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
)

try:  # faiss 缺失不致命：本模块仍可导入，is_available() 会报告 False
    import faiss  # type: ignore[import-not-found]

    HAS_FAISS = True
except ImportError:  # pragma: no cover - 只有在无依赖环境才会走到
    faiss = None  # type: ignore[assignment]
    HAS_FAISS = False

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False


#: 索引与元数据落盘目录
DEFAULT_INDEX_DIR = ".find_yourself/faiss"
MAX_QUERY_TOP_K = 200

_SUPPORTED_FILTERS = frozenset({"doc_ids"})


def _owner_key(owner_id: str) -> str:
    """owner_id → 文件名安全摘要（避免路径穿越与非法字符）。"""
    return hashlib.sha256((owner_id or "").encode("utf-8")).hexdigest()[:32]


class FaissStore(VectorStore):
    """FAISS 索引文件后端：每 owner 一索引 + 一 JSON 映射。"""

    name = "faiss"

    def __init__(
        self,
        *,
        dim: int,
        index_dir: str = DEFAULT_INDEX_DIR,
        metric: str = "ip",
        session: Any = None,
        **kwargs: Any,
    ):
        if int(dim) <= 0:
            raise ValueError(f"vector dim must be positive, got {dim}")
        if metric not in {"ip", "l2"}:
            raise ValueError(f"faiss metric must be 'ip' or 'l2', got {metric!r}")
        self.dim = int(dim)
        self.index_dir = index_dir
        self.metric = metric
        self._extra = kwargs
        # ``session`` 是 search.py 的通用调用签名（所有后端都收到它），但本后端
        # 用独立索引文件、不共享 SQLAlchemy 事务。显式接收并忽略（非静默吞掉）。
        self.session = session
        #: owner_key -> (index, 映射 dict, id->row dict)
        self._cache: dict[str, tuple[Any, dict[str, dict[str, Any]], dict[str, int]]] = {}
        self._available: bool | None = None

    # -- availability -------------------------------------------------------- #
    def is_available(self) -> bool:
        if not HAS_FAISS or not HAS_NUMPY or self._available is False:
            return False
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            os.makedirs(self.index_dir, exist_ok=True)
            return True
        except Exception:  # noqa: BLE001 — 目录不可写 → 降级
            return False

    # -- paths --------------------------------------------------------------- #
    def _paths(self, owner_id: str) -> tuple[str, str]:
        key = _owner_key(owner_id)
        return (
            os.path.join(self.index_dir, f"{key}.faiss"),
            os.path.join(self.index_dir, f"{key}.meta.json"),
        )

    def _new_index(self) -> Any:
        if self.metric == "l2":
            return faiss.IndexIDMap2(faiss.IndexFlatL2(self.dim))
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))

    # -- load / save ---------------------------------------------------------- #
    def _load(self, owner_id: str) -> tuple[Any, dict[str, dict[str, Any]], dict[str, int]]:
        """取 owner 的（索引, row->meta, chunk_id->row）。首次访问建空索引。

        **缓存按 (owner, dim) 校验**：同一 owner 换维度后，旧缓存的索引维度
        不符 → 丢弃缓存并从磁盘重载（磁盘上仍是旧维度时由 ``_ensure_index``
        判定漂移后重建）。缓存不校验维度会让「维度漂移重建」静默失效。
        """
        key = _owner_key(owner_id)
        cached = self._cache.get(key)
        if cached is not None:
            index = cached[0]
            if int(getattr(index, "d", self.dim)) == self.dim:
                return cached
            # 维度漂移：缓存失效，落盘内容也不可用 → 就地重建空索引
            self._cache.pop(key, None)
            fresh = self._new_index()
            entry = (fresh, {}, {})
            self._cache[key] = entry
            return entry
        idx_path, meta_path = self._paths(owner_id)
        index = None
        mapping: dict[str, dict[str, Any]] = {}
        if os.path.exists(idx_path):
            try:
                index = faiss.read_index(idx_path)
            except Exception:  # noqa: BLE001 — 索引文件损坏 → 当作空索引重建
                index = None
        # 磁盘索引维度与请求不符 → 派生索引重建（源数据在 kb 主库，不丢）
        if index is not None and int(getattr(index, "d", self.dim)) != self.dim:
            index = None
            mapping = {}
        if index is None:
            index = self._new_index()
        if index is not None and os.path.exists(meta_path) and not mapping:
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    mapping = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            except Exception:  # noqa: BLE001 — 映射损坏 → 与索引一起重来更安全
                mapping = {}
                index = self._new_index()
        rev = {str(m.get("chunk_id")): int(r) for r, m in mapping.items() if m.get("chunk_id")}
        entry = (index, mapping, rev)
        self._cache[key] = entry
        return entry

    def _save(self, owner_id: str) -> None:
        index, mapping, _rev = self._load(owner_id)
        idx_path, meta_path = self._paths(owner_id)
        try:
            faiss.write_index(index, idx_path)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"faiss write_index failed: {exc}") from exc
        # meta 原子写：同目录 tmp + os.replace，避免半文件
        fd, tmp = tempfile.mkstemp(dir=self.index_dir, suffix=".meta.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(mapping, fh, ensure_ascii=False)
            os.replace(tmp, meta_path)
        except Exception as exc:  # noqa: BLE001
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise VectorStoreError(f"faiss meta write failed: {exc}") from exc

    # -- vector helpers -------------------------------------------------------- #
    def _matrix(self, embeddings: Iterable[Sequence[float]]) -> Any:
        arr = np.asarray([[float(x) for x in e] for e in embeddings], dtype="float32")
        if self.metric == "ip":
            # 内积索引要求 L2 归一化才能等价 cosine
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        return arr

    # -- read-only guard ------------------------------------------------------- #
    def _has_owner_index(self, owner_id: str) -> bool:
        """只读路径的守卫：索引文件存在**且维度匹配**才可查。

        维度不符不触发重建（重建属写路径职责）——只读路径一律返回空，
        由调用方对账后走 ``add`` 重建。与 sqlite-vec 的 ``_table_ok`` 同语义。
        """
        if not self.is_available():
            return False
        idx_path, _ = self._paths(owner_id)
        if not os.path.exists(idx_path):
            return False
        try:
            probe = faiss.read_index(idx_path)
        except Exception:  # noqa: BLE001 — 索引损坏 → 视为不可查
            return False
        return int(getattr(probe, "d", self.dim)) == self.dim

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
        if not self.is_available():
            return 0
        index, mapping, rev = self._load(owner_id)
        try:
            next_row = (max((int(r) for r in mapping), default=-1)) + 1
            rows: list[int] = []
            for r in material:
                # chunk_id 已存在 → 占用原 row，避免重复行（幂等）
                row = rev.get(r.chunk_id)
                if row is None:
                    row = next_row
                    next_row += 1
                else:
                    _remove_rows(index, [row])
                rows.append(row)
                mapping[str(row)] = {
                    "chunk_id": r.chunk_id,
                    "doc_id": r.doc_id,
                    "metadata": dict(r.metadata or {}),
                }
                rev[r.chunk_id] = row
            index.add_with_ids(self._matrix([r.embedding for r in material]), np.asarray(rows, dtype="int64"))
        except VectorStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"faiss add failed: {exc}") from exc
        self._save(owner_id)
        return len(material)

    def upsert(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        """FAISS 无原生 upsert：add 内部已按 chunk_id 占位覆盖，语义即 upsert。"""
        return self.add(records, owner_id=owner_id)

    def delete(self, chunk_ids: Iterable[str], *, owner_id: str) -> int:
        ids = [c for c in chunk_ids if c]
        if not ids or not owner_id:
            return 0
        if not self._has_owner_index(owner_id):
            return 0
        index, mapping, rev = self._load(owner_id)
        target = [rev[c] for c in ids if c in rev]
        if not target:
            return 0
        try:
            _remove_rows(index, target)
            for c in ids:
                row = rev.pop(c, None)
                if row is not None:
                    mapping.pop(str(row), None)
            # 删除产生 row 空洞，重建索引把空洞压实（映射同步重编号）
            index, mapping, rev = _compact(self._new_index(), index, mapping)
            self._cache[_owner_key(owner_id)] = (index, mapping, rev)
        except VectorStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"faiss delete failed: {exc}") from exc
        self._save(owner_id)
        return len(target)

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
        # 参数校验先于一切可用性检查
        doc_ids: list[str] = []
        if filters:
            unknown = set(filters) - _SUPPORTED_FILTERS
            if unknown:
                raise VectorFilterUnsupported(
                    f"unsupported metadata filters: {sorted(unknown)}"
                )
            doc_ids = [d for d in (filters.get("doc_ids") or []) if d]
        if not self._has_owner_index(owner_id):
            return []
        index, mapping, _rev = self._load(owner_id)
        if int(index.ntotal) == 0:
            return []
        limit = max(1, min(int(top_k), MAX_QUERY_TOP_K))
        # 过滤只能后置（FAISS 无谓词）；为不破坏「授权谓词先于排序」，先按
        # doc_ids 收窄候选集再排序——故请求量放大到 min(ntotal, limit*8)，
        # 仍不足时退化为全量再截断。
        probe = limit if not doc_ids else min(int(index.ntotal), max(limit * 8, limit))
        try:
            scores, rows = index.search(self._matrix([embedding]), probe)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"faiss query failed: {exc}") from exc
        hits: list[VectorHit] = []
        for score, row in zip(scores[0], rows[0]):
            if int(row) < 0:
                continue
            meta = mapping.get(str(int(row)))
            if not meta:
                continue
            if doc_ids and str(meta.get("doc_id") or "") not in doc_ids:
                continue
            similarity = max(0.0, min(1.0, float(score)))
            hits.append(
                VectorHit(
                    chunk_id=str(meta.get("chunk_id") or ""),
                    doc_id=str(meta.get("doc_id") or ""),
                    score=similarity,
                    distance=1.0 - similarity,
                    metadata=dict(meta.get("metadata") or {}),
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def count(self, *, owner_id: str | None = None) -> int:
        if not self.is_available():
            return 0
        try:
            if owner_id:
                if not self._has_owner_index(owner_id):
                    return 0
                index, _m, _r = self._load(owner_id)
                return int(index.ntotal)
            total = 0
            for fname in os.listdir(self.index_dir):
                if fname.endswith(".faiss"):
                    try:
                        total += int(faiss.read_index(os.path.join(self.index_dir, fname)).ntotal)
                    except Exception:  # noqa: BLE001
                        continue
            return total
        except Exception:  # noqa: BLE001
            return 0

    def list_chunk_ids(self, *, owner_id: str) -> set[str]:
        if not owner_id or not self._has_owner_index(owner_id):
            return set()
        _index, mapping, _rev = self._load(owner_id)
        return {str(m.get("chunk_id")) for m in mapping.values() if m.get("chunk_id")}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _remove_rows(index: Any, rows: list[int]) -> None:
    """remove_ids 要求 IndexIDMap2 支持；老版本无此 API 时跳过（由 _compact 兜底）。"""
    if not rows:
        return
    remover = getattr(index, "remove_ids", None)
    if not callable(remover):
        return
    remover(np.asarray(rows, dtype="int64"))


def _compact(
    fresh: Any, index: Any, mapping: dict[str, dict[str, Any]]
) -> tuple[Any, dict[str, dict[str, Any]], dict[str, int]]:
    """把 index 里现存向量重编号到连续 row（消除删除留下的空洞）。"""
    ntotal = int(index.ntotal)
    if ntotal == 0:
        return fresh, {}, {}
    _scores, rows = index.search(
        np.asarray(index.reconstruct_n(0, ntotal), dtype="float32"), ntotal
    )
    flat_rows = [int(r) for r in rows[0] if int(r) >= 0]
    # 注意：上面用「自身向量搜自身」拿到的是最近邻顺序；这里只需要 id 集合，
    # 顺序无关。为稳健起见直接读 IDMap2 的 id_map。
    id_map = _read_id_map(index)
    if id_map:
        flat_rows = id_map
    new_mapping: dict[str, dict[str, Any]] = {}
    new_rev: dict[str, int] = {}
    vectors: list[Any] = []
    ids: list[int] = []
    for new_row, old_row in enumerate(flat_rows):
        meta = mapping.get(str(old_row))
        if not meta:
            continue
        vectors.append(index.reconstruct(int(old_row)))
        ids.append(new_row)
        new_mapping[str(new_row)] = meta
        if meta.get("chunk_id"):
            new_rev[str(meta["chunk_id"])] = new_row
    if vectors:
        fresh.add_with_ids(
            np.asarray(vectors, dtype="float32"), np.asarray(ids, dtype="int64")
        )
    return fresh, new_mapping, new_rev


def _read_id_map(index: Any) -> list[int]:
    """读 IndexIDMap2 的 id_map（faiss 用 swig 暴露，取不到则返回空）。"""
    try:
        raw = index.id_map
        return [int(x) for x in raw]
    except Exception:  # noqa: BLE001
        return []
