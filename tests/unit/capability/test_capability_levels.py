"""A-能力网关-02：五级分级、独立开关、越级拒绝、一键降级。"""

from __future__ import annotations

import pytest

from find_yourself.services.capability import CapabilityRequest, GrantSpec
from find_yourself.services.capability.levels import LEVEL_IDS, LEVEL_SPECS


def test_five_levels_defaults_and_manifests(broker):
    """默认 L1/L2 开、L3/L4/L5 关；每级有名称/能力清单/风险提示；L5 可选默认关。"""
    desc = {d["id"]: d for d in broker.describe_levels()}
    assert LEVEL_IDS == ("L1", "L2", "L3", "L4", "L5")
    assert desc["L1"]["enabled"] and desc["L2"]["enabled"]
    assert not desc["L3"]["enabled"] and not desc["L4"]["enabled"] and not desc["L5"]["enabled"]
    for level_id, d in desc.items():
        assert d["name"] and d["risk_notes"]
        assert d["capabilities"], f"{level_id} 必须声明能力清单"
    assert desc["L5"]["name"].startswith("驱动级")
    # 级别能力清单与类型注册表一致（避免两处真相的漂移）。
    for spec in LEVEL_SPECS["L4"].capabilities:
        assert broker.types.get(spec) is not None


def test_level_independent_toggle(broker, owner):
    """每级独立开关：开 L3 只解锁 L3；L4 仍被级别开关挡住。"""
    broker.set_level("L3", True, actor=owner)
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.process.spawn",
                  resource_kind="process", resource_pattern="python*", ttl_seconds=600),
        actor=owner,
    )
    assert broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner).allowed
    # L4 未开：即使 automation 档位齐全，级别 kill-switch 也先否决。
    res = broker.decide(CapabilityRequest("local.desktop.observe"), actor=owner)
    assert not res.allowed
    assert any("L4 is disabled" in r for r in res.reasons)


def test_level_kill_switch_blocks_granted_deep_capability(broker, owner):
    """级别开关是纯否决门：已授予的深级别能力在级别关闭后立即失效。"""
    broker.set_level("L3", True, actor=owner)
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.process.spawn",
                  resource_kind="process", resource_pattern="python*", ttl_seconds=600),
        actor=owner,
    )
    assert broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner).allowed
    broker.set_level("L3", False, actor=owner)
    res = broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner)
    assert not res.allowed and any("disabled" in r for r in res.reasons)


def test_escalation_requires_explicit_grant(broker, owner, tmp_path):
    """越级（L1 能力 → L2 触达工作区）：无显式越级授予 → 拒绝；有 → 放行。"""
    resource = str(tmp_path / "workspace" / "report.md")
    denied = broker.decide(CapabilityRequest("local.fs.read", resource=resource, level="L2"), actor=owner)
    assert not denied.allowed and denied.escalated
    assert any("escalation" in r for r in denied.reasons) or denied.code == "default_deny"
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.read", resource_kind="path",
                  resource_pattern=str(tmp_path / "workspace"), level="L2",
                  escalation=True, ttl_seconds=600),
        actor=owner,
    )
    allowed = broker.decide(CapabilityRequest("local.fs.read", resource=resource, level="L2"), actor=owner)
    assert allowed.allowed and allowed.grant_id and allowed.escalated


def test_escalation_still_blocked_by_level_kill_switch(broker, owner, tmp_path):
    """越级授予不能绕过级别开关：L4 关闭时，越级到 L4 的请求仍被否决。"""
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.read", resource_kind="path",
                  resource_pattern=str(tmp_path / "workspace"), level="L4",
                  escalation=True, ttl_seconds=600),
        actor=owner,
    )
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "workspace" / "x"), level="L4"),
        actor=owner,
    )
    assert not res.allowed and any("L4 is disabled" in r for r in res.reasons)


def test_plain_grant_cannot_satisfy_escalated_request(broker, owner, tmp_path):
    """普通授予（非 escalation）不满足越级请求——越级必须显式声明。"""
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.read", resource_kind="path",
                  resource_pattern=str(tmp_path / "workspace"), ttl_seconds=600),
        actor=owner,
    )
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "workspace" / "x"), level="L2"),
        actor=owner,
    )
    assert not res.allowed


def test_degrade_to_closes_above_and_revokes_deep_grants(broker, owner):
    """一键降级：关闭目标级别以上全部级别 + 回收其上的可撤回授予。"""
    broker.set_level("L3", True, actor=owner)
    broker.set_level("L4", True, actor=owner)
    g3 = broker.grant(
        GrantSpec(subject="owner:owner", capability="local.process.spawn",
                  resource_kind="process", resource_pattern="python*", ttl_seconds=600),
        actor=owner,
    )
    # CLI driver 通道（跨 agent 但落地在 L3）：降级同样回收。
    g4 = broker.grant(
        GrantSpec(subject="owner:owner", capability="cross_agent.cli.exec",
                  resource_kind="process", resource_pattern="agent-cli*", ttl_seconds=600),
        actor=owner,
    )
    result = broker.degrade_to("L2", actor=owner)
    assert result["closed_levels"] == ["L3", "L4"]
    assert set(result["revoked_grants"]) == {g3.id, g4.id}
    desc = {d["id"]: d for d in broker.describe_levels()}
    assert not desc["L3"]["enabled"] and not desc["L4"]["enabled"] and desc["L2"]["enabled"]
    # 回收后，原本放行的深能力立即回到默认拒绝。
    res = broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner)
    assert not res.allowed
    active_ids = {g.id for g in broker.list_grants()}
    assert g3.id not in active_ids and g4.id not in active_ids


def test_level_toggle_persisted_to_isolated_file(broker, owner, tmp_path):
    """级别开关持久化到隔离 JSON（tmp_path），重启语义可用；未知级别拒绝。"""
    from find_yourself.services.capability import LevelRegistry
    from find_yourself.services.errors import ValidationFailed

    broker.set_level("L3", True, actor=owner)
    reloaded = LevelRegistry(persist_path=tmp_path / "levels.json")
    assert reloaded.enabled("L3")
    with pytest.raises(ValidationFailed):
        broker.set_level("L9", True, actor=owner)
