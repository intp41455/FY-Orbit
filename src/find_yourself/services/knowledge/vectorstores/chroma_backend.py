"""Chroma 本地模式向量后端（P10 · A-向量库-04）。

* **本地持久化**：``chromadb.PersistentClient(path=...)`` 落本地目录，零服务；
  也可传 ``host``/``port`` 走 ``HttpClient`` 连独立 Chroma 服务，两种模式共用
  本后端（构造参数决定）。
* ``owner_id`` 映射为 Chroma 的 **collection 维度**——每个 owner 一个 collection
  （``f"{collection_prefix}__{owner_id}"``）。这样跨 owner 的向量**物理隔离在
  不同 collection 内**，不是查询后过滤；与 sqlite-vec 的 PARTITION KEY 同哲学，
  且避免上游 Chroma 版本对 ``where`` 谓词支持面的差异。
* **幂等**：Chroma 的 ``upsert`` 原生按 id 覆盖，直接用它（覆写 base 默认）。
* **过滤键**：``doc_ids`` 映射为 ``where={"doc_id": {"$in": [...]}}``；
  其他键抛 :class:`VectorFilterUnsupported`。
* **距离与分数**：collection 建时显式声明 ``hnsw:space``（默认 ``cosine``）。
  Chroma 返回的是**距离**（越小越近），``score`` 统一换算到 ``[0,1]``。
* 维度：Chroma 按首条写入向量定维；已存在 collection 维度与请求不符 →
  **删 collection 后重建**（派生索引可再生）。
* chromadb 包缺失 / 连接失败 → ``is_available() is False``，读接口空、写接口 0，
  **不抛异常**；参数错误仍抛异常。
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

try:  # chromadb 缺失不致命：本模块仍可导入，is_available() 会报告 False
    import chromadb  # type: ignore[import-not-found]

    HAS_CHROMADB = True
except ImportError:  # pragma: no cover - 只有在无依赖环境才会走到
    chromadb = None  # type: ignore[assignment]
    HAS_CHROMADB = False


#: collection 名前缀（每个 owner 一个 collection）。
#: **带后端身份前缀**：Chroma 虽落在独立目录，但部署方可能把 path 指到共用
#: 位置；带前缀后跨后端不撞名，运维对账时一眼看出 collection 归属。
DEFAULT_COLLECTION_PREFIX = "chroma_kb_chunks"
DEFAULT_PERSIST_DIR = ".find_yourself/chroma"
#: 默认距离空间：Chroma 支持 cosine / l2 / ip
DEFAULT_SPACE = "cosine"
MAX_QUERY_TOP_K = 200

_SUPPORTED_FILTERS = frozenset({"doc_ids"})

#: Chroma collection 名约束：3-63 字符，字母数字开头结尾，中间允许 _ - .
_COLLECTION_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _collection_name(prefix: str, owner_id: str) -> str:
    """owner 隔离的 collection 名。

    Chroma 对 collection 名有字符与长度约束，owner_id 可能含非法字符
    （邮箱、UUID 带花括号等）→ 做保守替换，并保证长度落在 3..63。
    """
    safe_prefix = _COLLECTION_SAFE_RE.sub("_", prefix or DEFAULT_COLLECTION_PREFIX)
    safe_owner = _COLLECTION_SAFE_RE.sub("_", owner_id or "")
    name = f"{safe_prefix}__{safe_owner}"
    if len(name) < 3:
        name = (name + "___")[:3]
    return name[:63]


class ChromaStore(VectorStore):
    """Chroma 本地模式后端（PersistentClient / HttpClient 二选一）。"""

    name = "chroma"

    def __init__(
        self,
        *,
        dim: int,
        path: str | None = DEFAULT_PERSIST_DIR,
        host: str | None = None,
        port: int | None = None,
        collection_prefix: str = DEFAULT_COLLECTION_PREFIX,
        space: str = DEFAULT_SPACE,
        session: Any = None,
        **kwargs: Any,
    ):
        if int(dim) <= 0:
            raise ValueError(f"vector dim must be positive, got {dim}")
        self.dim = int(dim)
        self.path = path
        self.host = host
        self.port = port
        self.collection_prefix = collection_prefix
        self.space = space
        self._client_kwargs = kwargs
        self._client: Any = None
        self._available: bool | None = None
        # ``session`` 是 search.py 的通用调用签名（所有后端都收到它），但本后端
        # 用独立持久化目录、不共享 SQLAlchemy 事务。显式接收并忽略（非静默吞掉）。
        self.session = session

    # -- availability -------------------------------------------------------- #
    def is_available(self) -> bool:
        if not HAS_CHROMADB or self._available is False:
            return False
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            if self.host:
                self._client = chromadb.HttpClient(
                    host=self.host, port=self.port or 8000, **self._client_kwargs
                )
            else:
                self._client = chromadb.PersistentClient(
                    path=self.path or DEFAULT_PERSIST_DIR, **self._client_kwargs
                )
            # 主动 ping：部分版本 connect 是惰性的，缺服务要到首次操作才暴露
            beat = getattr(self._client, "heartbeat", None)
            if callable(beat):
                beat()
            return True
        except Exception:  # noqa: BLE001 — 缺依赖 / 无服务 / 目录不可写 → 降级
            return False

    def _client_or_none(self) -> Any:
        if not self.is_available():
            return None
        return self._client

    # -- collection lifecycle ------------------------------------------------- #
    def _existing_dim(self, name: str) -> int | None:
        """已存在 collection 的维度；不存在 → None；存在但读不到 → -1。

        维度来源有两条，按可靠性排序：

        1. **collection metadata 的 ``dim``**（本后端建 collection 时写入）——
           最可靠。Chroma 不支持读取 collection 的向量维度（``peek`` 在新版本
           不再返回 embeddings），故必须自己记账。
        2. 兜底：``peek`` 若能拿到 embeddings 就数长度（老版本 Chroma）。

        区分「不存在」（None，合法空状态）与「存在但读不到」（-1，不确定态）：
        ``get_collection`` 对不存在的 collection 抛异常，必须单独接住。
        """
        client = self._client_or_none()
        if client is None:
            return None
        col = None
        try:
            col = client.get_collection(name)
        except Exception:  # noqa: BLE001 — 不存在（Chroma 以异常表达）
            return None
        if col is None:
            return None
        # 路径 1：本后端写入的 dim 元数据
        try:
            meta = getattr(col, "metadata", None) or {}
            recorded = meta.get("dim")
            if recorded is not None:
                return int(recorded)
        except Exception:  # noqa: BLE001
            pass
        # 路径 2：老版本 Chroma 的 peek 可能带 embeddings
        try:
            peek = col.peek(limit=1)
            embeddings = (peek or {}).get("embeddings") or []
            if embeddings:
                first = embeddings[0]
                return len(first) if first is not None else -1
        except Exception:  # noqa: BLE001
            pass
        # 存在但无任何维度信息
        return -1

    def _ensure_collection(self, owner_id: str) -> bool:
        """取/建 owner 的 collection（维度漂移时重建）。False ⇒ 降级。

        四态处理：不存在 → 建；空 collection（维度未定）→ 直接用；维度匹配 →
        直接用；维度不符 → 删后建。空 collection 必须「直接用」——它没有维度
        信息，删掉重建是多余副作用，且会丢掉「首条写入定维」的自然路径。
        """
        client = self._client_or_none()
        if client is None:
            return False
        name = _collection_name(self.collection_prefix, owner_id)
        current = self._existing_dim(name)
        if current == self.dim or current == -1:
            # 维度匹配，或存在但维度未定（空 collection）→ 直接用
            try:
                client.get_or_create_collection(
                    name=name, metadata={"hnsw:space": self.space, "dim": self.dim}
                )
                return True
            except Exception:  # noqa: BLE001
                self._available = False
                return False
        try:
            if current is not None:  # 维度漂移 → 派生索引重建
                client.delete_collection(name)
            client.get_or_create_collection(
                name=name, metadata={"hnsw:space": self.space, "dim": self.dim}
            )
            return True
        except Exception:  # noqa: BLE001 — 建 collection 失败 → 降级
            self._available = False
            return False

    def _collection_exists(self, owner_id: str) -> bool:
        """collection 是否真实存在（含空 collection）。只读路径的判断依据。"""
        client = self._client_or_none()
        if client is None:
            return False
        name = _collection_name(self.collection_prefix, owner_id)
        try:
            return bool(client.get_collection(name))
        except Exception:  # noqa: BLE001 — 不存在
            return False

    def _collection_ok(self, owner_id: str) -> bool:
        """只读路径用：collection 存在且维度匹配（或维度未定）才查。"""
        if not self.is_available():
            return False
        name = _collection_name(self.collection_prefix, owner_id)
        current = self._existing_dim(name)
        # None = 不存在 → 不查；-1 = 空 collection（维度未定）→ 可查（返回空）
        return current is not None and (current == self.dim or current == -1)

    def _col(self, owner_id: str) -> Any:
        name = _collection_name(self.collection_prefix, owner_id)
        return self._client_or_none().get_collection(name)

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
        if not self._ensure_collection(owner_id):
            return 0
        # owner 隔离靠 collection，故不再写 owner_id 元数据列；doc_id 必须写供过滤
        # 注意：同一 owner 内重复 add 同 id 会抛 DuplicateIDError —— add 语义就是
        # 「插入」，需要幂等请走 upsert。这与 base 契约一致（base 的 upsert 默认
        # 实现才是先删后写）。
        try:
            self._col(owner_id).add(
                ids=[r.chunk_id for r in material],
                embeddings=[[float(x) for x in r.embedding] for r in material],
                metadatas=[_metadata(r) for r in material],
                documents=[str(r.metadata.get("text") or "") for r in material],
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"chroma add failed: {exc}") from exc
        return len(material)

    def upsert(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        """Chroma 原生 upsert（按 id 覆盖），覆写 base 的「先删后写」。"""
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
        if not self._ensure_collection(owner_id):
            return 0
        try:
            self._col(owner_id).upsert(
                ids=[r.chunk_id for r in material],
                embeddings=[[float(x) for x in r.embedding] for r in material],
                metadatas=[_metadata(r) for r in material],
                documents=[str(r.metadata.get("text") or "") for r in material],
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"chroma upsert failed: {exc}") from exc
        return len(material)

    def delete(self, chunk_ids: Iterable[str], *, owner_id: str) -> int:
        ids = [c for c in chunk_ids if c]
        if not ids or not owner_id:
            return 0
        if not self._collection_ok(owner_id):
            return 0
        try:
            col = self._col(owner_id)
            before = int(col.count())
            col.delete(ids=ids)
            after = int(col.count())
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"chroma delete failed: {exc}") from exc
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
        where: dict[str, Any] | None = None
        if filters:
            unknown = set(filters) - _SUPPORTED_FILTERS
            if unknown:
                raise VectorFilterUnsupported(
                    f"unsupported metadata filters: {sorted(unknown)}"
                )
            doc_ids = [d for d in (filters.get("doc_ids") or []) if d]
            if doc_ids:
                where = {"doc_id": {"$in": doc_ids}}
        if not self._collection_ok(owner_id):
            return []
        limit = max(1, min(int(top_k), MAX_QUERY_TOP_K))
        try:
            res = self._col(owner_id).query(
                query_embeddings=[[float(x) for x in embedding]],
                n_results=limit,
                where=where,
                include=["metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"chroma query failed: {exc}") from exc
        ids = (res or {}).get("ids") or [[]]
        dists = (res or {}).get("distances") or [[]]
        metas = (res or {}).get("metadatas") or [[]]
        hits: list[VectorHit] = []
        for i, chunk_id in enumerate(ids[0] if ids else []):
            distance = float(dists[0][i]) if dists and dists[0] and i < len(dists[0]) else None
            if distance is None:
                continue
            meta = metas[0][i] if metas and metas[0] and i < len(metas[0]) else {}
            meta = dict(meta or {})
            doc_id = str(meta.pop("doc_id", "") or "")
            hits.append(
                VectorHit(
                    chunk_id=str(chunk_id),
                    doc_id=doc_id,
                    score=_distance_to_score(distance, self.space),
                    distance=distance,
                    metadata=meta,
                )
            )
        return hits

    def count(self, *, owner_id: str | None = None) -> int:
        if not self.is_available():
            return 0
        try:
            if owner_id:
                if not self._collection_ok(owner_id):
                    return 0
                return int(self._col(owner_id).count())
            total = 0
            for col in self._client.list_collections():
                name = getattr(col, "name", None) or str(col)
                if not name.startswith(self.collection_prefix):
                    continue
                total += int(self._client.get_collection(name).count())
            return total
        except Exception:  # noqa: BLE001
            return 0

    def list_chunk_ids(self, *, owner_id: str) -> set[str]:
        if not owner_id or not self._collection_ok(owner_id):
            return set()
        try:
            res = self._col(owner_id).get(include=[])
        except Exception:  # noqa: BLE001
            return set()
        ids = (res or {}).get("ids") or []
        return {str(i) for i in ids}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _metadata(record: VectorRecord) -> dict[str, Any]:
    """Chroma 元数据只接受标量；doc_id 必写（过滤用），其余标量透传。"""
    out: dict[str, Any] = {"doc_id": str(record.doc_id or "")}
    for k, v in (record.metadata or {}).items():
        if k == "doc_id":
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _distance_to_score(distance: float, space: str) -> float:
    """Chroma 距离 → [0,1] 相似度（越大越近）。

    * ``cosine``：d ∈ [0,2]，``score = 1 - d``，截断 [0,1]。
    * ``l2``：``score = 1 / (1 + d)``，d=0 → 1.0。
    * ``ip``（内积）：Chroma 返回 ``1 - ip``，故 ``score = 1 - d``。
    """
    if space == "l2":
        return max(0.0, min(1.0, 1.0 / (1.0 + max(distance, 0.0))))
    return max(0.0, min(1.0, 1.0 - distance))
