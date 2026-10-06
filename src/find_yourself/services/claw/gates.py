"""A-Claw架构-01/02/03/04 · 三层把关流水线。

第一层 **自审**（-02）：Agent 检查逻辑漏洞、与全局事实对齐、违反禁行规则，
自己改不往外抛；
第二层 **同角色交叉验证**（-03）：同角色另一 Agent 交叉审核，一致才往下，
不一致进辩论，辩不出升级；
第三层 **独立质检**（-04）：独立质检不干活专挑错——违规/前后矛盾/浪费资源
直接打回，甚至越级上报（不受主控管）。

流水线（-01）逐层短路：上一层不过，下一层不跑。每层裁决落
``ClawGateDecision`` + 审计哈希链挂帧（``claw.gate``）。

机检规则（增强-01 的可执行子集）：
* R1 自我冲突：同一结论对象（claim key）前后结论相反（布尔反转/数值异号）
  → block（这就是增强-01 验收给的样例规则）；
* R2 事实对齐：claim 与全局事实基线不一致 → block（升级语义由调用方定）；
* R3 禁行规则：产出文本命中禁行规则 → revise；
* R4 交叉一致性：双产出核心结论集不一致 → 按比例判 revise（进辩论）或
  escalate（辩不出）；
* R5 质检浪费检测：同任务同节点重复产出次数超阈值仍失败 → escalate。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...db.claw_models import ClawGateDecision
from ..actor import Actor
from ..audit import AuditService


class GateLayer(str, Enum):
    SELF_CHECK = "self_check"
    CROSS_VALIDATION = "cross_validation"
    INDEPENDENT_QA = "independent_qa"


class GateVerdict(str, Enum):
    PASS = "pass"
    REVISE = "revise"      # 打回给自己改（不外抛）
    REJECT = "reject"      # 违规/事实冲突，内容不可用
    ESCALATE = "escalate"  # 升级（辩论不出/越级上报）


@dataclass(frozen=True)
class GateFinding:
    rule: str
    message: str
    severity: str = "info"  # info / warn / block


@dataclass
class GateOutcome:
    layer: GateLayer
    verdict: GateVerdict
    findings: list[GateFinding] = field(default_factory=list)
    fact_keys_checked: list[str] = field(default_factory=list)
    decision_id: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is GateVerdict.PASS


def _claims_of(output: dict[str, Any]) -> list[dict[str, Any]]:
    """产出里的结论对象列表：``output["claims"] = [{key, value}, ...]``。"""
    claims = output.get("claims")
    return [c for c in claims if isinstance(c, dict) and c.get("key")] if isinstance(claims, list) else []


def _is_contradiction(a: Any, b: Any) -> bool | None:
    """判断两个结论值是否"相反"。True=相反 False=一致 None=不可判定（字符串等）。"""
    if isinstance(a, bool) and isinstance(b, bool):
        return a != b
    try:
        fa, fb = float(a), float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (fa > 0) != (fb > 0) if (fa != 0 and fb != 0) else (fa != fb)


def _values_differ(a: Any, b: Any) -> bool:
    """事实基线对齐用：与基线**必须相等**——不等即冲突（数值/布尔按值，其余按字符串）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) != bool(b)
    try:
        return float(a) != float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(a) != str(b)


class SelfCheckGate:
    """第一层：Agent 自审（-02）。查完自己改，不往外抛。"""

    def __init__(
        self,
        *,
        forbidden_rules: tuple[str, ...] = (),
        fact_lookup: Callable[[str], str | None] | None = None,
    ):
        self.forbidden_rules = tuple(forbidden_rules)
        self.fact_lookup = fact_lookup

    def check(self, *, task_id: str, agent_role: str, output: dict[str, Any]) -> GateOutcome:
        findings: list[GateFinding] = []
        fact_keys: list[str] = []

        # R1 自我冲突（增强-01 样例规则的机检实现）
        by_key: dict[str, list[Any]] = {}
        for c in _claims_of(output):
            by_key.setdefault(str(c["key"]), []).append(c.get("value"))
        for key, values in by_key.items():
            for i in range(len(values) - 1):
                contra = _is_contradiction(values[i], values[i + 1])
                if contra:
                    findings.append(GateFinding(
                        rule="self_contradiction",
                        message=f"结论 {key!r} 前后相反: {values[i]!r} → {values[i + 1]!r}",
                        severity="block",
                    ))

        # R2 与全局事实基线对齐（基线是权威值——不等即冲突）
        for c in _claims_of(output):
            if self.fact_lookup is None:
                break
            baseline = self.fact_lookup(str(c["key"]))
            if baseline is None:
                continue
            fact_keys.append(str(c["key"]))
            if _values_differ(baseline, c.get("value")):
                findings.append(GateFinding(
                    rule="fact_baseline_conflict",
                    message=f"结论 {c['key']!r} 与事实基线冲突: 基线={baseline!r} 产出={c.get('value')!r}",
                    severity="block",
                ))

        # R3 禁行规则
        text = str(output.get("text") or "")
        for rule in self.forbidden_rules:
            if rule and rule in text:
                findings.append(GateFinding(
                    rule="forbidden_rule",
                    message=f"产出命中禁行规则: {rule[:80]}",
                    severity="warn",
                ))

        has_block = any(f.severity == "block" for f in findings)
        has_warn = any(f.severity == "warn" for f in findings)
        if has_block:
            # 事实冲突=内容不可用；自我冲突=自己改（revise）
            if any(f.rule == "fact_baseline_conflict" for f in findings):
                verdict = GateVerdict.REJECT
            else:
                verdict = GateVerdict.REVISE
        elif has_warn:
            verdict = GateVerdict.REVISE
        else:
            verdict = GateVerdict.PASS
        return GateOutcome(layer=GateLayer.SELF_CHECK, verdict=verdict,
                           findings=findings, fact_keys_checked=fact_keys)


class CrossValidationGate:
    """第二层：同角色交叉验证（-03）。一致才往下；不一致进辩论，辩不出升级。"""

    def check(
        self, *, task_id: str, agent_role: str,
        primary: dict[str, Any], cross: dict[str, Any],
    ) -> GateOutcome:
        p_claims = {str(c["key"]): c.get("value") for c in _claims_of(primary)}
        x_claims = {str(c["key"]): c.get("value") for c in _claims_of(cross)}
        findings: list[GateFinding] = []
        if not p_claims:
            return GateOutcome(layer=GateLayer.CROSS_VALIDATION, verdict=GateVerdict.PASS,
                               findings=[GateFinding(
                                   rule="no_claims",
                                   message="主产出无可比对结论，交叉验证放行",
                                   severity="info")])
        common = sorted(set(p_claims) & set(x_claims))
        if not common:
            findings.append(GateFinding(
                rule="no_overlap", message="两份产出无任何共同结论对象",
                severity="warn"))
            verdict = GateVerdict.ESCALATE   # 辩不出：连比的对象都对不上
        else:
            # 交叉验证的共识语义是严格的：同一结论对象，两份产出结论必须一致
            #（字符串分歧也算"辩不出"——正是 -03 要升级的场景）
            contradictions = [k for k in common if p_claims[k] != x_claims[k]]
            if contradictions:
                verdict = GateVerdict.ESCALATE   # 核心结论相反且辩不出 → 升级
                for k in contradictions:
                    findings.append(GateFinding(
                        rule="cross_contradiction",
                        message=f"交叉验证结论 {k!r} 相反: {p_claims[k]!r} vs {x_claims[k]!r}",
                        severity="block"))
            elif len(common) < len(p_claims):
                missing = sorted(set(p_claims) - set(x_claims))
                findings.append(GateFinding(
                    rule="partial_disagreement",
                    message=f"部分结论未被交叉确认（需辩论/复核）: {missing[:5]}",
                    severity="warn"))
                verdict = GateVerdict.REVISE
            else:
                verdict = GateVerdict.PASS
        return GateOutcome(layer=GateLayer.CROSS_VALIDATION, verdict=verdict,
                           findings=findings)


class IndependentQAGate:
    """第三层：独立质检监督（-04）。不干活专挑错，可越级上报。"""

    def __init__(self, *, forbidden_rules: tuple[str, ...] = (), waste_threshold: int = 3):
        self.forbidden_rules = tuple(forbidden_rules)
        self.waste_threshold = waste_threshold

    def check(
        self, *, task_id: str, agent_role: str,
        primary: dict[str, Any], attempts: int = 1,
    ) -> GateOutcome:
        findings: list[GateFinding] = []
        text = str(primary.get("text") or "")
        violations = [r for r in self.forbidden_rules if r and r in text]
        for r in violations:
            findings.append(GateFinding(
                rule="qa_violation", message=f"质检命中违规: {r[:80]}",
                severity="block"))
        # 前后矛盾（复用自我冲突规则——质检视角复核）
        by_key: dict[str, list[Any]] = {}
        for c in _claims_of(primary):
            by_key.setdefault(str(c["key"]), []).append(c.get("value"))
        for key, values in by_key.items():
            for i in range(len(values) - 1):
                if _is_contradiction(values[i], values[i + 1]):
                    findings.append(GateFinding(
                        rule="qa_contradiction",
                        message=f"质检发现前后矛盾: {key!r}",
                        severity="block"))
        if attempts >= self.waste_threshold:
            findings.append(GateFinding(
                rule="waste",
                message=f"同任务重复产出 {attempts} 次仍未收敛——浪费资源，越级上报",
                severity="warn"))
        has_block = any(f.severity == "block" for f in findings)
        if violations:
            verdict = GateVerdict.REJECT
        elif has_block:
            verdict = GateVerdict.REJECT
        elif attempts >= self.waste_threshold:
            verdict = GateVerdict.ESCALATE   # 越级上报语义
        else:
            verdict = GateVerdict.PASS
        return GateOutcome(layer=GateLayer.INDEPENDENT_QA, verdict=verdict,
                           findings=findings)


@dataclass
class PipelineResult:
    verdict: GateVerdict
    outcomes: list[GateOutcome] = field(default_factory=list)
    task_id: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is GateVerdict.PASS

    @property
    def blocked(self) -> bool:
        return self.verdict in (GateVerdict.REJECT, GateVerdict.ESCALATE)


class ThreeLayerPipeline:
    """架构-01 三层把关：自审 → 交叉验证 → 独立质检，逐层短路、逐层留痕。"""

    def __init__(
        self,
        session: Session,
        audit: AuditService,
        actor: Actor,
        *,
        forbidden_rules: tuple[str, ...] = (),
        fact_lookup: Callable[[str], str | None] | None = None,
        waste_threshold: int = 3,
        owner_id: str = "",
    ):
        self.session = session
        self.audit = audit
        self.actor = actor
        self.owner_id = owner_id or getattr(actor, "owner_id", "") or ""
        self.self_gate = SelfCheckGate(forbidden_rules=forbidden_rules, fact_lookup=fact_lookup)
        self.cross_gate = CrossValidationGate()
        self.qa_gate = IndependentQAGate(forbidden_rules=forbidden_rules,
                                         waste_threshold=waste_threshold)

    def _record(self, outcome: GateOutcome, *, task_id: str, agent_role: str) -> None:
        row = ClawGateDecision(
            id=uuid.uuid4().hex,
            owner_id=self.owner_id,
            task_id=task_id,
            agent_role=agent_role[:64],
            layer=outcome.layer.value,
            verdict=outcome.verdict.value,
            findings={"items": [
                {"rule": f.rule, "message": f.message[:300], "severity": f.severity}
                for f in outcome.findings
            ]},
            fact_keys_checked={"keys": outcome.fact_keys_checked[:20]},
        )
        self.session.add(row)
        self.session.flush()
        outcome.decision_id = row.id
        self.audit.append(
            self.actor, "claw.gate", row.id,
            {"layer": outcome.layer.value, "verdict": outcome.verdict.value,
             "task_id": task_id, "agent_role": agent_role,
             "findings": len(outcome.findings)},
        )

    def run(
        self,
        *,
        task_id: str,
        agent_role: str,
        primary_output: dict[str, Any],
        cross_output: dict[str, Any] | None = None,
        attempts: int = 1,
    ) -> PipelineResult:
        """跑三层把关。上一层不过即短路（自己改不外抛 / 升级）。"""
        result = PipelineResult(verdict=GateVerdict.PASS, task_id=task_id)

        # 第一层：自审（必须）
        o1 = self.self_gate.check(task_id=task_id, agent_role=agent_role,
                                  output=primary_output)
        self._record(o1, task_id=task_id, agent_role=agent_role)
        result.outcomes.append(o1)
        if o1.verdict is not GateVerdict.PASS:
            result.verdict = o1.verdict
            return result

        # 第二层：交叉验证（提供了交叉产出才跑）
        if cross_output is not None:
            o2 = self.cross_gate.check(task_id=task_id, agent_role=agent_role,
                                       primary=primary_output, cross=cross_output)
            self._record(o2, task_id=task_id, agent_role=agent_role)
            result.outcomes.append(o2)
            if o2.verdict is not GateVerdict.PASS:
                result.verdict = o2.verdict
                return result

        # 第三层：独立质检（终闸）
        o3 = self.qa_gate.check(task_id=task_id, agent_role=agent_role,
                                primary=primary_output, attempts=attempts)
        self._record(o3, task_id=task_id, agent_role=agent_role)
        result.outcomes.append(o3)
        result.verdict = o3.verdict
        return result
