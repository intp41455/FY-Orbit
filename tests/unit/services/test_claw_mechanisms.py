"""Claw 机制-01/03/04 + 参与四件测试。"""

from __future__ import annotations

import pytest

import find_yourself.db.claw_models  # noqa: F401
from find_yourself.db.claw_models import (
    ClawDecisionPreference,
    ClawParticipationMode,
)
from find_yourself.db.models import AuditEvent
from find_yourself.services.claw import (
    CommandGate,
    DecisionPreferenceStore,
    HealthDashboard,
    ParticipationMode,
    ParticipationService,
    ThreeLayerPipeline,
)
from find_yourself.services.claw.conflicts import ConflictService, DetectionSignal
from find_yourself.services.errors import Conflict as DomainConflict
from find_yourself.services.errors import ValidationFailed


# ---------------------------------------------------------------------------
# 机制-01 指令校验门
# ---------------------------------------------------------------------------

def test_command_gate_flags_role_mismatch_vagueness_prior_conflict():
    gate = CommandGate(
        role_capabilities={"viewer": {"read"}},
        prior_directives=["实现登录接口与权限模型"],
    )
    check = gate.validate(
        recipient_role="viewer",
        directive_text="随便弄一下实现登录接口与权限模型的事",
        tool="write",
    )
    kinds = {w["kind"] for w in check.warnings}
    assert {"role_mismatch", "vague_directive", "conflicts_with_prior"} <= kinds
    assert check.requires_confirmation is True        # 弹提醒等确认
    assert check.blocked is False                     # 只提醒不硬拦（AC 语义）


def test_command_gate_clean_directive_passes():
    gate = CommandGate(role_capabilities={"coder": {"write"}})
    check = gate.validate(recipient_role="coder",
                          directive_text="实现登录接口并补齐单元测试", tool="write")
    assert check.warnings == [] and check.requires_confirmation is False


# ---------------------------------------------------------------------------
# 机制-03 健康仪表盘
# ---------------------------------------------------------------------------

def _seed_conflicts(session, audit, owner, n: int) -> None:
    svc = ConflictService(session, audit, owner)
    svc.detect_and_record(task_id="seed",
                          signal=DetectionSignal(agent="alice", agent_role="c",
                                                 action="write", target="a.py"))
    # 每轮换一个新 agent 争用同一对象——管辖登记表按 agent 累积，逐轮各记一条
    for i in range(n):
        svc.detect_and_record(task_id="seed",
                              signal=DetectionSignal(agent=f"agent-{i}", agent_role="c",
                                                     action="write", target="a.py"))


def test_health_dashboard_aggregates_and_suggests_pause(session, audit, owner):
    _seed_conflicts(session, audit, owner, 6)          # >阈值 5
    dash = HealthDashboard(session)
    snap = dash.snapshot()
    assert snap["open_conflicts"] == 6
    assert snap["conflicts_by_class"] == {"jurisdiction": 6}
    assert snap["suggest_pause"] is True
    assert any("暂停" in s for s in snap["suggestions"])
    assert snap["requirement_change_count"] is None    # 未接入的数据源如实标注


def test_health_dashboard_gate_metrics(session, audit, owner):
    p = ThreeLayerPipeline(session, audit, owner)
    p.run(task_id="t-h", agent_role="coder",
          primary_output={"claims": [{"key": "通过", "value": True},
                                     {"key": "通过", "value": False}]})
    snap = HealthDashboard(session).snapshot(task_id="t-h")
    assert snap["gate_decisions"] == 1
    assert snap["self_correction_count"] == 1          # revise=自修正次数
    # 1/1 全是 revise → 自修正率 100% 超阈值，仪表盘如实建议暂停
    assert snap["revise_rate"] == 1.0 and snap["suggest_pause"] is True


# ---------------------------------------------------------------------------
# 机制-04 决策偏好库
# ---------------------------------------------------------------------------

def test_decision_preference_record_and_match(session, audit, owner):
    store = DecisionPreferenceStore(session, audit, owner)
    store.record(pattern_key="jurisdiction:同文件写入", decision="先到者得，后来者改路径",
                 task_id="t-1")
    store.record(pattern_key="jurisdiction:同文件写入", decision="先到者得，后来者改路径",
                 task_id="t-9")
    session.commit()
    row = session.query(ClawDecisionPreference).one()
    assert row.occurrences == 2                         # 同类裁决累积
    matched = store.match("jurisdiction:同文件写入")
    assert matched and matched["decision"].startswith("先到者得")
    assert store.match("没记录过的情境") is None
    frames = session.query(AuditEvent).filter(
        AuditEvent.action.like("claw.preference.%")).all()
    assert {f.action for f in frames} == {"claw.preference.created",
                                          "claw.preference.reinforced"}


def test_decision_preference_requires_content(session, audit, owner):
    store = DecisionPreferenceStore(session, audit, owner)
    with pytest.raises(ValueError):
        store.record(pattern_key=" ", decision="x")
    with pytest.raises(ValueError):
        store.record(pattern_key="k", decision="")


# ---------------------------------------------------------------------------
# 参与-01/02/03/04 四档模式
# ---------------------------------------------------------------------------

def test_participation_mode_rules_and_memory(session, audit, owner):
    svc = ParticipationService(session, audit, owner)
    assert svc.get_mode() == ""                        # 从未选择
    assert svc.should_pause("step_completed") is True  # 未选模式=保守逢事即问

    svc.remember_mode(ParticipationMode.AUTO)          # 参与-04：记住
    session.commit()
    assert svc.get_mode() == ParticipationMode.AUTO
    # 参与-01 全自动：只有方案确认与最终交付停
    assert svc.should_pause("plan_approval") is True
    assert svc.should_pause("step_completed") is False
    assert svc.should_pause("final_delivery") is True

    # 参与-02 关键节点：需求变更/技术选型/上线发布才裁决
    svc.remember_mode(ParticipationMode.KEY_NODES)
    session.commit()
    assert svc.should_pause("requirement_change") is True
    assert svc.should_pause("tech_choice") is True
    assert svc.should_pause("release") is True
    assert svc.should_pause("step_completed") is False

    # 参与-03 全程陪跑：每大步骤都响
    svc.remember_mode(ParticipationMode.ESCORT)
    session.commit()
    assert svc.should_pause("step_completed") is True
    assert svc.should_pause("gate_blocked") is True

    rows = session.query(ClawParticipationMode).all()
    assert len(rows) == 1                              # 一人一行（记忆覆盖）
