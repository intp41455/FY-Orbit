"""Claw 六类冲突检测 + 四级升级测试（A-Claw架构-05/06 + 增强-01）。"""

from __future__ import annotations

import pytest

import find_yourself.db.claw_models  # noqa: F401
from find_yourself.db.claw_models import ClawConflictRecord
from find_yourself.db.models import AuditEvent
from find_yourself.services.claw import (
    CONFLICT_CLASSES,
    ConflictDetector,
    ConflictService,
    DetectionSignal,
)
from find_yourself.services.errors import Conflict as DomainConflict


def test_six_conflict_classes_are_named():
    assert CONFLICT_CLASSES == (
        "jurisdiction", "boundary", "self_contradiction",
        "persona", "data_inconsistency", "role_overreach",
    )


def test_c1_jurisdiction_target_claimed_by_other():
    d = ConflictDetector()
    s1 = DetectionSignal(agent="alice", agent_role="coder", action="write", target="app.py")
    assert d.detect(s1) == []                       # 首个声明=登记，不冲突
    s2 = DetectionSignal(agent="bob", agent_role="coder", action="write", target="app.py")
    hits = d.detect(s2)
    assert any(h.conflict_class == "jurisdiction" for h in hits)


def test_c2_boundary_unowned_action():
    hits = ConflictDetector().detect(DetectionSignal(action="deploy"))
    assert any(h.conflict_class == "boundary" for h in hits)


def test_c3_self_contradiction_claim_reversed():
    hits = ConflictDetector().detect(DetectionSignal(
        agent="alice", agent_role="analyst",
        claims=[{"key": "通过", "value": True}, {"key": "通过", "value": False}]))
    assert any(h.conflict_class == "self_contradiction" for h in hits)


def test_c4_persona_banned_phrase():
    d = ConflictDetector(persona_banned_phrases={"support": ("这不归我管",)})
    hits = d.detect(DetectionSignal(
        agent="s1", agent_role="support", text="这个嘛，这不归我管。"))
    assert any(h.conflict_class == "persona" for h in hits)


def test_c5_data_inconsistency_with_baseline():
    d = ConflictDetector(fact_lookup=lambda k: 1200 if k == "用户数量" else None)
    hits = d.detect(DetectionSignal(
        agent="a1", agent_role="analyst",
        claims=[{"key": "用户数量", "value": 9999}]))
    assert any(h.conflict_class == "data_inconsistency" for h in hits)


def test_c6_role_overreach_outside_capabilities():
    d = ConflictDetector(role_capabilities={"viewer": {"read"}})
    hits = d.detect(DetectionSignal(
        agent="b1", agent_role="viewer", action="write"))
    assert any(h.conflict_class == "role_overreach" for h in hits)
    # 清单内的动作不报
    assert d.detect(DetectionSignal(agent="b1", agent_role="viewer", action="read")) == []


def test_detect_and_record_persists_and_audits(session, audit, owner):
    svc = ConflictService(session, audit, owner)
    records = svc.detect_and_record(
        task_id="t-1",
        signal=DetectionSignal(agent="alice", agent_role="coder", action="write",
                               target="shared.py"),
    )
    assert records == []                            # 首个声明只登记管辖
    records = svc.detect_and_record(
        task_id="t-1",
        signal=DetectionSignal(agent="bob", agent_role="coder", action="write",
                               target="shared.py"),
    )
    assert len(records) == 1
    session.commit()
    row = session.get(ClawConflictRecord, records[0].id)
    assert row.conflict_class == "jurisdiction" and row.status == "open"
    frames = session.query(AuditEvent).filter(
        AuditEvent.action == "claw.conflict.recorded").all()
    assert len(frames) == 1


def test_escalation_ladder_is_strictly_incremental(session, audit, owner, owner2=None):
    svc = ConflictService(session, audit, owner)
    svc.detect_and_record(                                  # alice 先声明管辖
        task_id="t-2",
        signal=DetectionSignal(agent="alice", agent_role="coder", action="write",
                               target="owned.py"),
    )
    (rec,) = svc.detect_and_record(
        task_id="t-2",
        signal=DetectionSignal(agent="bob", agent_role="coder", action="write",
                               target="owned.py"),
    )
    session.commit()
    svc.escalate(owner, rec.id, note="自修复失败")          # L1→L2
    assert session.get(ClawConflictRecord, rec.id).level == 2
    svc.escalate(owner, rec.id, note="协商不出")            # L2→L3
    row = session.get(ClawConflictRecord, rec.id)
    assert row.level == 3 and row.status == "escalated"     # L3+ 等人工
    svc.escalate(owner, rec.id, note="人工裁决转全局")       # L3→L4
    assert session.get(ClawConflictRecord, rec.id).level == 4
    with pytest.raises(DomainConflict):
        svc.escalate(owner, rec.id)                         # L4 封顶


def test_escalation_rejected_after_resolve(session, audit, owner):
    svc = ConflictService(session, audit, owner)
    svc.detect_and_record(
        task_id="t-3",
        signal=DetectionSignal(agent="alice", agent_role="coder", action="write",
                               target="done.py"),
    )
    (rec,) = svc.detect_and_record(
        task_id="t-3",
        signal=DetectionSignal(agent="bob", agent_role="coder", action="write",
                               target="done.py"),
    )
    session.commit()
    svc.resolve(owner, rec.id, note="自修复完成")
    row = session.get(ClawConflictRecord, rec.id)
    assert row.status == "resolved" and row.resolved_at is not None
    with pytest.raises(DomainConflict):
        svc.escalate(owner, rec.id)


def test_open_conflicts_listing(session, audit, owner):
    svc = ConflictService(session, audit, owner)
    svc.detect_and_record(task_id="t-4",
                          signal=DetectionSignal(agent="alice", agent_role="c",
                                                 action="write", target="x.py"))
    svc.detect_and_record(task_id="t-4",
                          signal=DetectionSignal(agent="bob", agent_role="c",
                                                 action="write", target="x.py"))
    svc.detect_and_record(task_id="t-4",
                          signal=DetectionSignal(agent="alice", agent_role="c",
                                                 action="write", target="y.py"))
    svc.detect_and_record(task_id="t-4",
                          signal=DetectionSignal(agent="carol", agent_role="c",
                                                 action="write", target="y.py"))
    assert len(svc.open_conflicts()) == 2
    svc.detect_and_record(task_id="t-5",
                          signal=DetectionSignal(agent="alice", agent_role="c",
                                                 action="write", target="z.py"))
    (rec,) = svc.detect_and_record(task_id="t-5",
                                   signal=DetectionSignal(agent="dave", agent_role="c",
                                                          action="write", target="z.py"))
    session.commit()
    svc.resolve(owner, rec.id, note="ok")
    assert all(r.id != rec.id for r in svc.open_conflicts())
