"""A-能力网关-01：唯一裁决入口 + 注册即接入 + 默认拒绝。"""

from __future__ import annotations

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.capability import (
    CapabilityRequest,
    CapabilityTypeSpec,
    GrantSpec,
)
from find_yourself.services.capability.types import DOMAIN_CROSS_AGENT
from find_yourself.services.errors import PermissionDenied


def test_novice_baseline_sandbox_read_allowed_without_any_grant(broker, owner, tmp_path):
    """唯一入口放行：novice 档 L1 沙箱读零配置可用（四元组/级别全未配置）。"""
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")),
        actor=owner,
    )
    assert res.allowed and res.code == "allowed" and res.level == "L1"


def test_unknown_capability_default_denied(broker, owner):
    """未注册的能力类型一律拒绝（唯一入口，无旁路兜底）。"""
    res = broker.decide(CapabilityRequest("local.does.not.exist"), actor=owner)
    assert not res.allowed and res.code == "unknown_capability"


def test_enforce_raises_permission_denied_with_reasons(broker, owner, tmp_path):
    """enforce 是 decide 的强制形态：拒绝抛 PermissionDenied 并携带否决原因。"""
    with pytest.raises(PermissionDenied) as ei:
        broker.enforce(
            CapabilityRequest("local.fs.read", resource="C:/Windows/system32/config"),
            actor=owner,
        )
    assert ei.value.code == "denied"
    assert any("path_escape" in r for r in ei.value.message.split(";"))


def test_enforce_returns_decision_on_allow(broker, owner, tmp_path):
    res = broker.enforce(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "b.txt")),
        actor=owner,
    )
    assert res.allowed and res.grant_id is None


def test_new_capability_type_registered_only_plugs_into_pipeline(broker, owner):
    """新增能力类型仅注册即接入：注册 → 授予 → 同一管线裁决，零裁决代码改动。"""
    spec = CapabilityTypeSpec(
        name="cross_agent.custom.channel", domain=DOMAIN_CROSS_AGENT,
        description="自定义通道（测试用）", resource_kind="domain",
    )
    broker.register_capability_type(spec)
    # 未授予前：跨 agent 能力默认拒绝（novice 无隐式放行）。
    before = broker.decide(CapabilityRequest("cross_agent.custom.channel", resource="relay.example.com"), actor=owner)
    assert not before.allowed and before.code == "default_deny"
    # 注册即有资格被四元组授予；授予后同一入口放行。
    broker.grant(
        GrantSpec(subject="owner:owner", capability="cross_agent.custom.channel",
                  resource_kind="domain", resource_pattern="*.example.com", ttl_seconds=300),
        actor=owner,
    )
    after = broker.decide(CapabilityRequest("cross_agent.custom.channel", resource="relay.example.com"), actor=owner)
    assert after.allowed and after.grant_id


def test_duplicate_capability_registration_rejected(broker):
    """同名注册拒绝（防静默换义），显式 replace 才允许覆盖。"""
    spec = CapabilityTypeSpec(name="dup.type", domain=DOMAIN_CROSS_AGENT, description="x")
    broker.register_capability_type(spec)
    with pytest.raises(ValueError):
        broker.register_capability_type(CapabilityTypeSpec(name="dup.type", domain=DOMAIN_CROSS_AGENT, description="y"))
    broker.register_capability_type(CapabilityTypeSpec(name="dup.type", domain=DOMAIN_CROSS_AGENT, description="z"), replace=True)


def test_local_and_cross_agent_share_same_entry_and_audit(broker, owner, agent, tmp_path):
    """本机能力与跨 agent 能力走同一个 decide()：审计帧同链、action 同前缀。"""
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "c.txt")), actor=owner)
    broker.decide(CapabilityRequest("cross_agent.mcp.tool", resource="api.example.com"), actor=agent)
    frames = broker.search_audit(owner)
    actions = {f.action for f in frames}
    assert actions == {"capability.decision"}


def test_service_actor_subject_mismatch_rejected(broker, agent):
    """请求主体与凭据不一致 → 拒绝（agent 不能冒名）。"""
    res = broker.decide(CapabilityRequest("local.fs.read", subject="owner:someone-else"), actor=agent)
    assert not res.allowed
    assert any("subject mismatch" in r for r in res.reasons)


def test_expired_service_credential_rejected(broker, tmp_path):
    """过期的 service 凭据在网关被否决（RBAC 收编语义）。"""
    from datetime import timedelta

    from find_yourself.db.types import utcnow
    actor = Actor.service("short-lived", "agent", allowed_tools=["local.fs.read"],
                          expires_at=utcnow() - timedelta(seconds=1))
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "d.txt")),
        actor=actor,
    )
    assert not res.allowed and any("expired" in r for r in res.reasons)
