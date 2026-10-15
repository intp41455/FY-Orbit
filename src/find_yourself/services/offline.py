"""默认离线运行架构（A-离线优先-01/03）—— 兼容垫片。

实体已下沉至 L1 契约单源 :mod:`find_yourself.offline`（ADR-0001 三层模型 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续
``from ..services.offline import ...``。
``runtime``（L2.0）此前正是因此产生 ``runtime → services`` 的 D3R 禁止方向。
新代码请直接 ``from ..offline import ...``。
"""

from ..offline import (
    OFFLINE_MODE_ENV,
    OfflineStatus,
    OfflineUnavailable,
    clear_cache,
    is_offline,
    offline_mode_enabled,
    probe_network,
    remote_block_reason,
    require_remote,
    status,
)

__all__ = [
    "OFFLINE_MODE_ENV",
    "OfflineStatus",
    "OfflineUnavailable",
    "clear_cache",
    "is_offline",
    "offline_mode_enabled",
    "probe_network",
    "remote_block_reason",
    "require_remote",
    "status",
]
