"""A-能力网关-01/02/04：多路裁决取最严 + 既有门收编（RBAC / automation 四档 / 路径边界）。"""

from __future__ import annotations

from datetime import timedelta

from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.capability import CapabilityRequest, GrantSpec


def test_multi_gate_strictest_wins_rbac_over_grant(broker, tmp_path):
    """RBAC 与授予竞争取最严：service 不在 allowed_tools，即使有 allow 授予也拒绝。"""
    owner = Actor.owner("owner")
    scoped = Actor.service("agent-ok", "agent", allowed_tools=["local.fs.read"])
    unscoped = Actor.service("agent-no", "agent", allowed_tools=["other.tool"])
    for actor in (scoped, unscoped):
        broker.grant(
            GrantSpec(subject=f"service:{actor.service_id}", capability="local.fs.read",
                      resource_kind="path", resource_pattern=str(tmp_path / "artifacts"), ttl_seconds=600),
            actor=owner,
        )
    resource = str(tmp_path / "artifacts" / "in.txt")
    assert broker.decide(CapabilityRequest("local.fs.read", resource=resource), actor=scoped).allowed
    denied = broker.decide(CapabilityRequest("local.fs.read", resource=resource), actor=unscoped)
    assert not denied.allowed
    assert any("allowed_tools" in r for r in denied.reasons)


def test_multi_gate_rbac_domain_binding_for_cross_agent(broker, agent):
    """跨 agent 域资源受 RBAC bound_domains 约束：绑定域内放行，域外 RBAC 一票否决。"""
    owner = Actor.owner("owner")
    broker.grant(
        GrantSpec(subject="service:agent-1", capability="cross_agent.mcp.tool",
                  resource_kind="domain", resource_pattern="*.example.com", ttl_seconds=600),
        actor=owner,
    )
    ok = broker.decide(CapabilityRequest("cross_agent.mcp.tool", resource="api.example.com"), actor=agent)
    assert ok.allowed
    out_of_domain = broker.decide(CapabilityRequest("cross_agent.mcp.tool", resource="evil.example.org"), actor=agent)
    assert not out_of_domain.allowed
    assert any("bound_domains" in r for r in out_of_domain.reasons)


def test_automation_mode_gate_adopted_off_blocks_even_with_level_enabled(broker, owner, automation):
    """automation 四档收编①：档位 off 时，即使 L4 已开，桌面观察仍被拒。"""
    broker.set_level("L4", True, actor=owner)
    res = broker.decide(CapabilityRequest("local.desktop.observe"), actor=owner)
    assert not res.allowed
    assert any("automation" in r.lower() for r in res.reasons)


def test_automation_mode_gate_adopted_readonly_allows_observe_only(broker, owner, automation):
    """automation 四档收编②：readonly 放行观察；键鼠注入（full 类）仍被拒。"""
    broker.set_level("L4", True, actor=owner)
    automation.set_mode("readonly")
    assert broker.decide(CapabilityRequest("local.desktop.observe"), actor=owner).allowed
    res = broker.decide(CapabilityRequest("local.desktop.full"), actor=owner)
    assert not res.allowed and any("automation" in r.lower() for r in res.reasons)


def test_automation_full_mode_ttl_enforced_through_broker(broker, owner, automation, automation_clock):
    """automation 四档收编③：full 注入必须带 TTL，到期自动回落 off（经网关可见）。"""
    broker.set_level("L4", True, actor=owner)
    automation.set_mode("full", ttl_seconds=120)
    assert broker.decide(CapabilityRequest("local.desktop.full"), actor=owner).allowed
    # 时钟推进 121s：full 档 TTL 到期回落 off，网关随之拒绝。
    automation_clock.advance(121)
    res = broker.decide(CapabilityRequest("local.desktop.full"), actor=owner)
    assert not res.allowed and any("automation" in r.lower() for r in res.reasons)


def test_path_boundary_no_bypass_by_level_or_grant(broker, owner, tmp_path):
    """A-Claw安全-01：越级授予/更高级别都不能把路径带出允许根（.. 与绝对路径）。"""
    # 越级到 L2 的授予存在，但请求路径在工作区之外 → 路径边界先否决。
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.read", resource_kind="path",
                  resource_pattern=str(tmp_path), level="L2", escalation=True, ttl_seconds=600),
        actor=owner,
    )
    outside = broker.decide(
        CapabilityRequest("local.fs.read", resource="C:/Windows/System32/drivers/etc/hosts", level="L2"),
        actor=owner,
    )
    assert not outside.allowed and any("path_escape" in r for r in outside.reasons)
    # .. 穿越形态同样被折叠后拦截。
    traversal = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "workspace" / ".." / ".." / "secret.txt"), level="L2"),
        actor=owner,
    )
    assert not traversal.allowed and any("path_escape" in r for r in traversal.reasons)
    # L1 能力不能借 sandbox 根之外的相对路径逃逸。
    l1_escape = broker.decide(
        CapabilityRequest("local.fs.read", resource="../outside.txt"),
        actor=owner,
    )
    assert not l1_escape.allowed


def test_l1_path_inside_sandbox_allowed_but_workspace_path_rejected_at_l1(broker, owner, tmp_path):
    """L1 与 L2 的边界差异：L1 只认沙箱根；L2 认工作区根（含沙箱根）。"""
    assert broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "sandbox" / "run.txt")), actor=owner
    ).allowed
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "workspace" / "note.md")), actor=owner
    )
    assert not res.allowed and any("path_escape" in r for r in res.reasons)
    # L2 能力在工作区内合法。
    assert broker.decide(
        CapabilityRequest("local.workspace.read", resource=str(tmp_path / "workspace" / "note.md")), actor=owner
    ).allowed


def test_deny_wins_over_all_allow_paths(broker, owner, agent, tmp_path):
    """多路取最严总纲：novice 基线 + RBAC + 授予都放行时，一条 deny 即否决。"""
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "artifacts"), effect="deny"),
        actor=owner,
    )
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "z.txt")), actor=owner
    )
    assert not res.allowed and res.code == "denied"


def test_expired_rbac_credential_is_strictest(broker, tmp_path):
    """过期 service 凭据即使四元组有效也被否决（RBAC 输入的一票否决）。"""
    owner = Actor.owner("owner")
    actor = Actor.service("agent-x", "agent", allowed_tools=["local.fs.read"],
                          expires_at=utcnow() + timedelta(seconds=3600))
    broker.grant(
        GrantSpec(subject="service:agent-x", capability="local.fs.read",
                  resource_kind="path", resource_pattern=str(tmp_path / "artifacts"), ttl_seconds=600),
        actor=owner,
    )
    resource = str(tmp_path / "artifacts" / "ok.txt")
    assert broker.decide(CapabilityRequest("local.fs.read", resource=resource), actor=actor).allowed
    # 凭据过期（TTL=0 已过）。
    expired = Actor.service("agent-x", "agent", allowed_tools=["local.fs.read"],
                            expires_at=utcnow() - timedelta(seconds=1))
    res = broker.decide(CapabilityRequest("local.fs.read", resource=resource), actor=expired)
    assert not res.allowed and any("expired" in r for r in res.reasons)
