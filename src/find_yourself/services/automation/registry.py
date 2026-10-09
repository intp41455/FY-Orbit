"""把 GUI 自动化工具注册进受治理的 harness 网关 (W10-B).

任务书要求：这些能力「注册进 tool_registry，required_level 按 harness 的
L0-L6 分级」，且 harness.py 只经公开 API 使用。项目里有两套注册中心：

* :mod:`find_yourself.services.tool_registry`（P1-05，builtin/http/mcp，无
  required_level 分级）；
* :mod:`find_yourself.skills.harness` 的 ``FunctionCallingGateway``
  （带 ``required_level`` 0..6 与 Actor 鉴权）。

``required_level`` 这个概念只存在于 harness 网关，因此我们经它的公开方法
:meth:`FunctionCallingGateway.register_tool` 注册，**不修改 harness.py 一行**。

档位与 harness 级别的映射（集中在此，避免两处真相）：

=====================  ============  ==================================
工具                    required_lvl  解锁所需权限档位
=====================  ============  ==================================
automation.screenshot   3 (SANDBOX)   readonly
automation.list_windows 3 (SANDBOX)   readonly
automation.move_mouse   4 (ORCHESTRATE) safe
automation.click        5 (SYSTEM_FS)  full（带 TTL）
automation.type_text    5 (SYSTEM_FS)  full（带 TTL）
automation.press_key    5 (SYSTEM_FS)  full（带 TTL）
=====================  ============  ==================================
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import permissions as perm
from .backend import AutomationBackend

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器解析字符串注解
    # 下面 ``_permissions`` / ``get_permissions`` 的注解写的是字符串形式
    # （``"AutomationPermissionManager | None"``），但这个类名在本模块里
    # **从未被导入** —— 运行时注解不求值所以一直没炸，直到有工具调用
    # ``typing.get_type_hints()`` 才会变真 NameError。这里补 TYPE_CHECKING
    # 守卫导入：让类型检查器与ruff F821 都能看到名字，又不在运行时代价。
    from .permissions import AutomationPermissionManager


#: 工具名 -> (描述, required_level, 最低权限档位, 参数 JSON Schema)
def _tool_specs() -> dict[str, dict[str, Any]]:
    return {
        "automation.screenshot": {
            "level": 3,
            "min_mode": perm.MODE_READONLY,
            "description": (
                "Capture the current primary monitor as a PNG frame. "
                "Returns RAW pixels only — no OCR / on-screen understanding. "
                "Feed the image to a vision-capable model; without one the frame is opaque."
            ),
            "schema": {"type": "object", "properties": {}},
        },
        "automation.list_windows": {
            "level": 3,
            "min_mode": perm.MODE_READONLY,
            "description": "Enumerate visible top-level windows (title + geometry). Read-only.",
            "schema": {"type": "object", "properties": {}},
        },
        "automation.move_mouse": {
            "level": 4,
            "min_mode": perm.MODE_SAFE,
            "description": "Move the mouse cursor to absolute screen coordinates (no click).",
            "schema": {
                "type": "object",
                "required": ["x", "y"],
                "properties": {
                    "x": {"type": "integer", "minimum": 0},
                    "y": {"type": "integer", "minimum": 0},
                },
            },
        },
        "automation.click": {
            "level": 5,
            "min_mode": perm.MODE_FULL,
            "description": (
                "Inject a real mouse click at absolute coordinates. "
                "SYSTEM_FS-level; only available in time-limited 'full' mode."
            ),
            "schema": {
                "type": "object",
                "required": ["x", "y"],
                "properties": {
                    "x": {"type": "integer", "minimum": 0},
                    "y": {"type": "integer", "minimum": 0},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "clicks": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            },
        },
        "automation.type_text": {
            "level": 5,
            "min_mode": perm.MODE_FULL,
            "description": (
                "Inject a real keyboard typing of the given text at the focused window. "
                "SYSTEM_FS-level; only available in time-limited 'full' mode."
            ),
            "schema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 2000}},
            },
        },
        "automation.press_key": {
            "level": 5,
            "min_mode": perm.MODE_FULL,
            "description": (
                "Press a single keyboard key (e.g. 'enter', 'esc', 'tab'). "
                "SYSTEM_FS-level; only available in time-limited 'full' mode."
            ),
            "schema": {
                "type": "object",
                "required": ["key"],
                "properties": {"key": {"type": "string", "minLength": 1, "maxLength": 40}},
            },
        },
    }


def register_automation_tools(
    gateway: Any,
    *,
    permissions: perm.AutomationPermissionManager,
    backend: AutomationBackend,
) -> list[str]:
    """把六个 automation.* 工具注册进 ``gateway``（幂等）。

    Returns
    -------
    list[str]
        实际注册的工具名，供启动日志与测试断言。
    """
    specs = _tool_specs()
    registered: list[str] = []

    def _make_handler(name: str, spec: dict[str, Any]):
        min_mode = spec["min_mode"]
        level = spec["level"]

        def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
            # 1) 权限门：不达标直接 403，绝不碰系统。
            permissions.require(name, min_mode=min_mode, tool_level=level)
            # 2) 真调后端（测试里是 fake，不碰鼠标）。
            if name == "automation.screenshot":
                result = backend.screenshot()
                # 不把整张 base64 拷进审计日志（隐私 + 体积），只记摘要。
                permissions.record_executed(
                    name,
                    {
                        "width": result.get("width"),
                        "height": result.get("height"),
                        "note": result.get("note"),
                    },
                )
            elif name == "automation.list_windows":
                wins = backend.list_windows()
                permissions.record_executed(name, {"window_count": len(wins)})
            elif name == "automation.move_mouse":
                result = backend.move_mouse(int(arguments["x"]), int(arguments["y"]))
                permissions.record_executed(name, {"x": result["x"], "y": result["y"]})
            elif name == "automation.click":
                result = backend.click(
                    int(arguments["x"]),
                    int(arguments["y"]),
                    button=arguments.get("button", "left"),
                    clicks=int(arguments.get("clicks", 1)),
                )
                permissions.record_executed(name, result)
            elif name == "automation.type_text":
                result = backend.type_text(arguments["text"])
                # 不把用户输入的原文写进审计（隐私），只记长度。
                permissions.record_executed(name, {"chars": result["chars"]})
            elif name == "automation.press_key":
                result = backend.press_key(arguments["key"])
                permissions.record_executed(name, {"key": result["key"]})
            else:  # pragma: no cover - 防御
                raise ValueError(f"unknown automation tool {name}")
            return result

        return _handler

    for name, spec in specs.items():
        gateway.register_tool(
            name=name,
            description=spec["description"],
            handler=_make_handler(name, spec),
            schema=spec["schema"],
            cost_cents=0,
            required_level=spec["level"],
            builtin=True,
            replace=True,
        )
        registered.append(name)
    return registered


# --- process-wide singletons (wired once at app startup) ---------------------

_permissions: "AutomationPermissionManager | None" = None
_backend: "AutomationBackend | None" = None
_wired = False


def get_permissions() -> "AutomationPermissionManager":
    """进程级权限门单例（状态持久化到 .runtime/automation_permissions.json）。"""
    global _permissions
    if _permissions is None:
        from .permissions import AutomationPermissionManager

        _permissions = AutomationPermissionManager()
    return _permissions


def get_backend() -> "AutomationBackend":
    """进程级系统调用后端单例（缺依赖时方法内才报错）。"""
    global _backend
    if _backend is None:
        from .backend import SystemAutomationBackend

        _backend = SystemAutomationBackend()
    return _backend


def wire_automation_tools() -> list[str]:
    """应用启动时调用一次：把 automation.* 注册进 harness 全局网关单例。

    幂等（register_tool 用 replace=True）。返回注册的工具名，供启动日志与
    挂接断言测试使用。
    """
    global _wired
    from ...skills.harness import gateway

    names = register_automation_tools(
        gateway, permissions=get_permissions(), backend=get_backend()
    )
    _wired = True
    return names

