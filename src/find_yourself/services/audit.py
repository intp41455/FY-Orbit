"""追加式审计哈希链 —— 兼容垫片。

实体已下沉至 L1 契约单源 :mod:`find_yourself.audit`（ADR-0001 三层模型 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续
``from ..services.audit import ...``。
``runtime``（L2.0）此前正是因此产生 ``runtime → services`` 的 D3R 禁止方向。
新代码请直接 ``from ..audit import ...``。
"""

from ..audit import (
    MESSAGE_ID_KEY,
    AuditService,
    AuditVerifyResult,
    message_id_of,
)

__all__ = [
    "MESSAGE_ID_KEY",
    "AuditService",
    "AuditVerifyResult",
    "message_id_of",
]