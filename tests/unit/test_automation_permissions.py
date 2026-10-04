"""W10-B · 权限门单测（tests/unit/test_automation_permissions.py）。

纪律（任务书 §W10-B.4）：本文件不碰任何真系统调用——权限判定只在内存里发生。
覆盖：
  * 默认 off，一切被拒；
  * readonly / safe / full 三档的解锁边界；
  * full 档必须给 TTL，且 TTL 过期自动回落 off；
  * 每次放行/拒绝都写审计事件（拒绝也写）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from find_yourself.services.automation import (
    MODE_FULL,
    MODE_OFF,
    MODE_READONLY,
    MODE_SAFE,
    AutomationPermissionManager,
)
from find_yourself.services.errors import PermissionDenied


class FixedClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc))


@pytest.fixture()
def events() -> list[tuple[str, dict, bool]]:
    return []


@pytest.fixture()
def mgr(tmp_path, clock, events):
    return AutomationPermissionManager(
        persist_path=tmp_path / "perms.json",
        on_event=lambda action, details, allowed: events.append((action, details, allowed)),
        clock=clock,
    )


def test_default_mode_is_off_and_denies_everything(mgr, events) -> None:
    assert mgr.current().mode == MODE_OFF
    with pytest.raises(PermissionDenied):
        mgr.require("automation.screenshot", min_mode=MODE_READONLY, tool_level=3)
    with pytest.raises(PermissionDenied):
        mgr.require("automation.click", min_mode=MODE_FULL, tool_level=5)
    # 两次拒绝都留下了审计痕迹
    denied = [e for e in events if not e[2]]
    assert len(denied) == 2
    assert all(e[1]["reason"] == "mode_insufficient" for e in denied)


def test_readonly_allows_screen_but_not_injection(mgr, events) -> None:
    mgr.set_mode(MODE_READONLY)
    # 只读类放行
    mgr.require("automation.screenshot", min_mode=MODE_READONLY, tool_level=3)
    mgr.require("automation.list_windows", min_mode=MODE_READONLY, tool_level=3)
    # 注入类仍被拒
    with pytest.raises(PermissionDenied):
        mgr.require("automation.move_mouse", min_mode=MODE_SAFE, tool_level=4)
    with pytest.raises(PermissionDenied):
        mgr.require("automation.click", min_mode=MODE_FULL, tool_level=5)
    allowed = [e for e in events if e[2]]
    assert ("automation.screenshot",) and any(e[0] == "automation.screenshot" for e in allowed)


def test_safe_allows_move_but_not_click(mgr) -> None:
    mgr.set_mode(MODE_SAFE)
    mgr.require("automation.move_mouse", min_mode=MODE_SAFE, tool_level=4)
    with pytest.raises(PermissionDenied):
        mgr.require("automation.click", min_mode=MODE_FULL, tool_level=5)


def test_full_requires_explicit_ttl(mgr) -> None:
    # 不给 TTL 的永久完全控制必须被拒绝
    with pytest.raises(PermissionDenied) as exc:
        mgr.set_mode(MODE_FULL)
    assert "ttl" in exc.value.code.lower() or "ttl" in exc.value.message.lower()
    assert mgr.current().mode == MODE_OFF


def test_full_granted_with_ttl_expires_and_falls_back(tmp_path, clock, events) -> None:
    mgr = AutomationPermissionManager(
        persist_path=tmp_path / "perms.json",
        on_event=lambda a, d, ok: events.append((a, d, ok)),
        clock=clock,
    )
    mgr.set_mode(MODE_FULL, ttl_seconds=60)
    assert mgr.current().mode == MODE_FULL
    mgr.require("automation.click", min_mode=MODE_FULL, tool_level=5)  # 窗口内放行

    # 时间前进 2 分钟，超过 TTL
    clock.t += timedelta(seconds=120)
    # TTL 过期 -> 自动回落 off，click 被拒
    with pytest.raises(PermissionDenied):
        mgr.require("automation.click", min_mode=MODE_FULL, tool_level=5)
    assert mgr.current().mode == MODE_OFF


def test_off_revokes_everything(mgr) -> None:
    mgr.set_mode(MODE_READONLY)
    mgr.require("automation.screenshot", min_mode=MODE_READONLY, tool_level=3)
    mgr.set_mode(MODE_OFF)
    with pytest.raises(PermissionDenied):
        mgr.require("automation.screenshot", min_mode=MODE_READONLY, tool_level=3)


def test_persistence_survives_reload(tmp_path, clock) -> None:
    p = tmp_path / "perms.json"
    m1 = AutomationPermissionManager(persist_path=p, clock=clock)
    m1.set_mode(MODE_READONLY)
    # 新实例从同一文件恢复档位
    m2 = AutomationPermissionManager(persist_path=p, clock=clock)
    assert m2.current().mode == MODE_READONLY


def test_unknown_mode_rejected(mgr) -> None:
    with pytest.raises(PermissionDenied):
        mgr.set_mode("god")  # type: ignore[arg-type]
