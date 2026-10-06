"""Claw 三层把关 + 事实基线库测试（A-Claw架构-01/02/03/04 + 机制-02 + 增强-01）。"""

from __future__ import annotations

import pytest

import find_yourself.db.claw_models  # noqa: F401
from find_yourself.db.claw_models import ClawFactBaseline, ClawGateDecision
from find_yourself.db.models import AuditEvent
from find_yourself.services.claw import (
    CrossValidationGate,
    FactBaselineService,
    GateVerdict,
    IndependentQAGate,
    SelfCheckGate,
    ThreeLayerPipeline,
    upsert_fact,
)


# ---------------------------------------------------------------------------
# 事实基线库（机制-02）
# ---------------------------------------------------------------------------

def test_fact_baseline_upsert_and_read(session, audit, owner):
    svc = FactBaselineService(session, audit, owner)
    row, created = svc.upsert(fact_key="项目根目录", fact_value="agent/outputs/find-yourself",
                              verified_by="agent-scout", source_task_id="t-1")
    session.commit()
    assert created is True
    # 同 key 再写 = 更新（upsert 语义），审计帧区分 created/updated
    row2, created2 = svc.upsert(fact_key="项目根目录", fact_value="agent/outputs/find-yourself/v2",
                                verified_by="agent-scout-2", source_task_id="t-2")
    session.commit()
    assert created2 is False and row2.id == row.id
    assert svc.get("项目根目录") == "agent/outputs/find-yourself/v2"
    assert svc.get("不存在的键") is None                     # 诚实缺省
    frames = session.query(AuditEvent).filter(
        AuditEvent.action.like("claw.fact.%")).all()
    assert {f.action for f in frames} == {"claw.fact.baseline_created",
                                          "claw.fact.baseline_updated"}


def test_fact_baseline_rejects_empty(session, audit, owner):
    with pytest.raises(ValueError):
        upsert_fact(session, audit, owner, fact_key="  ", fact_value="x")


# ---------------------------------------------------------------------------
# 第一层：自审（架构-02 + 增强-01 样例规则）
# ---------------------------------------------------------------------------

def test_self_check_detects_self_contradiction():
    gate = SelfCheckGate()
    out = gate.check(
        task_id="t-1", agent_role="coder",
        output={"claims": [
            {"key": "测试通过", "value": True},
            {"key": "测试通过", "value": False},      # 同对象前后相反 → block
        ]},
    )
    assert out.verdict is GateVerdict.REVISE
    assert any(f.rule == "self_contradiction" for f in out.findings)


def test_self_check_fact_baseline_conflict_rejects(session, audit, owner):
    svc = FactBaselineService(session, audit, owner)
    svc.upsert(fact_key="用户数量", fact_value="1200")
    session.commit()
    gate = SelfCheckGate(fact_lookup=lambda k: svc.get(k))
    out = gate.check(
        task_id="t-1", agent_role="analyst",
        output={"claims": [{"key": "用户数量", "value": 9999}]},
    )
    assert out.verdict is GateVerdict.REJECT           # 与基线冲突=不可用
    assert "用户数量" in out.fact_keys_checked


def test_self_check_forbidden_rule_revise_and_pass():
    gate = SelfCheckGate(forbidden_rules=("自动重试不可逆操作",))
    out = gate.check(task_id="t-1", agent_role="r",
                     output={"text": "方案：自动重试不可逆操作"})
    assert out.verdict is GateVerdict.REVISE
    ok = gate.check(task_id="t-1", agent_role="r",
                    output={"text": "干净产出", "claims": [{"key": "k", "value": 1}]})
    assert ok.verdict is GateVerdict.PASS


# ---------------------------------------------------------------------------
# 第二层：同角色交叉验证（架构-03）
# ---------------------------------------------------------------------------

def test_cross_validation_agreement_passes():
    gate = CrossValidationGate()
    primary = {"claims": [{"key": "行数", "value": 42}, {"key": "有测试", "value": True}]}
    cross = {"claims": [{"key": "行数", "value": 42}, {"key": "有测试", "value": True}]}
    out = gate.check(task_id="t", agent_role="coder", primary=primary, cross=cross)
    assert out.verdict is GateVerdict.PASS


def test_cross_validation_contradiction_escalates():
    gate = CrossValidationGate()
    out = gate.check(
        task_id="t", agent_role="coder",
        primary={"claims": [{"key": "根目录", "value": "/a/b"}]},
        cross={"claims": [{"key": "根目录", "value": "/x/y"}]},
    )
    assert out.verdict is GateVerdict.ESCALATE          # 辩不出 → 升级
    assert any(f.rule == "cross_contradiction" for f in out.findings)


def test_cross_validation_partial_disagreement_revise():
    gate = CrossValidationGate()
    out = gate.check(
        task_id="t", agent_role="coder",
        primary={"claims": [{"key": "行数", "value": 42}, {"key": "另一点", "value": 1}]},
        cross={"claims": [{"key": "行数", "value": 42}]},
    )
    assert out.verdict is GateVerdict.REVISE            # 进辩论（部分未确认）
    assert any(f.rule == "partial_disagreement" for f in out.findings)


def test_cross_validation_no_overlap_escalates():
    gate = CrossValidationGate()
    out = gate.check(task_id="t", agent_role="r",
                     primary={"claims": [{"key": "a", "value": 1}]},
                     cross={"claims": [{"key": "b", "value": 2}]})
    assert out.verdict is GateVerdict.ESCALATE


# ---------------------------------------------------------------------------
# 第三层：独立质检（架构-04）
# ---------------------------------------------------------------------------

def test_qa_gate_rejects_violation_and_escalates_waste():
    qa = IndependentQAGate(forbidden_rules=("纯绿色",))
    out = qa.check(task_id="t", agent_role="ui",
                   primary={"text": "这里用了纯绿色"}, attempts=1)
    assert out.verdict is GateVerdict.REJECT
    # 浪费：重复 3 次不收敛 → 越级上报
    out2 = qa.check(task_id="t", agent_role="r",
                    primary={"text": "ok", "claims": [{"key": "k", "value": 1}]},
                    attempts=3)
    assert out2.verdict is GateVerdict.ESCALATE
    assert any(f.rule == "waste" for f in out2.findings)


# ---------------------------------------------------------------------------
# 三层流水线（架构-01）：逐层短路 + 全量留痕
# ---------------------------------------------------------------------------

def _pipeline(session, audit, owner, **kw) -> ThreeLayerPipeline:
    return ThreeLayerPipeline(session, audit, owner, **kw)


def test_pipeline_all_pass_records_three_layers(session, audit, owner):
    p = _pipeline(session, audit, owner)
    out = p.run(
        task_id="t-ok", agent_role="coder",
        primary_output={"text": "ok", "claims": [{"key": "行数", "value": 42}]},
        cross_output={"claims": [{"key": "行数", "value": 42}]},
    )
    session.commit()
    assert out.passed and len(out.outcomes) == 3
    rows = session.query(ClawGateDecision).filter(
        ClawGateDecision.task_id == "t-ok").all()
    assert [r.layer for r in rows] == ["self_check", "cross_validation", "independent_qa"]
    frames = session.query(AuditEvent).filter(
        AuditEvent.action == "claw.gate", AuditEvent.target.in_([r.id for r in rows])
    ).all()
    assert len(frames) == 3


def test_pipeline_self_check_short_circuits(session, audit, owner):
    p = _pipeline(session, audit, owner)
    out = p.run(
        task_id="t-sc", agent_role="coder",
        primary_output={"claims": [{"key": "通过", "value": True},
                                   {"key": "通过", "value": False}]},
        cross_output={"claims": [{"key": "通过", "value": True}]},
    )
    assert out.verdict is GateVerdict.REVISE
    assert len(out.outcomes) == 1                        # 互审/质检未跑
    rows = session.query(ClawGateDecision).filter(
        ClawGateDecision.task_id == "t-sc").all()
    assert len(rows) == 1 and rows[0].layer == "self_check"


def test_pipeline_cross_escalate_blocks_before_qa(session, audit, owner):
    p = _pipeline(session, audit, owner)
    out = p.run(
        task_id="t-esc", agent_role="coder",
        primary_output={"claims": [{"key": "结论", "value": "A 方案"}]},
        cross_output={"claims": [{"key": "结论", "value": "B 方案"}]},
    )
    assert out.verdict is GateVerdict.ESCALATE
    layers = [o.layer.value for o in out.outcomes]
    assert layers == ["self_check", "cross_validation"]  # 质检未跑
