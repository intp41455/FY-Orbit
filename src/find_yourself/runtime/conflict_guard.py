"""A-Claw六类冲突 × 单循环执行主干 · 冲突闸门（B7「冲突检测挂主链」）。

接线前的现场
------------
:class:`~find_yourself.services.claw.conflicts.ConflictDetector` 的检测能力完整，
但 ``runtime/`` 对它的引用数是 0——冲突只会被「记一笔」，拦不住任何东西。
本模块把它接到**两个真实执行点**，并给出「拦」与「降」两级落地：

===================== ========================== ============================
 闸门                  位置                        语义
===================== ========================== ============================
 ``pre_execution``    内核 ``run`` 图启动前       阻断级 → 抛 ``ConflictDetected``
                                                （图不启动、零花费）；降级级 →
                                                改写任务体（丢弃自相矛盾的结论、
                                                脱敏人设禁用话术、标记无归属动作）
                                                并留痕
 ``pre_action``       图内每个执行节点调用模型前 阻断级 → 抛 ``ConflictDetected``
                                                （**动作不发生、网关一次不被调用**）；
                                                降级级 → 记进节点产出 ``degradations``
 ``post_action``      模型文本落地后               人设禁用话术 → 真实脱敏改写文本
===================== ========================== ============================

为什么状态是进程级
------------------
C1 管辖权冲突的本质是**跨请求**的：「A 刚领了这份报告，B 也来写」。
``POST /api/runtime/run`` 每次请求都新建内核，所以管辖登记表必须活在内核之外，
否则跨请求的争抢永远检不出来。与 ``runtime/hooks.py`` 的
``get_default_hook_bus()`` 同一形态（进程内单例 + 测试用 reset）。

**数据隔离**：登记表按 ``owner_id`` 分桶，跨 owner 物理不可见（铁律 6）。

降级不是静默
------------
降级记录走三条可见通道，缺一不可：
1. ``AuditService`` 挂帧 ``claw.conflict.degraded``；
2. 任务体 / 节点产出的 ``degradations`` 字段（可被 API/前端读到）；
3. 内核 hook 轨迹的 ``conflict.detected`` 事件（``GET /api/runtime/hooks/events`` 可见）。

诚实边界
--------
检测需要**调用方声明事实**：``agent`` / ``agent_role`` / ``target`` / ``action`` /
``claims`` 缺省时不臆造。默认策略下不会误伤任何既有任务（无声明 = 无信号 = 放行），
一旦任务声明了上述事实，冲突就会真的被拦下或降级。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping

from ..identity import Actor
from ..services.claw.conflicts import (
    CONFLICT_BLOCK_CODE,
    ConflictDetected,
    ConflictDetector,
    ConflictVerdict,
    DetectionHit,
    DetectionSignal,
    severity_of,
)

#: 闸门阶段标签（进审计 details，便于按阶段排查）
PHASE_PRE_EXECUTION = "pre_execution"
PHASE_PRE_ACTION = "pre_action"
PHASE_POST_ACTION = "post_action"

#: 人设禁用话术的脱敏占位符（脱敏是真实改写，不是打标记）
REDACTION = "[已按人设禁用话术规则脱敏]"

#: 任务体里可用于构造检测信号的字段别名（首个命中者生效）
_FIELD_AGENT = ("agent", "agent_id")
_FIELD_ROLE = ("agent_role", "role")
_FIELD_TARGET = ("target",)
_FIELD_ACTION = ("action",)
_FIELD_CLAIMS = ("claims",)


@dataclass(frozen=True)
class Degradation:
    """一条可见的降级记录（结构固定，进 state / 审计 / API）。"""

    conflict_class: str
    rule: str
    phase: str
    detail: str
    effect: str

    def to_public(self) -> dict[str, Any]:
        return {
            "conflict_class": self.conflict_class,
            "rule": self.rule,
            "phase": self.phase,
            "severity": severity_of(self.conflict_class),
            "detail": self.detail[:200],
            "effect": self.effect,
        }


def _first(state: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = state.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def task_signal(state: Mapping[str, Any]) -> DetectionSignal:
    """从**任务体**提取检测信号：只认调用方声明的事实，不臆造。

    * ``agent`` ← ``agent`` / ``agent_id``
    * ``agent_role`` ← ``agent_role`` / ``role``
    * ``target`` ← ``target``（未声明则不参与 C1 管辖裁决）
    * ``action`` ← ``action``（**不**回退到 route：route 是策略不是动作，
      用 route 兜底会让每一次正常任务都被判成「无归属动作」的噪声）
    * ``claims`` ← ``claims``；``text`` ← ``goal``
    """
    target = _first(state, _FIELD_TARGET)
    if target is None:
        refs = state.get("input_refs") or []
        if isinstance(refs, list) and refs:
            target = ",".join(str(r) for r in refs)
    claims = state.get("claims")
    return DetectionSignal(
        agent=str(_first(state, _FIELD_AGENT) or ""),
        agent_role=str(_first(state, _FIELD_ROLE) or ""),
        action=str(_first(state, _FIELD_ACTION) or ""),
        target=str(target or ""),
        claims=list(claims) if isinstance(claims, list) else [],
        text=str(state.get("goal") or ""),
    )


def action_signal(
    state: Mapping[str, Any], *, action: str, role: str, text: str = ""
) -> DetectionSignal:
    """从**执行节点的动作**提取信号：动作与角色由节点自己声明（不可伪造）。"""
    task = task_signal(state)
    return DetectionSignal(
        agent=task.agent,
        agent_role=task.agent_role or role,
        action=action,
        target=task.target,
        claims=task.claims,
        text=text or task.text,
    )


class ConflictGuard:
    """把 :class:`ConflictDetector` 变成「拦得住」的闸门。

    判定与执行分离：本类只负责**查表 → 裁决 → raise / 改写**，
    检测规则本身仍由 ``services/claw`` 的检测器持有（不重复实现、不绕开）。
    """

    def __init__(
        self,
        *,
        auditor: Any | None = None,
        actor: Actor | None = None,
        detectors: dict[str, ConflictDetector] | None = None,
        detector_policy: dict[str, Any] | None = None,
    ):
        self._auditor = auditor
        self._actor = actor
        # owner_id -> ConflictDetector：管辖登记表按 owner 分桶（跨 owner 不可见）
        self._detectors: dict[str, ConflictDetector] = dict(detectors or {})
        self._policy = dict(detector_policy or {})

    # -- 检测器分桶 --------------------------------------------------------- #

    def detector_for(self, owner_id: str = "") -> ConflictDetector:
        key = owner_id or "anonymous"
        found = self._detectors.get(key)
        if found is None:
            # 每个 owner 一个独立检测器：策略相同、管辖登记表互不可见。
            found = ConflictDetector(**self._policy)
            self._detectors[key] = found
        return found

    @property
    def owners(self) -> list[str]:
        return sorted(self._detectors)

    def reset(self) -> None:
        """清空管辖登记表（进程重启即失效；测试与人工解锁用）。"""
        self._detectors.clear()

    def bind(self, *, auditor: Any | None = None, actor: Actor | None = None) -> ConflictGuard:
        """注入审计通道（内核在构造时调用）。

        与 ``graph.ROUTE_RUNTIME`` 的「后设覆盖、None 不清空」同一形态：
        进程级默认闸门被多个内核共用，谁最后拿到审计服务谁负责挂帧，
        ``None`` **不**清空已绑定的通道（避免一个无审计的内核把别人的擦掉）。
        """
        if auditor is not None:
            self._auditor = auditor
        if actor is not None:
            self._actor = actor
        return self

    # -- 裁决 --------------------------------------------------------------- #

    def evaluate(self, signal: DetectionSignal, *, owner_id: str = "") -> ConflictVerdict:
        """跑规则 + 两级裁决（不抛异常，可单独断言）。"""
        return self.detector_for(owner_id).evaluate(signal)

    def enforce(
        self,
        signal: DetectionSignal,
        *,
        phase: str,
        task_id: str = "",
        owner_id: str = "",
        effect: str = "rejected",
    ) -> list[Degradation]:
        """闸门本体：阻断级 **raise**；降级级返回可见记录（由调用方落地）。"""
        verdict = self.evaluate(signal, owner_id=owner_id)
        if verdict.blocked:
            self._audit_block(phase, task_id, verdict, owner_id)
            raise ConflictDetected(
                _block_message(verdict.blocking, phase), hits=verdict.blocking
            )
        return self.degradations_of(verdict, phase=phase, effect=effect)

    def degradations_of(
        self, verdict: ConflictVerdict, *, phase: str, effect: str
    ) -> list[Degradation]:
        records = [
            Degradation(
                conflict_class=hit.conflict_class,
                rule=hit.rule,
                phase=phase,
                detail=hit.message,
                effect=effect,
            )
            for hit in verdict.degrading
        ]
        if records:
            self._audit_degrade(phase, records)
        return records

    # -- 降级动作（真实改写，不是打标记） ----------------------------------- #

    def drop_contradicting_claims(
        self, records: list[Degradation], claims: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """C3 自我冲突：保留每个结论对象的**首次**取值，丢弃与之相反的后续取值。"""
        if not claims:
            return claims
        if not any(r.conflict_class == "self_contradiction" for r in records):
            return claims
        from ..services.claw.gates import _is_contradiction  # 同源判定，不另造「相反」语义

        kept: list[dict[str, Any]] = []
        seen: dict[str, Any] = {}
        for claim in claims:
            if not isinstance(claim, dict) or not claim.get("key"):
                kept.append(claim)
                continue
            key = str(claim["key"])
            previous = seen.get(key, _MISSING)
            if previous is not _MISSING and _is_contradiction(previous, claim.get("value")):
                records.append(Degradation(
                    conflict_class="self_contradiction", rule="claim_dropped",
                    phase=PHASE_PRE_EXECUTION, detail=f"丢弃相反结论 {key!r}",
                    effect="claim_dropped",
                ))
                continue
            if previous is _MISSING:
                seen[key] = claim.get("value")
            kept.append(claim)
        return kept

    def redact_persona(self, records: list[Degradation], text: str, *, role: str) -> str:
        """C4 人设冲突：把禁用话术**真实替换**掉（原文不再出现在产出里）。"""
        if not text:
            return text
        banned = self._active_detector().persona_banned.get(role, ())
        out = text
        for phrase in banned:
            if phrase and phrase in out:
                out = out.replace(phrase, REDACTION)
                records.append(Degradation(
                    conflict_class="persona", rule="persona_redacted",
                    phase=PHASE_POST_ACTION,
                    detail=f"角色 {role!r} 产出命中禁用话术并已脱敏: {phrase[:40]!r}",
                    effect="text_redacted",
                ))
        return out

    def mark_unowned(self, records: list[Degradation], state: dict[str, Any]) -> dict[str, Any]:
        """C2 边界模糊：动作没有声明归属 → 在任务体上留下不可忽略的归属缺口标记。"""
        if not any(r.conflict_class == "boundary" for r in records):
            return state
        action = str(state.get("action") or "")
        if action:
            state["unowned_action"] = action
        return state

    # -- 审计（失败不炸主干，与 kernel._audit 同语义） ------------------------ #

    def _active_detector(self) -> ConflictDetector:
        """当前 actor 所属 owner 的检测器（策略读取用；不改管辖登记表）。"""
        return self.detector_for(getattr(self._actor, "owner_id", "") or "")

    def _append(self, action: str, target: str, details: dict[str, Any]) -> None:
        if self._auditor is None:
            return
        actor = self._actor if self._actor is not None else Actor.owner("kernel")
        try:
            self._auditor.append(actor, action, target, details)
        except Exception:  # noqa: BLE001 —— 审计失败不炸主干
            pass

    def _audit_block(
        self, phase: str, task_id: str, verdict: ConflictVerdict, owner_id: str
    ) -> None:
        self._append(
            "claw.conflict.blocked", task_id or None,
            {"phase": phase, "code": CONFLICT_BLOCK_CODE, "owner_id": owner_id,
             **verdict.as_public()},
        )

    def _audit_degrade(self, phase: str, records: list[Degradation]) -> None:
        self._append(
            "claw.conflict.degraded", None,
            {"phase": phase, "count": len(records),
             "items": [r.to_public() for r in records][:8]},
        )


_MISSING = object()


def _block_message(hits: list[DetectionHit], phase: str) -> str:
    head = hits[0]
    return (
        f"{head.conflict_class} 冲突（{head.rule}）："
        f"{head.message[:180]}｜已在 {phase} 阶段拒绝执行"
    )


# ---------------------------------------------------------------------------
# 进程级默认单例（管辖权跨请求累积；与 hooks.get_default_hook_bus 同一形态）
# ---------------------------------------------------------------------------

_DEFAULT_GUARD: ConflictGuard | None = None
_GUARD_LOCK = threading.Lock()
_DEFAULT_POLICY: dict[str, Any] = {}


def configure_default_conflict_policy(**policy: Any) -> None:
    """配置默认单例的检测策略（人设禁用话术 / 能力清单 / 事实基线）。

    必须在**首次** :func:`get_conflict_guard` 之前调用；进程内后设无效会抛错，
    避免「看起来配了其实没生效」的假配置。
    """
    global _DEFAULT_POLICY
    with _GUARD_LOCK:
        if _DEFAULT_GUARD is not None:
            raise RuntimeError(
                "default conflict guard already in use; call "
                "reset_conflict_registry() before re-configuring"
            )
        _DEFAULT_POLICY = dict(policy)


def get_conflict_guard(**policy: Any) -> ConflictGuard:
    """进程级默认闸门（首次调用创建；等价于 hook 总线的单例约定）。"""
    global _DEFAULT_GUARD
    with _GUARD_LOCK:
        if _DEFAULT_GUARD is None:
            merged = dict(_DEFAULT_POLICY)
            merged.update(policy)
            _DEFAULT_GUARD = ConflictGuard(detector_policy=merged)
        return _DEFAULT_GUARD


def reset_conflict_registry() -> None:
    """测试专用：清空默认单例与管辖登记表。"""
    global _DEFAULT_GUARD
    with _GUARD_LOCK:
        _DEFAULT_GUARD = None
        _DEFAULT_POLICY = {}
