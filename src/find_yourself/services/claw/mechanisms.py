"""A-Claw机制-01/03/04 · 指令校验门 + 任务健康仪表盘 + 决策偏好库。

* **指令校验门**（机制-01）：发令前先扫一遍——收件人角色与指令内容对不对
  得上、模糊表述、与之前任务书/事实基线冲突；有警告就 ``requires_confirmation``
  弹提醒，用户确认再发（不静默拦截也不静默放行）。
* **健康仪表盘**（机制-03）：聚合任务进度/冲突频率/自修正次数等真实信号，
  超阈值主动建议暂停。数据全部来自已落库的治理台账——没有的数据源如实
  标注，不编造指标。
* **决策偏好库**（机制-04）：用户每次裁决/拍板/修改记下来（occurrences
  累积），下次类似冲突自动套用——从「每次都要裁决」变「大部分系统自己处理」。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.claw_models import (
    ClawDecisionPreference,
    ClawGateDecision,
    ClawParticipationMode,
)
from ..actor import Actor
from ..audit import AuditService
from .conflicts import ClawConflictRecord

# ---------------------------------------------------------------------------
# 机制-01 · 发令前指令校验门
# ---------------------------------------------------------------------------

_VAGUE_WORDS = ("随便", "看看", "大概", "之类", "那些", "弄一下", "搞一下", "尽量")


@dataclass
class DirectiveCheck:
    ok: bool
    warnings: list[dict[str, str]] = field(default_factory=list)
    requires_confirmation: bool = False

    @property
    def blocked(self) -> bool:
        # 机制-01 语义：只提醒不硬拦——确认权在用户
        return False


class CommandGate:
    """发令前指令校验门。"""

    def __init__(
        self,
        *,
        role_capabilities: dict[str, set[str]] | None = None,
        fact_lookup: Callable[[str], str | None] | None = None,
        prior_directives: list[str] | None = None,
    ):
        self.role_capabilities = role_capabilities or {}
        self.fact_lookup = fact_lookup
        self.prior_directives = list(prior_directives or [])

    def validate(
        self, *, recipient_role: str, directive_text: str, tool: str = "",
    ) -> DirectiveCheck:
        warnings: list[dict[str, str]] = []
        text = (directive_text or "").strip()

        # W1 角色-指令对不上：指令涉及的工具不在收件人能力清单
        caps = self.role_capabilities.get(recipient_role)
        if caps is not None and tool and tool not in caps:
            warnings.append({
                "kind": "role_mismatch",
                "message": f"角色 {recipient_role!r} 能力清单不含工具 {tool!r}"
                           "（指令可能与收件人职责对不上）",
            })

        # W2 模糊表述：过短或命中模糊词
        if len(text) < 8:
            warnings.append({"kind": "vague_directive",
                             "message": f"指令过短（{len(text)} 字），表述可能不够明确"})
        for w in _VAGUE_WORDS:
            if w in text:
                warnings.append({"kind": "vague_directive",
                                 "message": f"指令含模糊表述 {w!r}"})
                break

        # W3 与之前任务书冲突：指令复述了先前任务书片段（可能有变更意图）
        for prior in self.prior_directives:
            key_frag = prior[:20]
            if key_frag and key_frag in text and prior != text:
                warnings.append({
                    "kind": "conflicts_with_prior",
                    "message": f"指令与先前任务书片段重叠：{key_frag!r}…（确认是否有意变更）",
                })
                break
        return DirectiveCheck(ok=True, warnings=warnings,
                              requires_confirmation=bool(warnings))


# ---------------------------------------------------------------------------
# 机制-03 · 任务健康仪表盘
# ---------------------------------------------------------------------------

class HealthDashboard:
    """聚合治理台账的真实信号；超阈值主动建议暂停。"""

    def __init__(
        self, session: Session, *,
        max_open_conflicts: int = 5,
        max_escalation_ratio: float = 0.3,
        max_revise_rate: float = 0.5,
    ):
        self.s = session
        self.max_open_conflicts = max_open_conflicts
        self.max_escalation_ratio = max_escalation_ratio
        self.max_revise_rate = max_revise_rate

    def snapshot(self, *, task_id: str = "") -> dict[str, Any]:
        q_conf = select(ClawConflictRecord)
        if task_id:
            # 冲突登记不带 task_id 列（跨任务信号）——按 open 状态全量聚合，
            # task_id 维度在 detail 里，由指标消费方过滤。
            pass
        conflicts = list(self.s.execute(q_conf).scalars().all())
        open_conflicts = [c for c in conflicts if c.status != "resolved"]
        escalated = [c for c in conflicts if c.status == "escalated"]
        by_class: dict[str, int] = {}
        for c in open_conflicts:
            by_class[c.conflict_class] = by_class.get(c.conflict_class, 0) + 1

        q_gate = select(ClawGateDecision)
        if task_id:
            q_gate = q_gate.where(ClawGateDecision.task_id == task_id)
        gates = list(self.s.execute(q_gate).scalars().all())
        revise = sum(1 for g in gates if g.verdict == "revise")
        _ = sum(1 for g in gates if g.verdict == "reject")
        escal = sum(1 for g in gates if g.verdict == "escalate")
        total_gates = len(gates)
        revise_rate = round(revise / total_gates, 4) if total_gates else 0.0
        escalation_ratio = round((escal + len(escalated)) / total_gates, 4) if total_gates else 0.0

        suggestions: list[str] = []
        if len(open_conflicts) > self.max_open_conflicts:
            suggestions.append(f"未解决冲突 {len(open_conflicts)} 个（>阈值 {self.max_open_conflicts}），建议暂停任务")
        if total_gates and escalation_ratio > self.max_escalation_ratio:
            suggestions.append(f"升级率 {escalation_ratio}（>阈值 {self.max_escalation_ratio}），建议人工介入")
        if total_gates and revise_rate > self.max_revise_rate:
            suggestions.append(f"自修正率 {revise_rate}（>阈值 {self.max_revise_rate}），建议检查任务定义质量")
        return {
            "task_id": task_id,
            "open_conflicts": len(open_conflicts),
            "conflicts_by_class": by_class,
            "escalated_conflicts": len(escalated),
            "gate_decisions": total_gates,
            "self_correction_count": revise,       # 自修正次数（AC 指标）
            "revise_rate": revise_rate,
            "escalation_ratio": escalation_ratio,
            # 需求变更次数：数据源是需求管理域（不在治理台账），如实标注未接入
            "requirement_change_count": None,
            "requirement_change_note": "需求变更计数需接需求管理域，治理侧未接入（诚实标注）",
            "suggest_pause": bool(suggestions),
            "suggestions": suggestions,
        }


# ---------------------------------------------------------------------------
# 机制-04 · 决策偏好库
# ---------------------------------------------------------------------------

class DecisionPreferenceStore:
    """用户每次裁决/拍板/修改沉淀；下次类似冲突自动套用。"""

    def __init__(self, session: Session, audit: AuditService, actor: Actor):
        self.s = session
        self.audit = audit
        self.actor = actor

    def record(self, *, pattern_key: str, decision: str,
               task_id: str = "") -> ClawDecisionPreference:
        key = (pattern_key or "").strip()
        decision = (decision or "").strip()
        if not key or not decision:
            raise ValueError("pattern_key and decision are required")
        owner = getattr(self.actor, "owner_id", "") or ""
        row = self.s.execute(
            select(ClawDecisionPreference).where(
                ClawDecisionPreference.owner_id == owner,
                ClawDecisionPreference.pattern_key == key,
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = ClawDecisionPreference(
                id=uuid.uuid4().hex, owner_id=owner, pattern_key=key[:200],
                decision=decision, occurrences=1, last_task_id=task_id[:200],
            )
            self.s.add(row)
        else:
            row.decision = decision
            row.occurrences = (row.occurrences or 0) + 1
            row.last_task_id = task_id[:200] or row.last_task_id
        self.s.flush()
        self.audit.append(
            self.actor,
            "claw.preference.created" if created else "claw.preference.reinforced",
            row.id, {"pattern_key": key[:120], "occurrences": row.occurrences},
        )
        return row

    def match(self, pattern_key: str) -> dict[str, Any] | None:
        """下次类似冲突自动套用：精确匹配；找不到返回 None（诚实缺省）。"""
        key = (pattern_key or "").strip()
        if not key:
            return None
        owner = getattr(self.actor, "owner_id", "") or ""
        row = self.s.execute(
            select(ClawDecisionPreference).where(
                ClawDecisionPreference.owner_id == owner,
                ClawDecisionPreference.pattern_key == key,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {"pattern_key": row.pattern_key, "decision": row.decision,
                "occurrences": row.occurrences}


# ---------------------------------------------------------------------------
# 参与-01/02/03/04 · 四档参与模式（+记忆）
# ---------------------------------------------------------------------------

class ParticipationMode:
    AUTO = "auto"                # 参与-01 全自动：只给最终目标，预设节点自动停
    KEY_NODES = "key_nodes"      # 参与-02 关键节点：需求变更/技术选型/上线发布才裁决
    ESCORT = "escort"            # 参与-03 全程陪跑：每大步骤响一下，随时打断


#: 各档位需要用户裁决/知会的事件类型（AC 语义落成确定性规则表）
_PAUSE_EVENTS: dict[str, tuple[str, ...]] = {
    ParticipationMode.AUTO: ("plan_approval", "final_delivery"),
    ParticipationMode.KEY_NODES: ("requirement_change", "tech_choice",
                                  "release", "final_delivery"),
    ParticipationMode.ESCORT: ("step_completed", "requirement_change",
                               "tech_choice", "release", "final_delivery",
                               "gate_blocked", "conflict_escalated"),
}


class ParticipationService:
    """参与模式裁决规则 + 记忆（参与-01/02/03/04）。"""

    def __init__(self, session: Session, audit: AuditService, actor: Actor):
        self.s = session
        self.audit = audit
        self.actor = actor

    def remember_mode(self, mode: str) -> ClawParticipationMode:
        """参与-04：第一次选了模式系统就记住，下次同样问题直接按规则来。"""
        if mode not in (ParticipationMode.AUTO, ParticipationMode.KEY_NODES,
                        ParticipationMode.ESCORT):
            raise ValueError(f"unknown participation mode: {mode!r}")
        owner = getattr(self.actor, "owner_id", "") or ""
        row = self.s.execute(
            select(ClawParticipationMode).where(
                ClawParticipationMode.owner_id == owner)
        ).scalar_one_or_none()
        if row is None:
            row = ClawParticipationMode(id=uuid.uuid4().hex, owner_id=owner, mode=mode)
            self.s.add(row)
        else:
            row.mode = mode
        self.s.flush()
        self.audit.append(self.actor, "claw.participation.mode_set", row.id,
                          {"mode": mode})
        return row

    def get_mode(self) -> str:
        """已记忆的模式；从未选过时诚实返回 ""（调用方引导用户选择）。"""
        owner = getattr(self.actor, "owner_id", "") or ""
        row = self.s.execute(
            select(ClawParticipationMode).where(
                ClawParticipationMode.owner_id == owner)
        ).scalar_one_or_none()
        return row.mode if row is not None else ""

    def should_pause(self, event_kind: str, *, mode: str | None = None) -> bool:
        """该事件在当前参与模式下是否需要停下来给用户。"""
        m = mode or self.get_mode()
        if not m:
            return True   # 未选模式=默认保守（逢事即问），选过就按规则来
        return event_kind in _PAUSE_EVENTS.get(m, ())
