"""A-能力网关-05：授权档位切换（novice ⇄ fine），切档提示与回退。"""

from __future__ import annotations

import pytest

from find_yourself.services.capability import CapabilityRequest, GrantSpec


def test_default_profile_is_novice_and_zero_config_usable(broker, owner, tmp_path):
    """零配置下载即用：全新安装 novice 档，L1/L2 本机能力无需任何授予即可用。"""
    assert broker.current_profile() == "novice"
    assert broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner
    ).allowed
    assert broker.decide(
        CapabilityRequest("local.workspace.write", resource=str(tmp_path / "workspace" / "b.txt")), actor=owner
    ).allowed
    # 深级别在 novice 档也不会自动放开。
    assert not broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner).allowed


def test_switch_to_fine_requires_explicit_grants(broker, owner, tmp_path):
    """切细粒度档：零配置基线整体收回，一切按四元组授予；通知带提示与回退信息。"""
    assert broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner
    ).allowed
    notice = broker.switch_profile("fine", actor=owner)
    assert notice["changed"] and notice["from"] == "novice" and notice["to"] == "fine"
    assert notice["previous"] == "novice" and notice["hint"]
    res = broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner
    )
    assert not res.allowed and res.code == "default_deny"
    # 授予后恢复可用（资源元仍约束）。
    broker.grant(
        GrantSpec(subject="owner:owner", capability="local.fs.read", resource_kind="path",
                  resource_pattern=str(tmp_path / "artifacts"), ttl_seconds=600),
        actor=owner,
    )
    assert broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner
    ).allowed


def test_fine_profile_does_not_relax_deep_levels_or_cross_agent(broker, owner, tmp_path):
    """fine 档不放大任何东西：novice 里被拒的深级别/跨 agent 在 fine 里依旧默认拒绝。"""
    broker.switch_profile("fine", actor=owner)
    assert not broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner).allowed
    assert not broker.decide(
        CapabilityRequest("cross_agent.a2a.skill", resource="peer.example.net"), actor=owner
    ).allowed


def test_revert_profile_restores_novice_baseline(broker, owner, tmp_path):
    """一键回退：fine → novice，零配置基线恢复；无历史时回退报错。"""
    broker.switch_profile("fine", actor=owner)
    notice = broker.revert_profile(actor=owner)
    assert notice["to"] == "novice" and notice["changed"]
    assert broker.decide(
        CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner
    ).allowed
    with pytest.raises(ValueError):
        broker.revert_profile(actor=owner)


def test_switch_to_same_profile_is_noop_notice(broker, owner):
    notice = broker.switch_profile("novice", actor=owner)
    assert not notice["changed"] and notice["hint"]


def test_profile_changes_audited(broker, owner):
    """切档与回退入审计哈希链（capability.profile_change）。"""
    broker.switch_profile("fine", actor=owner)
    broker.revert_profile(actor=owner)
    frames = broker.search_audit(owner)
    assert any(f.action == "capability.profile_change" for f in frames)
    assert broker.verify_audit_chain().ok
