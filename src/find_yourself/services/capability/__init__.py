"""统一能力网关 (Capability Broker) —— 补齐包1 A-能力网关-01~06。

公开面（施工约定：本包是能力裁决的唯一出口）::

    from find_yourself.services.capability import (
        CapabilityBroker,          # 唯一裁决入口（decide/enforce）+ 管理面
        CapabilityRequest,         # 能力调用请求（本机 + 跨 agent 同一表达）
        CapabilityTypeSpec,        # 能力类型（注册即接入）
        GrantSpec,                 # 四元组授予（能力×资源×时限×可撤回）
        LevelRegistry,             # 五级分级开关
        ProfileManager,            # 授权档位（novice/fine）
        build_capability_broker,   # 从 Settings 组装（api/deps 用）
    )

设计四维：深（五级）× 全（类型注册表）× 细（四元组）× 可审计（哈希链）。
与 A-Agent运行时-03 四路权限竞争裁决同域合并；既有门收编为输入，不建第二套权限系统。
"""

from .broker import CapabilityBroker, build_capability_broker
from .grants import CapabilityGrantRow, GrantSpec, GrantStore
from .levels import (
    LEVEL_IDS,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    LEVEL_L4,
    LEVEL_L5,
    LEVEL_ORDER,
    LEVEL_SPECS,
    LevelRegistry,
)
from .profiles import PROFILE_FINE, PROFILE_NOVICE, ProfileManager
from .types import (
    CapabilityRequest,
    CapabilityTypeRegistry,
    CapabilityTypeSpec,
    Decision,
    PathEscape,
    Verdict,
)

__all__ = [
    "CapabilityBroker",
    "build_capability_broker",
    "CapabilityGrantRow",
    "GrantSpec",
    "GrantStore",
    "LEVEL_IDS",
    "LEVEL_ORDER",
    "LEVEL_SPECS",
    "LEVEL_L1",
    "LEVEL_L2",
    "LEVEL_L3",
    "LEVEL_L4",
    "LEVEL_L5",
    "LevelRegistry",
    "PROFILE_FINE",
    "PROFILE_NOVICE",
    "ProfileManager",
    "CapabilityRequest",
    "CapabilityTypeRegistry",
    "CapabilityTypeSpec",
    "Decision",
    "PathEscape",
    "Verdict",
]
