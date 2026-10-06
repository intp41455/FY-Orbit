"""统一能力网关（补齐包1）测试夹具。

隔离原则：
* LevelRegistry / ProfileManager / AutomationPermissionManager 的本地 JSON
  持久化一律落在 ``tmp_path``，绝不读写仓库根 ``.runtime/``；
* 路径边界根也全部落在 ``tmp_path`` 下的 sandbox / artifacts / workspace 目录；
* DB 复用顶层 conftest 的内存 SQLite ``session``（审计哈希链同库同验）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import find_yourself.db.models  # noqa: F401  (确保 audit_events 等表注册)
import find_yourself.services.capability  # noqa: F401  (确保 capability_grants 注册进 Base.metadata)
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.automation.permissions import AutomationPermissionManager
from find_yourself.services.capability import (
    CapabilityBroker,
    GrantStore,
    LevelRegistry,
    ProfileManager,
)

OWNER_ID = "owner"


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner(OWNER_ID)


@pytest.fixture()
def agent() -> Actor:
    """被 RBAC 作用域约束的 service 身份（A-Agent运行时-03 的四路之一）。"""
    return Actor.service(
        "agent-1", "agent",
        allowed_tools=["cross_agent.mcp.tool", "local.fs.read"],
        domains=["api.example.com"],
    )


@pytest.fixture()
def automation_clock():
    """可推进的时钟（验证 automation full 档 TTL 到期回落）。"""
    state = {"now": datetime.now(timezone.utc)}

    def clock() -> datetime:
        return state["now"]

    def advance(seconds: float) -> None:
        state["now"] = state["now"] + timedelta(seconds=seconds)

    clock.advance = advance  # type: ignore[attr-defined]
    return clock


@pytest.fixture()
def automation_events() -> list:
    return []


@pytest.fixture()
def automation(automation_events, tmp_path, automation_clock) -> AutomationPermissionManager:
    """automation 四档权限门（收编路），审计回调进内存列表。"""
    return AutomationPermissionManager(
        persist_path=tmp_path / "automation.json",
        on_event=lambda action, details, ok: automation_events.append((action, details, ok)),
        clock=automation_clock,
    )


@pytest.fixture()
def broker(session, tmp_path, automation) -> CapabilityBroker:
    return CapabilityBroker(
        session=session,
        audit=AuditService(session),
        levels=LevelRegistry(persist_path=tmp_path / "levels.json"),
        grants=GrantStore(session),
        profiles=ProfileManager(persist_path=tmp_path / "profile.json"),
        automation_permissions=automation,
        sandbox_roots=[tmp_path / "sandbox", tmp_path / "artifacts"],
        workspace_roots=[tmp_path / "workspace"],
    )
