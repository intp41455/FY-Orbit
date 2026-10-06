"""向量后端引擎无关抽象（补齐包2 · A-向量库-01）。

所有后端必须遵守的契约：

* **owner 隔离在后端内部强制**——``add/upsert/delete/query/count`` 都必须收
  ``owner_id`` 且后端自行落过滤（本仓检索铁律「授权谓词先于排序」）；空
  ``owner_id`` 抛 ``ValidationFailed``，与 ``search.py`` 一致。
* ``query`` 返回 :class:`VectorHit`：``score`` 为相似度（[0,1]，越大越近），
  ``distance`` 为后端原始距离，``metadata`` 携带后端可给的附加信息。
* ``filters`` 只定义一个元数据键：``doc_ids: Sequence[str]``（切片所属文档
  白名单，等价检索层的 ``document_ids`` 作用域）。其他键抛
  :class:`VectorFilterUnsupported`——宁可报错也不静默忽略（不假装过滤了）。
* 写接口幂等：同一 ``chunk_id`` 重复 ``upsert`` 不产生重复行。
* 不可用（缺依赖/缺服务）时 ``is_available()`` 返回 False，读接口返回空、
  **不抛异常**——降级是常态路径（与 FTS5→LIKE 同哲学）；但调用方传参错误
  （维度不符、非法 filter）必须抛异常，那是 bug 不是降级。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ....services.errors import ValidationFailed


class VectorStoreError(RuntimeError):
    """向量后端写入/查询失败（基础设施级，调用方决定降级或上抛）。"""


class VectorFilterUnsupported(VectorStoreError):
    """请求了本后端不支持的元数据过滤键（显式报错，不静默忽略）。"""


@dataclass(frozen=True)
class VectorRecord:
    """待写入向量后端的一条记录（一个切片的嵌入）。"""

    chunk_id: str
    doc_id: str
    embedding: Sequence[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorHit:
    """向量近邻查询的一条命中。"""

    chunk_id: str
    doc_id: str
    #: 相似度分数（[0,1]，越大越近；cosine 距离换算 1 - distance 并截断）
    score: float
    #: 后端原始距离（越小越近）
    distance: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    """向量后端接口：add / upsert / delete / query + 元数据过滤 + owner 隔离。"""

    #: 注册名（registry key）
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """后端在当前进程/当前会话上是否可用（缺依赖、扩展加载失败 → False）。"""

    @abstractmethod
    def add(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        """写入记录，返回成功写入条数。后端不可用 → 0（降级），参数错误 → 异常。"""

    def upsert(self, records: Iterable[VectorRecord], *, owner_id: str) -> int:
        """按 chunk_id 先删后写（默认实现；后端可用原生 upsert 时覆写）。"""
        material = list(records)
        if material:
            self.delete([r.chunk_id for r in material], owner_id=owner_id)
        return self.add(material, owner_id=owner_id)

    @abstractmethod
    def delete(self, chunk_ids: Iterable[str], *, owner_id: str) -> int:
        """删除 owner 名下指定 chunk 的向量行，返回实际删除条数。"""

    @abstractmethod
    def query(
        self,
        embedding: Sequence[float],
        *,
        owner_id: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        """owner 隔离的向量近邻，按 score 降序；不支持过滤键抛 VectorFilterUnsupported。"""

    @abstractmethod
    def count(self, *, owner_id: str | None = None) -> int:
        """行数（owner 维度对账用）。"""

    @abstractmethod
    def list_chunk_ids(self, *, owner_id: str) -> set[str]:
        """owner 名下已索引的 chunk_id 集合（增量索引对账 + stale 清理用）。"""
