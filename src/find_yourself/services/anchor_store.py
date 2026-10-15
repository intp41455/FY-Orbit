"""审计锚点的独立留存存储 —— 兼容垫片。

实体已下沉至 L1 契约单源 :mod:`find_yourself.anchors`（ADR-0001 三层模型 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续
``from ..services.anchor_store import ...``。
新代码请直接 ``from ..anchors import ...``。
"""

from ..anchors import (
    DEFAULT_ANCHOR_DIR,
    RETIREMENT_EVIDENCE_KEY,
    AnchorRecord,
    AnchorStore,
    FileAnchorStore,
    NullAnchorStore,
    S3AnchorStore,
    anchor_key,
)

__all__ = [
    "DEFAULT_ANCHOR_DIR",
    "RETIREMENT_EVIDENCE_KEY",
    "AnchorRecord",
    "AnchorStore",
    "FileAnchorStore",
    "NullAnchorStore",
    "S3AnchorStore",
    "anchor_key",
]
