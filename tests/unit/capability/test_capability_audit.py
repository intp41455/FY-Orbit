"""A-能力网关-06：审计入链、三维检索、可逆动作回滚、防篡改 + 配置/迁移接线。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.capability import CapabilityRequest, GrantSpec
from find_yourself.services.errors import PermissionDenied


def test_every_decision_written_to_hash_chain(broker, owner, tmp_path):
    """放行与拒绝都进同一哈希链；链校验通过（capability.* 与全链同验）。"""
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner)
    broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner)  # 拒绝
    res = broker.verify_audit_chain()
    assert res.ok and res.checked >= 2
    frames = broker.search_audit(owner)
    decisions = {f.details["decision"] for f in frames if f.action == "capability.decision"}
    assert decisions == {"allowed", "denied"}


def test_search_by_capability_and_decision(broker, owner, tmp_path):
    """检索维度①②：按能力名、按 allow/deny 过滤。"""
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "a.txt")), actor=owner)
    broker.decide(CapabilityRequest("local.fs.write", resource=str(tmp_path / "artifacts" / "b.txt")), actor=owner)
    broker.decide(CapabilityRequest("local.process.spawn", resource="python3"), actor=owner)
    read_frames = broker.search_audit(owner, capability="local.fs.read")
    assert read_frames and all(f.details["capability"] == "local.fs.read" for f in read_frames)
    denied = broker.search_audit(owner, decision="denied")
    assert denied and all(f.details["decision"] == "denied" for f in denied)
    assert {f.details["capability"] for f in denied} == {"local.process.spawn"}


def test_search_by_task_id(broker, owner, tmp_path):
    """检索维度③：按任务号过滤（决策与授予变更同维度可查）。"""
    broker.decide(
        CapabilityRequest("local.workspace.write", resource=str(tmp_path / "workspace" / "c.txt"), task_id="task-7"),
        actor=owner,
    )
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "d.txt")), actor=owner)
    frames = broker.search_audit(owner, task_id="task-7")
    assert frames and all(f.details.get("task_id") == "task-7" for f in frames)


def test_search_by_time_window(broker, owner, tmp_path):
    """检索维度④：时间窗（since/until）过滤。"""
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "e.txt")), actor=owner)
    future = utcnow() + timedelta(hours=1)
    assert broker.search_audit(owner, since=future) == []
    past = utcnow() - timedelta(hours=1)
    assert len(broker.search_audit(owner, since=past)) >= 1
    assert broker.search_audit(owner, until=past) == []


def test_search_owner_isolation(broker, owner, agent, tmp_path):
    """owner 只能检索自己的帧；service 只能检索 service 身份名下的帧。"""
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "f.txt")), actor=owner)
    owner_frames = broker.search_audit(owner)
    assert owner_frames and all(f.actor == "owner" for f in owner_frames)
    # service 身份没有产生过帧 → 空集（不是别人的帧）。
    assert broker.search_audit(agent) == []


def test_tampering_breaks_chain_verification(broker, owner, tmp_path):
    """agent/任何单写权限都不可篡改：改一帧内容 → verify 报 content-hash mismatch。"""
    broker.decide(CapabilityRequest("local.fs.read", resource=str(tmp_path / "artifacts" / "g.txt")), actor=owner)
    assert broker.verify_audit_chain().ok
    frame = broker.search_audit(owner)[0]
    frame.details["capability"] = "local.fs.tampered"  # 模拟篡改
    broker.s.flush()
    res = broker.verify_audit_chain()
    assert not res.ok
    assert any("content-hash mismatch" in p for p in res.problems)


def test_admin_changes_audited(broker, owner, tmp_path):
    """授予/撤销/级别开关/降级全部留痕（capability.grant / revoke / level_change / degrade）。"""
    broker.set_level("L3", True, actor=owner)
    row = broker.grant(
        GrantSpec(subject="owner:owner", capability="local.process.spawn",
                  resource_kind="process", resource_pattern="python*", ttl_seconds=600),
        actor=owner,
    )
    broker.revoke(row.id, actor=owner)
    broker.degrade_to("L1", actor=owner)
    actions = {f.action for f in broker.search_audit(owner)}
    assert {"capability.grant", "capability.revoke", "capability.level_change", "capability.degrade"} <= actions


def test_reversible_action_rollback(broker, owner, tmp_path):
    """可逆动作：登记撤销回调 → 回滚执行 → 留 capability.rollback 帧；一次性。"""
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    sandbox_file = tmp_path / "artifacts" / "made-by-agent.txt"
    sandbox_file.write_text("payload", encoding="utf-8")

    def undo() -> dict:
        sandbox_file.unlink(missing_ok=True)
        return {"removed": sandbox_file.name}

    rid = broker.register_reversal(undo, description="remove sandbox artifact")
    result = broker.rollback(rid, actor=owner)
    assert result["result"] == {"removed": "made-by-agent.txt"}
    assert not sandbox_file.exists()
    assert any(f.action == "capability.rollback" for f in broker.search_audit(owner))
    with pytest.raises(KeyError):
        broker.rollback(rid, actor=owner)


def test_rollback_owner_only(broker, owner):
    rid = broker.register_reversal(lambda: "x", description="demo")
    with pytest.raises(PermissionDenied):
        broker.rollback(rid, actor=Actor.service("agent-1", "agent"))


# ---------------------------------------------------------------------------
# 配置接线（config.py 能力网关配置段 + a2a_upstream_url 代加字段）
# ---------------------------------------------------------------------------


def test_settings_capability_section_and_a2a_upstream_url():
    from find_yourself.config import Settings

    s = Settings(environment="test", session_secret="s" * 40)
    assert s.capability_profile == "novice"      # 默认小白友好档（零配置）
    assert s.capability_levels == {}             # 级别开关覆盖缺省为空
    assert s.a2a_upstream_url is None            # 补齐包3 使用：代加字段，默认未配置
    s2 = Settings(environment="test", session_secret="s" * 40,
                  capability_profile="fine", capability_levels={"L3": True},
                  a2a_upstream_url="https://a2a.upstream.example")
    assert s2.capability_profile == "fine" and s2.capability_levels == {"L3": True}
    assert s2.a2a_upstream_url == "https://a2a.upstream.example"


def test_build_capability_broker_from_settings(session, tmp_path, monkeypatch):
    """build_capability_broker 按 Settings 组装：config 覆盖进入级别初值。"""
    monkeypatch.chdir(tmp_path)
    from find_yourself.config import Settings
    from find_yourself.services.audit import AuditService
    from find_yourself.services.capability import build_capability_broker

    (tmp_path / "artifacts").mkdir()
    settings = Settings(environment="test", session_secret="s" * 40,
                        artifacts_path=str(tmp_path / "artifacts"),
                        capability_levels={"L3": True})
    broker = build_capability_broker(session, AuditService(session), settings)
    assert broker.current_profile() == "novice"
    desc = {d["id"]: d for d in broker.describe_levels()}
    assert desc["L3"]["enabled"]          # config 覆盖生效
    assert desc["L4"]["enabled"] is False
    # automation 收编路已接上进程级权限门单例。
    assert broker._gates[3].__class__.__name__ == "AutomationModeGate"


def test_migration_chain_head_is_0037():
    """迁移链：0037 down_revision 指向 0036（0034-0036 已被并行包占用，顺延编号）。"""
    import importlib

    m36 = importlib.import_module("migrations.versions.0036_feature_flags")
    m37 = importlib.import_module("migrations.versions.0037_capability_grants")
    assert m37.down_revision == m36.revision == "0036_feature_flags"


def test_grant_table_created_by_migration_shape(session):
    """0037 建的表与 ORM 同形：列集合一致（create_all 与迁移两条路径不漂移）。"""
    from sqlalchemy import inspect

    from find_yourself.services.capability.grants import CapabilityGrantRow

    cols = {c["name"] for c in inspect(session.bind).get_columns("capability_grants")}
    expected = {c.name for c in CapabilityGrantRow.__table__.columns}
    assert cols == expected
