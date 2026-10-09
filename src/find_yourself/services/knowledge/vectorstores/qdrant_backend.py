"""Qdrant 本地模式向量后端（P10 · A-向量库-06）。

* **本地模式**：``QdrantClient(path=...)`` 是 Qdrant 官方嵌入式模式（本地目录，
  零服务）；也可传 ``url``（``http://host:6333``）连独立 Qdrant 服务，两种模式
  共用本后端。注意：``path`` 与 ``url`` **互斥**，同时给以 ``url`` 为准。
* ``owner_id`` 用 Qdrant 原生的 **payload 过滤**（``Filter(must=[FieldCondition(
  key="owner_id", match=...)])``）——Qdrant 的过滤在 HNSW 遍历**之前**做候选剪枝
  （``prefetch``/filter 下推），符合 base 契约的「授权谓词先于排序」。
* **幂等**：Qdrant 的 ``upsert`` 原生按 point id 覆盖。point id 用
  ``chunk_id`` 的 **UUIDv5** 派生（Qdrant 的 id 只接受 uint64 或 UUID）——
  这样同一 ``chunk_id`` 恒定映射到同一 point，重复写入天然幂等，且不引入
  额外的 id 映射表。原始 ``chunk_id`` 存在 payload 里，查询时回读。
* **过滤键**：``doc_ids`` 映射为第二个 ``FieldCondition(key="doc_id", match=Any)``；
  其他键抛 :class:`VectorFilterUnsupported`。
* **距离与分数**：collection 建时声明 ``Cosine``（默认）。Qdrant 返回的
  ``score`` 已是「越大越近」（cosine 相似度），直接截断 [0,1]；
  ``distance = 1 - score``。
* 维度漂移：已存在 collection 维度不符 → 删 collection 后重建（派生索引可再生）。
* qdrant-client 包缺失 / 连接失败 → ``is_available() is False``，读接口空、
  写接口 0，**不抛异常**；参数错误仍抛异常。
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Sequence

from ....services.errors import ValidationFailed
from .base import (
    VectorFilterUnsupported,
    VectorHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
)

try:  # qdrant_client 缺失不致命：本模块仍可导入，is_available() 会报告 False
    from qdrant_client import QdrantClient  # type: ignore[import-not-found]
    from qdrant_client import models as qmodels

    HAS_QDRANT = True
except ImportError:  # pragma: no cover - 只有在无依赖环境才会走到
    QdrantClient = None  # type: ignore[assignment]
    qmodels = None  # type: ignore[assignment]
    HAS_QDRANT = False


#: 默认 collection 名。**带后端身份前缀**（理由同 chroma_backend）：
#: 实际 collection 名还会追加 ``__d<dim>`` 维度指纹，见 ``_collection()``。
DEFAULT_COLLECTION = "qdrant_kb_chunks_vec"
DEFAULT_LOCAL_PATH = ".find_yourself/qdrant"
MAX_QUERY_TOP_K = 200

_SUPPORTED_FILTERS = frozenset({"doc_ids"})

#: chunk_id → point UUID 的命名空间（固定常量，保证跨进程稳定）
_POINT_NS = uuid.UUID("6f1b0f9e-6a1e-4f2e-9a5c-2b0f3d7a1c88")


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_POINT_NS, str(chunk_id)))


#: 本地 path 模式的活动 client（path -> client）。
#: Qdrant 本地模式对同一目录持独占锁，多个 client 并存会导致后开者读到空库。
#: 用模块级注册表在开新实例前释放旧实例——这是 Qdrant 嵌入式模式的已知约束，
#: 不是本后端的实现缺陷。
_LOCAL_CLIENTS: dict[str, Any] = {}


def _release_local_client(path: str) -> None:
    """释放同路径的旧 client（若有）。失败静默——GC 兜底。"""
    old = _LOCAL_CLIENTS.pop(path, None)
    if old is None:
        return
    closer = getattr(old, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001
            pass


def _register_local_client(path: str, client: Any) -> None:
    _LOCAL_CLIENTS[path] = client


class QdrantStore(VectorStore):
    """Qdrant 后端（本地嵌入式 path 模式 / 远端 url 模式）。"""

    name = "qdrant"

    def __init__(
        self,
        *,
        dim: int,
        path: str | None = DEFAULT_LOCAL_PATH,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str = DEFAULT_COLLECTION,
        distance: str = "cosine",
        session: Any = None,
        **kwargs: Any,
    ):
        if int(dim) <= 0:
            raise ValueError(f"vector dim must be positive, got {dim}")
        self.dim = int(dim)
        self.path = path
        self.url = url
        self.api_key = api_key
        self.collection_name = collection_name
        self.distance = distance
        self._client_kwargs = kwargs
        self._client: Any = None
        self._available: bool | None = None
        # ``session`` 是 search.py 的通用调用签名（所有后端都收到它），但本后端
        # 用独立 Qdrant 存储、不共享 SQLAlchemy 事务。显式接收并忽略（非静默吞掉）。
        self.session = session

    # -- availability -------------------------------------------------------- #
    def is_available(self) -> bool:
        if not HAS_QDRANT or self._available is False:
            return False
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            if self.url:
                self._client = QdrantClient(
                    url=self.url, api_key=self.api_key, **self._client_kwargs
                )
            else:
                # 本地 path 模式：Qdrant 对同一目录持进程内独占锁。若上一实例
                # 未关闭（被 GC 或换维度重建），新实例会读到「空库」——表现为
                # collection 神秘消失。故开新实例前先释放同路径的旧实例。
                _release_local_client(self.path or DEFAULT_LOCAL_PATH)
                self._client = QdrantClient(
                    path=self.path or DEFAULT_LOCAL_PATH, **self._client_kwargs
                )
            # 主动探测：本地模式建目录也会在这里失败，缺服务则在 get_collections 暴露
            self._client.get_collections()
            _register_local_client(self.path or DEFAULT_LOCAL_PATH, self._client)
            return True
        except Exception:  # noqa: BLE001 — 缺依赖 / 无服务 / 目录不可写 → 降级
            return False

    def _client_or_none(self) -> Any:
        if not self.is_available():
            return None
        return self._client

    # -- collection lifecycle ------------------------------------------------- #
    def _distance_enum(self) -> Any:
        name = (self.distance or "cosine").upper()
        return getattr(qmodels.Distance, name, qmodels.Distance.COSINE)

    def _collection(self) -> str:
        """实际使用的 collection 名 = 逻辑名 + 维度指纹。

        **为什么把维度编进 collection 名**：Qdrant 本地模式**不支持在同一
        collection 名上改维度重建**——``delete_collection`` + ``create_collection``
        换成新维度后，磁盘上残留旧维度的段文件，**连全新 client 写入都会抛
        ``could not broadcast input array from shape (N,) into shape (M,)``**
        （已实测确认，非本后端缺陷）。故改用「维度即身份」：维度漂移等价于
        换了一个新 collection，从根上回避重建路径。

        代价：旧维度的 collection 会残留（不再被引用）。这是**可接受的**——
        向量表是可再生的派生索引，且用户切换 embedding 模型是低频事件。
        ``list_vector_stores`` 的运维诊断可通过 ``vector_backend_status`` 观察。
        """
        base = (self.collection_name or DEFAULT_COLLECTION).strip()
        return f"{base}__d{self.dim}"

    def _legacy_collection(self) -> str:
        """无维度后缀的旧命名（仅用于只读兼容探测）。"""
        return (self.collection_name or DEFAULT_COLLECTION).strip()

    def _existing_dim(self) -> int | None:
        """当前 collection 的维度；不存在 → None；存在但读不到 → -1。"""
        client = self._client_or_none()
        if client is None:
            return None
        try:
            info = client.get_collection(self._collection())
        except Exception:  # noqa: BLE001 — 不存在
            return None
        try:
            params = getattr(info, "config", None)
            params = getattr(params, "params", None)
            vectors = getattr(params, "vectors", None)
            # 单向量配置：vectors.size；多向量（dict）取第一个
            size = getattr(vectors, "size", None)
            if size is None and isinstance(vectors, dict) and vectors:
                size = getattr(next(iter(vectors.values())), "size", None)
            return int(size) if size is not None else -1
        except Exception:  # noqa: BLE001
            return -1

    def _ensure_collection(self) -> bool:
        """确保当前维度的 collection 存在（不存在则建）。False ⇒ 降级。

        因 collection 名含维度指纹，「维度漂移」表现为「目标 collection 不存在」
        ——只需创建即可，无需删除任何东西（见 ``_collection`` 的说明）。
        """
        client = self._client_or_none()
        if client is None:
            return False
        if self._existing_dim() == self.dim:
            return True
        try:
            client.create_collection(
                collection_name=self._collection(),
                vectors_config=qmodels.VectorParams(
                    size=self.dim, distance=self._distance_enum()
                ),
            )
            return True
        except Exception:  # noqa: BLE001 — 建 collection 失败 → 降级
            self._available = False
            return False

    def _collection_ok(self) -> bool:
        """只读路径用：当前维度的 collection 存在才查（不副作用建 collection）。"""
        if not self.is_available():
            return False
        return self._existing_dim() == self.dim

    # -- payload helpers ------------------------------------------------------ #
    def _payload(self, record: VectorRecord, owner_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chunk_id": str(record.chunk_id),
            "doc_id": str(record.doc_id or ""),
            "owner_id": str(owner_id),
        }
        for k, v in (record.metadata or {}).items():
            if k in payload:
                continue
            payload[k] = v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
        return payload

    def _owner_filter(self, owner_id: str, doc_ids: list[str] | None = None) -> Any:
        must: list[Any] = [
            qmodels.FieldCondition(
                key="owner_id", match=qmodels.MatchValue(value=str(owner_id))
            )
        ]
        if doc_ids:
            must.append(
                qmodels.FieldCondition(key="doc_id", match=qmodels.MatchAny(any=doc_ids))
            )
        return qmodels.Filter(must=must)

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
        if not self._ensure_collection():
            return 0
        try:
            # Qdrant 的 upsert 本就按 id 覆盖；add 语义下同一 chunk_id 重复写
            # 亦不产生重复行（点 id 由 chunk_id 派生）——与 base 的「写接口幂等」一致。
            self._client.upsert(
                collection_name=self._collection(),
                points=[
                    qmodels.PointStruct(
                        id=_point_id(r.chunk_id),
                        vector=[float(x) for x in r.embedding],
                        payload=self._payload(r, owner_id),
                    )
                    for r in material
                ],
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"qdrant add failed: {exc}") from exc
        return len(material)

    def upsert(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        """Qdrant 原生 upsert（点 id 由 chunk_id 派生），覆写 base 的先删后写。"""
        return self.add(records, owner_id=owner_id)

    def delete(self, chunk_ids: Iterable[str], *, owner_id: str) -> int:
        ids = [c for c in chunk_ids if c]
        if not ids or not owner_id:
            return 0
        if not self._collection_ok():
            return 0
        try:
            self._client.delete(
                collection_name=self._collection(),
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="owner_id",
                                match=qmodels.MatchValue(value=str(owner_id)),
                            ),
                            qmodels.FieldCondition(
                                key="chunk_id",
                                match=qmodels.MatchAny(any=[str(c) for c in ids]),
                            ),
                        ]
                    )
                ),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"qdrant delete failed: {exc}") from exc
        return len(ids)

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
        doc_ids: list[str] = []
        if filters:
            unknown = set(filters) - _SUPPORTED_FILTERS
            if unknown:
                raise VectorFilterUnsupported(
                    f"unsupported metadata filters: {sorted(unknown)}"
                )
            doc_ids = [d for d in (filters.get("doc_ids") or []) if d]
        if not self._collection_ok():
            return []
        limit = max(1, min(int(top_k), MAX_QUERY_TOP_K))
        query_filter = self._owner_filter(owner_id, doc_ids or None)
        try:
            # 新版 qdrant-client 用 query_points；老版只有 search
            qp = getattr(self._client, "query_points", None)
            if callable(qp):
                res = qp(
                    collection_name=self._collection(),
                    query=[float(x) for x in embedding],
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
                points = getattr(res, "points", None)
                if points is None:
                    points = res if isinstance(res, list) else []
            else:  # pragma: no cover - 仅兼容老版本 qdrant-client
                points = self._client.search(
                    collection_name=self._collection(),
                    query_vector=[float(x) for x in embedding],
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"qdrant query failed: {exc}") from exc
        hits: list[VectorHit] = []
        for p in points or []:
            payload = dict(getattr(p, "payload", None) or {})
            score = getattr(p, "score", None)
            if score is None:
                continue
            similarity = max(0.0, min(1.0, float(score)))
            chunk_id = str(payload.pop("chunk_id", "") or "")
            doc_id = str(payload.pop("doc_id", "") or "")
            payload.pop("owner_id", None)
            hits.append(
                VectorHit(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    score=similarity,
                    distance=1.0 - similarity,
                    metadata=payload,
                )
            )
        return hits

    def count(self, *, owner_id: str | None = None) -> int:
        """行数。只读路径：collection 不存在或**维度不符**一律返回 0。

        维度不符不触发重建（重建属写路径职责），与 sqlite-vec 的 ``_table_ok``
        同语义——``count`` 只报告「当前维度下可见的行数」。
        """
        client = self._client_or_none()
        if client is None or not self._collection_ok():
            return 0
        try:
            res = client.count(
                collection_name=self._collection(),
                count_filter=self._owner_filter(owner_id) if owner_id else None,
                exact=True,
            )
            return int(getattr(res, "count", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0

    def list_chunk_ids(self, *, owner_id: str) -> set[str]:
        if not owner_id or not self._collection_ok():
            return set()
        ids: set[str] = set()
        offset: Any = None
        try:
            while True:
                points, offset = self._client.scroll(
                    collection_name=self._collection(),
                    scroll_filter=self._owner_filter(owner_id),
                    limit=1024,
                    offset=offset,
                    with_payload=["chunk_id"],
                    with_vectors=False,
                )
                for p in points or []:
                    payload = getattr(p, "payload", None) or {}
                    cid = payload.get("chunk_id")
                    if cid:
                        ids.add(str(cid))
                if offset is None:
                    break
        except Exception:  # noqa: BLE001
            return ids
        return ids
