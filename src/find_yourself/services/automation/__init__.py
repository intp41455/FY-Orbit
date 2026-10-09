"""W10-B · GUI 自动化引擎（高风险，严格权限分级）。

Public surface:

* :class:`AutomationPermissionManager` —— 默认全关、三档开关、full 档 TTL 自动回落。
* :class:`SystemAutomationBackend` —— 真实系统调用（懒加载 mss/pyautogui/pygetwindow）。
* :class:`FakeAutomationBackend` —— 测试替身，绝不碰真鼠标。
* :func:`register_automation_tools` —— 经 harness 网关公开 API 注册 ``automation.*``。

绝不默认开启；缺依赖诚实报错；不做伪 OCR。
"""

from .backend import (
    VISION_HONESTY_NOTE,
    AutomationBackend,
    AutomationDepsMissing,
    FakeAutomationBackend,
    SystemAutomationBackend,
)
from .permissions import (
    DEFAULT_FULL_TTL_SECONDS,
    MAX_FULL_TTL_SECONDS,
    MODE_FULL,
    MODE_OFF,
    MODE_ORDER,
    MODE_READONLY,
    MODE_SAFE,
    AutomationMode,
    AutomationPermissionManager,
)
from .registry import get_backend, get_permissions, register_automation_tools, wire_automation_tools

__all__ = [
    "AutomationBackend",
    "AutomationDepsMissing",
    "FakeAutomationBackend",
    "SystemAutomationBackend",
    "VISION_HONESTY_NOTE",
    "DEFAULT_FULL_TTL_SECONDS",
    "MAX_FULL_TTL_SECONDS",
    "MODE_FULL",
    "MODE_OFF",
    "MODE_ORDER",
    "MODE_READONLY",
    "MODE_SAFE",
    "AutomationMode",
    "AutomationPermissionManager",
    "register_automation_tools",
    "wire_automation_tools",
    "get_permissions",
    "get_backend",
]
