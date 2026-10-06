"""统一能力网关 · 审计集成 (补齐包1 A-能力网关-06)。

**全部能力调用（本机动作 + 跨 agent 调度）的裁决与变更都进既有审计哈希链**
（:class:`find_yourself.services.audit.AuditService`）——不建第二套审计，
不重写哈希链。agent 不可篡改性由该链的 ``hash``/``previous_hash`` 与独立
anchor store（``services/anchor_store.py``）保证：改写任何一帧都会被
``verify()`` 抓出，抹平哈希需要同时攻破主库与独立锚库两个写权限面。

本模块在既有链之上提供能力域的三件事：

1. **记录**：每次裁决（放行与拒绝都记）、每次管理变更（授予/撤销/切档/级别开关）
   各一帧，``action`` 统一 ``capability.*`` 前缀，``details`` 带任务号与时间戳，
   因此「按任务 / 按时间 / 按能力」三种检索维度都可用；
2. **检索**：:meth:`CapabilityAudit.search`（owner 只能看自己的帧，复用审计链的
   actor 归属隔离语义）；
3. **回滚**：可逆动作（``spec.reversible``）的调用方可登记撤销回调，
   :meth:`CapabilityAudit.rollback` 执行并留 ``capability.rollback`` 审计帧。
   回滚登记是进程内的（诚实边界：进程重启后登记消失，历史帧仍可查但不可再执行）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..actor import Actor
from ..audit import AuditService
from ...db.models import AuditEvent
from ...db.types import utcnow

#: 审计 action 前缀（检索键之一）。
CAPABILITY_ACTION_PREFIX = "capability."
ACTION_DECISION = "capability.decision"
ACTION_GRANT = "capability.grant"
ACTION_REVOKE = "capability.revoke"
ACTION_PROFILE_CHANGE = "capability.profile_change"
ACTION_LEVEL_CHANGE = "capability.level_change"
ACTION_DEGRADE = "capability.degrade"
ACTION_ROLLBACK = "capability.rollback"

#: details 里承载各检索维度与时间戳的键。
KEY_TS = "ts"
KEY_TASK = "task_id"
KEY_CAPABILITY = "capability"
KEY_DECISION = "decision"
KEY_PROFILE = "profile"
KEY_SUBJECT = "subject"
KEY_RESOURCE = "resource"
KEY_LEVEL = "level"
KEY_REVERSAL_ID = "reversal_id"


def identity_of(actor: Actor) -> str | None:
    """审计链的 actor 归属值——**复用** AuditService.identity_of 的同一真相。

    （收编说明：归属隔离谓词只在 services/audit.py 定义一处；本别名仅为
    能力域调用方便，语义漂移会在 AuditService 的既有测试里立刻暴露。）
    """
    return AuditService.identity_of(actor)


class CapabilityAudit:
    """能力域审计门面：记录进哈希链 + 三维检索 + 可逆动作回滚登记。"""

    def __init__(self, audit: AuditService):
        self._audit = audit
        # reversal_id -> (undo 回调, 描述)。进程内登记；一次性（回滚后即除名）。
        self._reversals: dict[str, tuple[Callable[[], Any], str]] = {}
        self._lock = threading.Lock()

    # -- 记录 -------------------------------------------------------------------

    def record_decision(self, actor: Actor, decision) -> AuditEvent:
        """每次能力裁决（放行+拒绝）入链。"""
        req = decision.request
        details = {
            KEY_TS: utcnow().isoformat(),
            KEY_CAPABILITY: req.capability,
            KEY_SUBJECT: req.subject,
            KEY_RESOURCE: req.resource,
            KEY_LEVEL: decision.level,
            KEY_TASK: req.task_id,
            KEY_DECISION: "allowed" if decision.allowed else "denied",
            KEY_PROFILE: decision.profile,
            "code": decision.code,
            "reasons": list(decision.reasons),
            "gates": {
                v.gate: {"allowed": v.allowed, "reason": v.reason}
                for v in decision.verdicts
            },
        }
        if decision.grant_id:
            details["grant_id"] = decision.grant_id
        if req.message_id:
            details["message_id"] = req.message_id
        if decision.escalated:
            details["escalated"] = True
        return self._audit.append(actor, ACTION_DECISION, req.capability, details)

    def record_admin(self, actor: Actor, action: str, target: str | None, details: dict) -> AuditEvent:
        """管理变更（授予/撤销/切档/级别开关/降级）入链。"""
        body = {KEY_TS: utcnow().isoformat(), **details}
        return self._audit.append(actor, action, target, body)

    # -- 检索 -------------------------------------------------------------------

    def search(
        self,
        actor: Actor,
        *,
        capability: str | None = None,
        task_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        decision: str | None = None,
        limit: int = 200,
    ) -> list[AuditEvent]:
        """按 任务 / 时间 / 能力（可组合）检索能力审计帧，按 seq 升序返回。

        owner/service 只能看到自己名下的帧（actor 归属过滤在 SQL 谓词里，
        与 AuditService 的提问↔trace 索引同一隔离模型）。时间过滤在 Python
        侧对 ``details[ts]`` 做（audit_events 无时间列；ISO-UTC 字符串可比较）。
        """
        identity = identity_of(actor)
        if identity is None:
            return []
        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.actor == identity,
                AuditEvent.action.like(f"{CAPABILITY_ACTION_PREFIX}%"),
            )
            .order_by(AuditEvent.seq.asc())
        )
        if capability is not None:
            stmt = stmt.where(AuditEvent.details[KEY_CAPABILITY].as_string() == capability)
        if task_id is not None:
            stmt = stmt.where(AuditEvent.details[KEY_TASK].as_string() == task_id)
        if decision is not None:
            stmt = stmt.where(AuditEvent.details[KEY_DECISION].as_string() == decision)
        rows = list(self._audit.s.execute(stmt.limit(max(int(limit), 1) * 4)).scalars())
        out: list[AuditEvent] = []
        for ev in rows:
            if since is not None or until is not None:
                ts = self._frame_ts(ev)
                if ts is None:
                    continue
                if since is not None and ts < since:
                    continue
                if until is not None and ts > until:
                    continue
            out.append(ev)
            if len(out) >= max(int(limit), 1):
                break
        return out

    @staticmethod
    def _frame_ts(ev: AuditEvent) -> datetime | None:
        raw = (ev.details or {}).get(KEY_TS)
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    # -- 可逆动作回滚 -------------------------------------------------------------

    def register_reversal(self, undo: Callable[[], Any], *, description: str) -> str:
        """登记一个可逆动作的撤销回调，返回 ``reversal_id``。

        调用方在获得放行并执行动作后登记；``reversal_id`` 会随后续审计帧
        （``details[reversal_id]``）一起入链，供回滚时核对。
        """
        rid = f"rv_{len(self._reversals) + 1:06d}_{utcnow().strftime('%H%M%S%f')}"
        with self._lock:
            self._reversals[rid] = (undo, description)
        return rid

    def peek_reversal(self, reversal_id: str) -> str | None:
        with self._lock:
            entry = self._reversals.get(reversal_id)
            return entry[1] if entry else None

    def rollback(self, actor: Actor, reversal_id: str) -> dict:
        """执行登记过的撤销回调并留审计帧。一次性：回滚后登记即除名。"""
        with self._lock:
            entry = self._reversals.pop(reversal_id, None)
        if entry is None:
            raise KeyError(f"unknown or already-rolled-back reversal: {reversal_id}")
        undo, description = entry
        result = undo()
        self.record_admin(
            actor, ACTION_ROLLBACK, reversal_id,
            {"description": description, "result": _safe_repr(result)},
        )
        return {"reversal_id": reversal_id, "description": description, "result": result}


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)[:200]
    except Exception:  # noqa: BLE001 - 审计帧绝不因回滚结果的表示失败而中断
        return "<unrepresentable>"
