"""Canonical JSON 与摘要工具 —— 兼容垫片。

实体已下沉至 L1 契约单源 :mod:`find_yourself.hashing`（ADR-0001 三层模型 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续
``from ..services.hasher import ...``。
``runtime``（L2.0）此前正是因此产生 ``runtime → services`` 的 D3R 禁止方向。
新代码请直接 ``from ..hashing import ...``。
"""

from ..hashing import canonical_json, content_hash, digest

__all__ = [
    "canonical_json",
    "content_hash",
    "digest",
]