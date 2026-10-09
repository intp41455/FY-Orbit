"""W10-B · 工具注册与权限门集成单测（tests/unit/test_automation_registry.py）。

纪律：全程使用 :class:`FakeAutomationBackend`，绝不触碰真鼠标键盘。
覆盖：
  * 六个 automation.* 工具都注册进独立 harness 网关实例，required_level 正确；
  * off 档 -> 工具调用被拒且 fake 后端零调用；
  * readonly 档 -> 截图真返回帧，注入类被拒；
  * full 档 -> 点击真执行；
  * 截图帧带视觉诚实提示（不做 OCR）；
  * 审计记录在放行/拒绝时都产生。
"""

from __future__ import annotations

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.automation import (
    MODE_FULL,
    MODE_READONLY,
    VISION_HONESTY_NOTE,
    AutomationPermissionManager,
    FakeAutomationBackend,
    register_automation_tools,
)
from find_yourself.services.errors import PermissionDenied
from find_yourself.skills.harness import FunctionCallingGateway

OWNER = Actor.owner("automation-test-owner")


@pytest.fixture()
def gateway():
    # 用全新实例，不污染模块级 gateway 单例（其它测试依赖它的默认工具集）。
    return FunctionCallingGateway()


@pytest.fixture()
def events():
    return []


@pytest.fixture()
def stack(tmp_path, events, gateway):
    perms = AutomationPermissionManager(
        persist_path=tmp_path / "perms.json",
        on_event=lambda a, d, ok: events.append((a, d, ok)),
    )
    backend = FakeAutomationBackend()
    names = register_automation_tools(gateway, permissions=perms, backend=backend)
    return perms, backend, names


def test_all_six_tools_registered_with_correct_levels(gateway, stack) -> None:
    perms, backend, names = stack
    assert sorted(names) == [
        "automation.click",
        "automation.list_windows",
        "automation.move_mouse",
        "automation.press_key",
        "automation.screenshot",
        "automation.type_text",
    ]
    listed = {t["name"]: t for t in gateway.list_tools()}
    assert listed["automation.screenshot"]["required_level"] == 3
    assert listed["automation.list_windows"]["required_level"] == 3
    assert listed["automation.move_mouse"]["required_level"] == 4
    assert listed["automation.click"]["required_level"] == 5
    assert listed["automation.type_text"]["required_level"] == 5
    assert listed["automation.press_key"]["required_level"] == 5


def test_off_mode_blocks_calls_and_never_touches_backend(gateway, stack) -> None:
    perms, backend, names = stack
    # off（默认）：截图工具经 gateway 调用被拒，且 fake 后端零调用。
    # 注：harness.gateway 会把 handler 抛出的 PermissionDenied 包装成
    # ValidationFailed（公开 API 的既有行为，本任务不改 harness.py），
    # 因此这里断言被包装消息里保留了权限门的人类可读原因。
    with pytest.raises(Exception) as exc:
        gateway.invoke("automation.screenshot", {}, OWNER)
    assert "requires GUI automation mode" in str(exc.value)
    assert backend.calls == [], "permission gate must stop before any system call"


def test_readonly_screenshot_runs_but_click_blocked(gateway, stack) -> None:
    perms, backend, names = stack
    perms.set_mode(MODE_READONLY)

    receipt = gateway.invoke("automation.screenshot", {}, OWNER)
    assert receipt["result"]["image_format"] == "png"
    # 诚实边界：原始像素帧带明确提示，不做 OCR。
    assert receipt["result"]["note"] == VISION_HONESTY_NOTE
    assert any(c["op"] == "screenshot" for c in backend.calls)

    # readonly 档不允许注入：点击被拒，且 fake 后端没有 click 记录。
    with pytest.raises(Exception):
        gateway.invoke("automation.click", {"x": 10, "y": 20}, OWNER)
    assert not any(c["op"] == "click" for c in backend.calls)


def test_full_mode_runs_click(gateway, stack) -> None:
    perms, backend, names = stack
    perms.set_mode(MODE_FULL, ttl_seconds=600)
    receipt = gateway.invoke("automation.click", {"x": 5, "y": 6, "clicks": 1}, OWNER)
    assert receipt["result"]["action"] == "click"
    assert receipt["result"]["x"] == 5
    assert any(c["op"] == "click" for c in backend.calls)


def test_type_text_does_not_leak_original_into_audit(gateway, stack, events) -> None:
    perms, backend, names = stack
    perms.set_mode(MODE_FULL, ttl_seconds=600)
    secret = "my-password-123"
    gateway.invoke("automation.type_text", {"text": secret}, OWNER)
    # 审计只记字符数，不记原文（隐私边界）。
    audit_text = str(events)
    assert secret not in audit_text
    assert any("chars" in str(e[1]) for e in events)


def test_permission_gate_itself_raises_403_directly() -> None:
    # 绕过 gateway 的异常包装，直接验证权限门本身抛 403 PermissionDenied。
    import pathlib
    import tempfile

    p = pathlib.Path(tempfile.mkdtemp()) / "p.json"
    pm = AutomationPermissionManager(persist_path=p)
    with pytest.raises(PermissionDenied) as exc:
        pm.require("automation.click", min_mode=MODE_FULL, tool_level=5)
    assert exc.value.http_status == 403
