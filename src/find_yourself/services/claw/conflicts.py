"""A-Claw架构-05/06 + 增强-01 · 六类冲突预防（可命名+可机检）与四级升级。

六类冲突（增强-01：每类有命名定义与可执行的检测规则）：

========== ============================ ===================================
 类           命名                          机检规则（detect 输入信号）
========== ============================ ===================================
 C1 jurisdiction   任务范围/管辖权冲突        同一 target 被两个不同 agent
                                             声明写入（管辖声明碰撞）
 C2 boundary       职责边界模糊               动作未声明 owner 角色即执行
 C3 self_contradiction 自我冲突               同 Agent 同一结论对象前后相反
                                             （复用 gates._is_contradiction）
 C4 persona        情绪化/人设冲突             产出命中该角色禁用话术/越出人设标记
 C5 data_inconsistency 数据/口径不一致          同一指标不同数值（对照事实基线）
 C6 role_overreach 角色越界                    agent 执行了不在其能力清单内的工具
========== ============================ ===================================

四级升级（架构-06，采纳 WorkBuddy 现有语义）：
  L1 自修复/自我校验 → L2 协商解决/交叉验证 → L3 人工裁决 → L4 全局指令修正。
黄金三原则（参与-02）：能低级解决绝不升级，同角色协商→主控仲裁→才找用户
——状态机强制 **level 只能 +1 逐级升**，越级必须显式 ``force=True``（人工）。

每次检测命中落 ``claw_conflicts`` + 审计帧 ``claw.conflict.*``；L3 及以上
同时标记 ``status="escalated"`` 等待人工（HITL 接线由调用方完成）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.claw_models import ClawConflictRecord
from ..actor import Actor
from ..audit import AuditService
from .gates import _is_contradiction, _values_differ

#: 六类冲突的权威命名（增强-01：可命名）
CONFLICT_CLASSES: tuple[str, ...] = (
    "jurisdiction",        # C1 任务范围/管辖权冲突
    "boundary",            # C2 职责边界模糊
    "self_contradiction",  # C3 自我冲突
    "persona",             # C4 情绪化/人设冲突
    "data_inconsistency",  # C5 数据/口径不一致
    "role_overreach",      # C6 角色越界
)

#: 四级升级的语义标签（架构-06）
ESCALATION_LEVELS: tuple[str, ...] = (
    "L1 自修复/自我校验",
    "L2 协商解决/交叉验证",
    "L3 人工裁决",
    "L4 全局指令修正",
)


@dataclass(frozen=True)
class DetectionSignal:
    """一次可被检测规则消费的结构化信号（由调用方从任务/产出中提取）。"""

    agent: str = ""
    agent_role: str = ""
    action: str = ""          # 执行的动作/工具名
    target: str = ""          # 作用的对象（文件/资源/结论 key）
    claims: list[dict[str, Any]] = None  # type: ignore[assignment]
    text: str = ""
    extra: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.claims is None:
            object.__setattr__(self, "claims", [])
        if self.extra is None:
            object.__setattr__(self, "extra", {})


@dataclass(frozen=True)
class DetectionHit:
    conflict_class: str
    rule: str
    message: str


def _claims_of(signal: DetectionSignal) -> list[dict[str, Any]]:
    return [c for c in (signal.claims or []) if isinstance(c, dict) and c.get("key")]


class ConflictDetector:
    """六类冲突的机检规则集（增强-01：可命名、可执行、可测试）。"""

    def __init__(
        self,
        *,
        jurisdiction_claims: dict[str, set[str]] | None = None,
        fact_lookup: Any | None = None,
        persona_banned_phrases: dict[str, tuple[str, ...]] | None = None,
        role_capabilities: dict[str, set[str]] | None = None,
    ):
        # 管辖登记表：target -> 声明过写入权的 agent 集合（跨信号累积）
        self.jurisdiction_claims: dict[str, set[str]] = jurisdiction_claims or {}
        self.fact_lookup = fact_lookup
        # 人设禁用话术：role -> 禁用短语
        self.persona_banned = persona_banned_phrases or {}
        # 能力清单：role -> 允许的工具/动作
        self.role_capabilities = role_capabilities or {}

    def detect(self, signal: DetectionSignal) -> list[DetectionHit]:
        hits: list[DetectionHit] = []
        agent = signal.agent or signal.agent_role

        # C1 管辖权：同一 target 已被别的 agent 声明 → 冲突
        target = signal.target.strip()
        if target and agent:
            claimed = self.jurisdiction_claims.setdefault(target, set())
            if claimed and agent not in claimed:
                hits.append(DetectionHit(
                    "jurisdiction", "target_claimed_by_other",
                    f"对象 {target[:80]!r} 已由 {sorted(claimed)} 管辖，{agent} 争用同一对象"))
            claimed.add(agent)

        # C2 职责边界：动作执行但既无 agent 也无 role（没人负责）
        if signal.action and not (signal.agent or signal.agent_role):
            hits.append(DetectionHit(
                "boundary", "unowned_action",
                f"动作 {signal.action[:60]!r} 无归属角色（职责边界模糊）"))

        # C3 自我冲突：同 Agent 同一结论对象前后相反
        by_key: dict[str, list[Any]] = {}
        for c in _claims_of(signal):
            by_key.setdefault(str(c["key"]), []).append(c.get("value"))
        for key, values in by_key.items():
            for i in range(len(values) - 1):
                if _is_contradiction(values[i], values[i + 1]):
                    hits.append(DetectionHit(
                        "self_contradiction", "claim_reversed",
                        f"Agent {agent!r} 对 {key!r} 前后结论相反: "
                        f"{values[i]!r} → {values[i + 1]!r}"))

        # C4 人设冲突：命中该角色的禁用话术
        banned = self.persona_banned.get(signal.agent_role, ())
        for phrase in banned:
            if phrase and phrase in signal.text:
                hits.append(DetectionHit(
                    "persona", "persona_banned_phrase",
                    f"角色 {signal.agent_role!r} 产出命中禁用话术: {phrase[:40]!r}"))

        # C5 数据/口径不一致：claim 与事实基线不等
        if self.fact_lookup is not None:
            for c in _claims_of(signal):
                baseline = self.fact_lookup(str(c["key"]))
                if baseline is not None and _values_differ(baseline, c.get("value")):
                    hits.append(DetectionHit(
                        "data_inconsistency", "baseline_mismatch",
                        f"指标 {c['key']!r} 与基线口径不一致: 基线={baseline!r} "
                        f"产出={c.get('value')!r}"))

        # C6 角色越界：执行了能力清单之外的工具
        caps = self.role_capabilities.get(signal.agent_role)
        if caps is not None and signal.action and signal.action not in caps:
            hits.append(DetectionHit(
                "role_overreach", "action_outside_capabilities",
                f"角色 {signal.agent_role!r} 执行能力清单外的动作: "
                f"{signal.action[:60]!r}"))
        return hits


class ConflictService:
    """冲突登记与四级升级状态机（架构-06）。"""

    def __init__(self, session: Session, audit: AuditService, actor: Actor,
                 *, detector: ConflictDetector | None = None):
        self.s = session
        self.audit = audit
        self.actor = actor
        self.detector = detector or ConflictDetector()

    def detect_and_record(self, *, task_id: str, signal: DetectionSignal) -> list[ClawConflictRecord]:
        """跑六类检测规则；命中即登记（每条一行）+ 审计帧。无命中返回空表。"""
        records: list[ClawConflictRecord] = []
        for hit in self.detector.detect(signal):
            rec = ClawConflictRecord(
                id=uuid.uuid4().hex,
                owner_id=getattr(self.actor, "owner_id", "") or "",
                conflict_class=hit.conflict_class,
                parties={"agent": signal.agent, "agent_role": signal.agent_role},
                detail=f"[{hit.rule}] {hit.message}"[:2000],
                level=1, status="open",
            )
            self.s.add(rec)
            self.s.flush()
            records.append(rec)
            self.audit.append(
                self.actor, "claw.conflict.recorded", rec.id,
                {"conflict_class": hit.conflict_class, "rule": hit.rule,
                 "task_id": task_id, "message": hit.message[:200]},
            )
        return records

    def escalate(self, actor: Actor, conflict_id: str, *, note: str = "",
                 force: bool = False) -> ClawConflictRecord:
        """四级升级：默认**逐级 +1**（能低级解决绝不升级，黄金三原则）；
        越级必须人工显式 ``force=True``。L3+ 置 ``escalated`` 等人工。"""
        rec = self.s.get(ClawConflictRecord, conflict_id)
        if rec is None:
            from ..errors import NotFound
            raise NotFound("conflict_not_found", "Conflict not found", 404)
        if rec.status == "resolved":
            from ..errors import Conflict
            raise Conflict("conflict_resolved", "冲突已解决，不能再升级")
        current = rec.level
        target = current + 1
        if force:
            target = min(4, max(target, current + 1, 2))
        if target > 4:
            from ..errors import Conflict
            raise Conflict("escalation_ceiling", "已达 L4（全局指令修正），无更高层级")
        rec.level = target
        rec.status = "escalated" if target >= 3 else "open"
        rec.resolution_note = (note or rec.resolution_note)[:2000]
        rec.resolved_at = None
        self.s.flush()
        self.audit.append(
            actor, "claw.conflict.escalated", rec.id,
            {"from_level": current, "to_level": target, "force": force,
             "note": note[:200],
             "level_label": ESCALATION_LEVELS[target - 1]},
        )
        return rec

    def resolve(self, actor: Actor, conflict_id: str, *, note: str = "") -> ClawConflictRecord:
        rec = self.s.get(ClawConflictRecord, conflict_id)
        if rec is None:
            from ..errors import NotFound
            raise NotFound("conflict_not_found", "Conflict not found", 404)
        rec.status = "resolved"
        rec.resolution_note = (note or rec.resolution_note)[:2000]
        from ...db.types import utcnow
        rec.resolved_at = utcnow()
        self.s.flush()
        self.audit.append(
            actor, "claw.conflict.resolved", rec.id,
            {"level": rec.level, "note": note[:200]},
        )
        return rec

    def open_conflicts(self) -> list[ClawConflictRecord]:
        """当前未解决冲突（健康仪表盘 C-机制-03 的数据源）。"""
        return list(self.s.execute(
            select(ClawConflictRecord)
            .where(ClawConflictRecord.status != "resolved")
            .order_by(ClawConflictRecord.created_at.desc())
            .limit(200)
        ).scalars().all())
