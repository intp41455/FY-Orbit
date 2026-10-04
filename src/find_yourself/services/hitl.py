"""Human-in-the-loop 中断与恢复（需求 12）。

系统已有一套「记忆审批」（``MemoryService.activate_approved`` /
``deny_hypothesis``），但那管的是**记忆条目的状态**，不是**执行流**。本模块
管的是后者：Agent 跑到某个检查点，发现需要人来拍一下，于是把「我停在这儿了、
我打算这么做、你可以选这些」落库并**真的停住**；人给了答复之后，再按答复
继续或者取消。

四个动作，对应执行的四个时刻：

* :meth:`HitlInterruptService.interrupt` —— 暂停。落库并返回中断 id。
* :meth:`HitlInterruptService.pending` —— 查某执行是否正卡在某检查点。
* :meth:`HitlInterruptService.list_pending` —— 列出待决中断（人的工作台视图）。
* :meth:`HitlInterruptService.decide` —— 恢复。按人的决策落地。

设计上的三条硬规矩：

1. **等待中与已决策是两个状态，不是两个字段。** ``pending`` 行按schema 保证
   没有decision；已决策的行按 schema 保证有 decision（见 ``hitl_models``）。
2. **重复决策被拒。** 决策走条件UPDATE（``WHERE status='pending'``），
   ``rowcount == 0`` 即冲突——两个并发决策只有一个能赢。
3. **决策必须来自声明过的选项。** 非法选项在服务层被拒，不靠调用方自觉。

审计：暂停与决策都 append 到哈希链（``AuditService``），因为「谁在什么时候
叫停了哪个执行、谁拍的板」是需要事后追责的事实。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.hitl_models import (
    HITL_DECIDED_STATUSES,
    HITL_TIMEOUT_DECISION,
    HitlInterrupt,
)
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, ValidationFailed


class HitlInterruptService:
    """暂停 / 查询 / 恢复。刻意保持小而完整——不是一个框架。"""

    def __init__(self, session: Session, audit: Any | None = None):
        self.session = session
        self.audit = audit

    # ------------------------------------------------------------------
    # 暂停
    # ------------------------------------------------------------------
    def interrupt(
        self,
        actor: Actor,
        execution_id: str,
        checkpoint: str,
        *,
        context: dict | None = None,
        options: list[dict] | list[str] | None = None,
        reason: str = "",
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """在 ``checkpoint`` 处暂停 ``execution_id``，返回一个中断 id。

        同一执行同时只允许一个待决中断——两次暂停意味着调用方在同一个执行上
        搞出了两条并行的等待路径，那不是HITL 该容忍的形状，直接 Conflict。
        """
        actor.require_authenticated()
        if not execution_id:
            raise ValidationFailed("execution_id is required")
        if not checkpoint:
            raise ValidationFailed("checkpoint is required")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValidationFailed("timeout_seconds must be positive when provided")

        norm_options = self._normalise_options(options)
        if not norm_options:
            raise ValidationFailed(
                "options must offer at least one decision; an interrupt nobody "
                "can answer is not a question"
            )

        existing = self._current(execution_id)
        if existing is not None:
            raise Conflict(
                "interrupt_pending",
                f"Execution {execution_id} is already waiting at checkpoint "
                f"{existing.checkpoint} (interrupt {existing.id})",
            )

        row = HitlInterrupt(
            id=f"hitl-{uuid4().hex[:12]}",
            owner_id=actor.owner_id or actor.service_id,
            execution_id=execution_id,
            checkpoint=checkpoint,
            context=dict(context or {}),
            options=norm_options,
            status="pending",
            reason=reason or "",
            expires_at=(
                utcnow() + timedelta(seconds=timeout_seconds)
                if timeout_seconds is not None
                else None
            ),
        )
        self.session.add(row)
        self.session.flush()
        self._audit(actor, "hitl.interrupted", row.id,
                    {"execution_id": execution_id, "checkpoint": checkpoint})
        return self._view(row)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, actor: Actor, interrupt_id: str) -> dict[str, Any]:
        """取单个中断（含已决策的），不存在则 NotFound。"""
        actor.require_authenticated()
        row = self._row(interrupt_id)
        self._require_visible(actor, row)
        return self._view(row)

    def pending(self, actor: Actor, execution_id: str) -> dict[str, None] | dict[str, Any]:
        """某执行当前是否处于暂停态。

        被卡住且未超时 → 返回该中断视图（含 ``status='pending'``）；
        没有待决中断、待决的那个已过期、或不属于该调用者 → ``None``。

        这里对不可见行返回 ``None`` 而不是 NotFound：这是一个「我此刻卡住了吗」
        的轮询查询，调用方要的是布尔答案。确认某条具体记录的操作走 ``get`` /
        ``decide``，那两条才按「不存在」处理。
        """
        actor.require_authenticated()
        row = self._current(execution_id)
        if row is None or not self._is_visible(actor, row):
            return None
        if self._sweep_expired(actor, row):
            return None
        return self._view(row)

    def list_pending(
        self, actor: Actor, *, execution_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出待决中断——人的工作台「等我拍板」列表。"""
        actor.require_authenticated()
        stmt = select(HitlInterrupt).where(HitlInterrupt.status == "pending")
        if execution_id is not None:
            stmt = stmt.where(HitlInterrupt.execution_id == execution_id)
        rows = list(self.session.execute(stmt.order_by(HitlInterrupt.created_at)).scalars())

        out: list[dict[str, Any]] = []
        for row in rows:
            if not self._is_visible(actor, row):
                continue
            if self._sweep_expired(actor, row):
                continue
            out.append(self._view(row))
        return out

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------
    def decide(
        self,
        actor: Actor,
        interrupt_id: str,
        decision: str,
        *,
        resolution: dict | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """按人的决策把中断落定，返回更新后的中断视图。

        只有 owner 能拍板（``require_owner``）——执行自己不能给自己放行。

        ``decision`` 必须等于该中断 ``options`` 里某个 ``value``；否则
        ValidationFailed。已经决策过的再决策 → Conflict。

        ``expected_version`` 是给「人看过一版上下文之后才拍板」用的乐观锁：
        传入时若行已不是该version（暂停后上下文被改过），报 Conflict 而不是
        基于**人没看过的那份上下文**拍板。省略则不做该检查。
        """
        actor.require_owner()
        row = self._row(interrupt_id)
        self._require_visible(actor, row)

        if expected_version is not None and row.version != expected_version:
            raise Conflict(
                "version_changed",
                f"Interrupt {interrupt_id} is at version {row.version}, "
                f"expected {expected_version}; the context you reviewed has "
                "changed since — re-read it before deciding",
            )
        if row.status != "pending":
            raise Conflict(
                "already_decided",
                f"Interrupt {interrupt_id} was already decided "
                f"(status={row.status}, decision={row.decision})",
            )
        if row.expires_at is not None and row.expires_at <= utcnow():
            # 先把超时落盘，再报错——否则这次调用拒绝了，但库里仍是pending，
            # 下一个人还能对同一行拍板，超时就是个谎言。
            self._mark_expired(actor, row)
            raise Conflict(
                "interrupt_expired",
                f"Interrupt {interrupt_id} passed its deadline unanswered",
            )
        allowed = {o["value"] for o in (row.options or [])}
        if decision not in allowed:
            raise ValidationFailed(
                f"decision {decision!r} is not one of the offered options: "
                f"{sorted(allowed)}"
            )

        # 条件 UPDATE：只有仍处于 pending 的那一行会被命中，重复/并发决策
        # 的第二个请求拿到 rowcount == 0，据此拒绝。
        #
        # version 一并写进 WHERE：上面的读与这里的写之间仍有窗口，
        # 若不把「人看过的那个 version」也作为条件，另一进程在窗口内改了
        # 上下文（version+1），这里仍会命中，等于让人对没看过的版本拍板。
        stmt = update(HitlInterrupt).where(
            HitlInterrupt.id == interrupt_id,
            HitlInterrupt.status == "pending",
        )
        if expected_version is not None:
            stmt = stmt.where(HitlInterrupt.version == expected_version)
        res = self.session.execute(
            stmt.values(
                status=self._status_for(decision),
                decision=decision,
                resolution=dict(resolution) if resolution else None,
                decided_by=actor.owner_id,
                decided_at=utcnow(),
                updated_at=utcnow(),
                version=HitlInterrupt.version + 1,
            )
        )
        if res.rowcount == 0:
            # 区分「被别人抢了」与「上下文变了」，否则调用方无法正确处理：
            # 前者重试即可，后者必须让人重新看一遍。
            current = self.session.execute(
                select(HitlInterrupt.version, HitlInterrupt.status).where(
                    HitlInterrupt.id == interrupt_id
                )
            ).one_or_none()
            if (
                expected_version is not None
                and current is not None
                and current[1] == "pending"
                and current[0] != expected_version
            ):
                raise Conflict(
                    "version_changed",
                    f"Interrupt {interrupt_id} moved to version {current[0]} "
                    f"while you were deciding; re-read it before deciding",
                )
            raise Conflict(
                "already_decided",
                f"Interrupt {interrupt_id} was decided concurrently",
            )
        self.session.flush()
        self._audit(actor, "hitl.decided", interrupt_id,
                    {"decision": decision, "status": self._status_for(decision)})
        return self._view(self._row(interrupt_id))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _status_for(decision: str) -> str:
        """决策值 → 终态。未显式取消/批准的一律记为 rejected（保守默认）。

        刻意不做「猜语义」的映射：选项语义由调用方定义，服务只区分显式的
        cancel/approve 与其余。猜错会让审计链记录一个假的决策类别。

        ⚠️ 本方法被 :mod:`find_yourself.services.team_approval` 直接引用
        （需求6 团队审批）。那边的状态流转与本处**同形**，但因为授权模型
        不同——团队审批允许「另一个成员」拍，而 :meth:`decide` 的
        ``_require_visible`` 只允许行owner 拍——所以它无法调用
        :meth:`decide`，只能复用这里的终态映射以免两份拷贝漂移。

        **若要改名/清理本方法，请一并更新那边**：那边把它显式挂在
        ``TeamApprovalService._status_mapper`` 上，并有
        ``tests/unit/test_team_approval.py::test_both_flows_share_the_same_status_mapper``
        断言两者是同一个函数对象。 breakage 会在那条测试上立刻暴露，
        而不是静默地表现为「审批偶尔记错终态」。
        """
        if decision in ("cancel", "cancelled", "abort"):
            return "cancelled"
        if decision in ("approve", "approved", "continue", "accept"):
            return "approved"
        return "rejected"

    @staticmethod
    def _normalise_options(options: list[dict] | list[str] | None) -> list[dict[str, str]]:
        """把选项统一成 ``[{"value","label"}]``，顺带挡住空 value。

        接受 ``["approve"]`` 和 ``[{"value": "approve", "label": "同意"}]``
        两种写法，因为调用方一半是后端服务、一半是 DSL 画布。
        """
        out: list[dict[str, str]] = []
        for opt in options or []:
            if isinstance(opt, str):
                value, label = opt.strip(), opt.strip()
            elif isinstance(opt, dict):
                value = str(opt.get("value") or "").strip()
                label = str(opt.get("label") or value).strip()
            else:
                raise ValidationFailed(
                    f"option must be a string or an object, got {type(opt).__name__}"
                )
            if not value:
                raise ValidationFailed("option value must be a non-empty string")
            out.append({"value": value, "label": label})
        return out

    def _row(self, interrupt_id: str) -> HitlInterrupt:
        row = self.session.execute(
            select(HitlInterrupt).where(HitlInterrupt.id == interrupt_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFound(f"interrupt_not_found: {interrupt_id}")
        return row

    def _current(self, execution_id: str) -> HitlInterrupt | None:
        return self.session.execute(
            select(HitlInterrupt)
            .where(
                HitlInterrupt.execution_id == execution_id,
                HitlInterrupt.status == "pending",
            )
            .order_by(HitlInterrupt.created_at.desc())
        ).scalars().first()

    def _is_visible(self, actor: Actor, row: HitlInterrupt) -> bool:
        """owner 只看自己的中断；service 身份看自己创建的那批。"""
        caller = actor.owner_id or actor.service_id
        return not caller or row.owner_id == caller

    def _require_visible(self, actor: Actor, row: HitlInterrupt) -> None:
        if not self._is_visible(actor, row):
            # 不泄露「存在但不属于你」——按不存在处理。
            raise NotFound(f"interrupt_not_found: {row.id}")

    def _mark_expired(self, actor: Actor, row: HitlInterrupt) -> None:
        self.session.execute(
            update(HitlInterrupt)
            .where(
                HitlInterrupt.id == row.id,
                HitlInterrupt.status == "pending",
            )
            .values(
                status="expired",
                decision=HITL_TIMEOUT_DECISION,
                decided_at=utcnow(),
                updated_at=utcnow(),
                version=HitlInterrupt.version + 1,
            )
        )
        self.session.flush()
        self._audit(actor, "hitl.expired", row.id, {"execution_id": row.execution_id})

    def _sweep_expired(self, actor: Actor, row: HitlInterrupt) -> bool:
        """若已超时则落盘为 expired，返回是否发生了这次过期。"""
        if row.expires_at is None or row.expires_at > utcnow():
            return False
        self._mark_expired(actor, row)
        return True

    def _audit(
        self, actor: Actor, action: str, target: str, details: dict | None
    ) -> None:
        if self.audit is not None:
            self.audit.append(actor, action, target, details or {})

    @staticmethod
    def _view(row: HitlInterrupt) -> dict[str, Any]:
        return {
            "id": row.id,
            "execution_id": row.execution_id,
            "checkpoint": row.checkpoint,
            "context": row.context or {},
            "options": list(row.options or []),
            "status": row.status,
            "pending": row.status == "pending",
            "decided": row.status in HITL_DECIDED_STATUSES,
            "decision": row.decision,
            "resolution": row.resolution,
            "decided_by": row.decided_by,
            "reason": row.reason,
            "expires_at": _iso(row.expires_at),
            "decided_at": _iso(row.decided_at),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "version": row.version,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
