"""A-能力网关-04：四元组授予/拒绝/撤销/过期 + 默认最小必要。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from find_yourself.db.types import utcnow
from find_yourself.services.capability import CapabilityRequest, GrantSpec
from find_yourself.services.errors import NotFound, PermissionDenied, ValidationFailed


@pytest.fixture()
def fine_broker(broker, owner):
    """切到细粒度档：一切能力按四元组授予，隔离 novice 基线的干扰。"""
    broker.switch_profile("fine", actor=owner)
    return broker


def test_path_prefix_grant_scopes_resource(fine_broker, owner, tmp_path):
    """路径前缀授予：前缀内放行，前缀外拒绝（资源元生效）。"""
    fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="local.workspace.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "workspace" / "docs")),
        actor=owner,
    )
    inside = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "docs" / "note.md")),
        actor=owner,
    )
    outside = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "secrets" / "key.md")),
        actor=owner,
    )
    assert inside.allowed and inside.grant_id
    assert not outside.allowed and outside.code == "default_deny"


def test_domain_wildcard_grant(fine_broker, owner, tmp_path):
    """域名白名单授予：后缀通配命中，未知域名拒绝。"""
    fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="cross_agent.mcp.tool",
                  resource_kind="domain", resource_pattern="*.trusted.io", ttl_seconds=300),
        actor=owner,
    )
    ok = fine_broker.decide(CapabilityRequest("cross_agent.mcp.tool", resource="a.trusted.io"), actor=owner)
    bad = fine_broker.decide(CapabilityRequest("cross_agent.mcp.tool", resource="evil.io"), actor=owner)
    assert ok.allowed
    assert not bad.allowed


def test_process_whitelist_grant(fine_broker, owner):
    """进程白名单授予（L3）：白名单内放行，名单外拒绝。"""
    broker = fine_broker
    broker.set_level("L3", True, actor=owner)
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.process.spawn",
                  resource_kind="process", resource_pattern="python*", ttl_seconds=600),
        actor=owner,
    )
    assert broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner).allowed
    assert not broker.decide(CapabilityRequest("local.process.spawn", resource="cmd.exe"), actor=owner).allowed


def test_ttl_expiry(fine_broker, owner, tmp_path):
    """时限元：绝对过期时间已过 → 授予失效回到默认拒绝。"""
    fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="local.workspace.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "workspace"),
                  expires_at=utcnow() - timedelta(seconds=1)),
        actor=owner,
    )
    res = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "x")),
        actor=owner,
    )
    assert not res.allowed and res.code == "default_deny"


def test_revoke_immediate_effect(fine_broker, owner, tmp_path):
    """撤销即时生效：撤销后同一请求立即回到默认拒绝，并留撤销审计帧。"""
    row = fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="local.workspace.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "workspace"), ttl_seconds=600),
        actor=owner,
    )
    assert fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "a")), actor=owner
    ).allowed
    fine_broker.revoke(row.id, actor=owner)
    res = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "a")), actor=owner
    )
    assert not res.allowed and res.code == "default_deny"
    assert fine_broker.search_audit(owner, capability="local.workspace.read")


def test_revoke_unknown_grant_not_found(fine_broker, owner):
    with pytest.raises(NotFound):
        fine_broker.revoke("cg_missing", actor=owner)


def test_irrevocable_grant_rejects_revoke_but_deep_level_forced_revocable(fine_broker, owner, tmp_path):
    """不可撤回授予：低风险能力允许显式声明；深级别能力强制可撤回。"""
    row = fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="local.workspace.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "workspace"),
                  ttl_seconds=600, revocable=False),
        actor=owner,
    )
    with pytest.raises(PermissionDenied) as ei:
        fine_broker.revoke(row.id, actor=owner)
    assert ei.value.code == "grant_not_revocable"
    # 深级别（L3）授予必须可撤回。
    fine_broker.set_level("L3", True, actor=owner)
    with pytest.raises(ValidationFailed) as ei2:
        fine_broker.grant(
            GrantSpec(subject="owner:owner", capability="local.process.spawn",
                      resource_kind="process", resource_pattern="python*",
                      ttl_seconds=60, revocable=False),
            actor=owner,
        )
    assert ei2.value.code == "deep_grant_must_be_revocable"


def test_deny_grant_overrides_everything(broker, owner, tmp_path):
    """显式拒绝覆盖一切（含 novice 基线放行）——多路取最严的一部分。"""
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.write",
                  resource_kind="path", resource_pattern=str(tmp_path / "artifacts"), effect="deny"),
        actor=owner,
    )
    res = broker.decide(
        CapabilityRequest("local.fs.write", resource=str(tmp_path / "artifacts" / "x.txt")),
        actor=owner,
    )
    assert not res.allowed and res.code == "denied"
    assert any("explicit deny" in r for r in res.reasons)


def test_task_bound_grant(fine_broker, owner, tmp_path):
    """任务绑定：授予只对绑定任务生效（审计可按任务检索的同一维度）。"""
    fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="local.workspace.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "workspace"),
                  ttl_seconds=600, task_id="task-42"),
        actor=owner,
    )
    ok = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "a"), task_id="task-42"),
        actor=owner,
    )
    other = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "a"), task_id="task-43"),
        actor=owner,
    )
    unbound = fine_broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "a")),
        actor=owner,
    )
    assert ok.allowed
    assert not other.allowed and not unbound.allowed


def test_deep_grant_requires_short_ttl(fine_broker, owner):
    """深级别授予强制短时效：无 TTL 拒绝；超上限（24h）拒绝；deny 不受限。"""
    fine_broker.set_level("L3", True, actor=owner)
    with pytest.raises(ValidationFailed) as e1:
        fine_broker.grant(
            GrantSpec(subject="owner:owner", capability="local.process.spawn",
                      resource_kind="process", resource_pattern="python*"),
            actor=owner,
        )
    assert e1.value.code == "deep_grant_requires_ttl"
    with pytest.raises(ValidationFailed) as e2:
        fine_broker.grant(
            GrantSpec(subject="owner:owner", capability="local.process.spawn",
                      resource_kind="process", resource_pattern="python*",
                      ttl_seconds=30 * 24 * 3600),
            actor=owner,
        )
    assert e2.value.code == "deep_grant_ttl_too_long"
    # deny 永远安全：不强制 TTL。
    row = fine_broker.grant(
        GrantSpec(subject="owner:owner", capability="local.process.spawn",
                  resource_kind="process", resource_pattern="*", effect="deny"),
        actor=owner,
    )
    assert row.effect == "deny"


def test_grant_admin_owner_only(broker, agent, tmp_path):
    """agent/服务身份不可授予/撤销/切档/改级别（可审计的另一半：授权变更只有 owner）。"""
    with pytest.raises(PermissionDenied) as e:
        broker.grant(GrantSpec(subject="service:agent-1", capability="local.fs.read", ttl_seconds=60), actor=agent)
    assert e.value.code == "owner_only"
    with pytest.raises(PermissionDenied):
        broker.revoke("cg_x", actor=agent)
    with pytest.raises(PermissionDenied):
        broker.switch_profile("fine", actor=agent)
    with pytest.raises(PermissionDenied):
        broker.set_level("L3", True, actor=agent)


def test_resource_kind_must_match_capability(fine_broker, owner):
    """资源种类与能力类型错配 → 拒绝授予（防止用 domain 授予放宽 fs 能力）。"""
    with pytest.raises(ValidationFailed) as e:
        fine_broker.grant(
            GrantSpec(subject="owner:owner", capability="local.fs.read",
                      resource_kind="domain", resource_pattern="*.io", ttl_seconds=60),
            actor=owner,
        )
    assert e.value.code == "resource_kind_mismatch"


def test_unknown_capability_not_grantable(fine_broker, owner):
    with pytest.raises(ValidationFailed) as e:
        fine_broker.grant(
            GrantSpec(subject="owner:owner", capability="no.such.cap", ttl_seconds=60),
            actor=owner,
        )
    assert e.value.code == "unknown_capability"
