"""P5 · 自主任务认领（A-Agent运行时-11）：共享任务板 → 查板 → 认领。

需求现场
--------

A-Agent运行时-11「自主任务认领」要求：**空闲 agent 主动认领待办任务**（共享任务
板 → 查板 → 认领），↔ A-Claw参与 全自动档。此前没有「谁在做这件事」的原子裁决：
多个 worker 同时看到一条待办会**重复执行**（重复计费 / 重复写文件）。

设计约束（违反即返工）
----------------------

1. **认领必须原子**——沿用 ``services/outbox.py:27`` 的
   ``UPDATE ... WHERE state='pending' RETURNING`` 条件更新语义：只有把行从
   ``pending`` 翻成 ``claimed`` 的那一个调用者拿到所有权，其余拿到 ``None``。
   这是跨进程也成立的真原子（DB 层裁决），不是进程内锁——进程内 ``threading.Lock``
   在「两个 agent 各自一个进程」的真实拓扑下毫无保护力。
2. **认领要能过期回收**——持有者崩溃后 ``claimed`` 行不能永久粘连。认领带
   ``lease_expires_at`` 租约；:meth:`TaskBoard.reclaim_expired` 把过期租约放回
   ``pending``（等价于 ``scheduler.reclaim_stale`` 的任务板版本）。
3. **认领必须留痕**——每次认领/回收走 ``AuditService`` 哈希链挂帧，可反查
   「这条任务是谁、什么时候拿走的」。
4. **不许假装成功**——认领失败返回 ``None`` 且**不抛异常**（空板是正常态，
   不是错误）；调用方据此决定是否空转等待。

与 ``UnifiedScheduler`` 的关系
------------------------------

调度中心（``services/scheduler/core.py``）解决的是「**有了任务派给谁跑**」；
本模块解决的是「**任务在板上由谁认领**」——认领先于派发。二者组合：
agent 空闲 → ``claim_next`` 拿到任务 → 构造 ``DispatchRequest`` 交调度中心执行。
本模块**不重复实现**调度/优先级/并发，只做任务板的所有权裁决。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db.claim_models import TaskClaim
from ..db.types import utcnow
from ..services.actor import Actor
from ..services.audit import AuditService

#: 默认租约时长（秒）。持有者须在此之前 ``renew``，否则被回收放回板上。
DEFAULT_LEASE_SECONDS = 300

#: 任务板行状态机：pending → claimed → done|failed；claimed 租约过期 → pending。
CLAIM_PENDING = "pending"
CLAIM_CLAIMED = "claimed"
CLAIM_DONE = "done"
CLAIM_FAILED = "failed"

CLAIM_STATES = (CLAIM_PENDING, CLAIM_CLAIMED, CLAIM_DONE, CLAIM_FAILED)


@dataclass(frozen=True)
class ClaimResult:
    """一次认领的结果快照（认领成功后的所有权凭证）。"""

    id: str
    title: str
    payload: dict
    claimed_by: str
    lease_expires_at: datetime

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "payload": dict(self.payload),
            "claimed_by": self.claimed_by,
            "lease_expires_at": self.lease_expires_at.isoformat(),
        }


class TaskBoard:
    """共享任务板：发布 / 原子认领 / 心跳续租 / 完成 / 过期回收。

    所有写操作沿用「条件 UPDATE + RETURNING」的原子裁决——
    ``WHERE state='pending'`` 保证只有一个并发调用者能翻转状态拿到所有权。
    """

    def __init__(self, session: Session, audit: AuditService):
        self._session = session
        self._audit = audit

    # -- 发布 ---------------------------------------------------------------- #

    def publish(
        self,
        actor: Actor,
        *,
        title: str,
        payload: dict | None = None,
        priority: int = 5,
    ) -> TaskClaim:
        """往任务板放一条待认领任务（初始态 ``pending``）。"""
        if not title or not title.strip():
            raise ValueError("task title must be non-empty")
        row = TaskClaim(
            id=uuid.uuid4().hex,
            owner_id=getattr(actor, "owner_id", "") or "",
            title=title.strip()[:200],
            payload=dict(payload or {}),
            priority=int(priority),
            state=CLAIM_PENDING,
            claimed_by="",
        )
        self._session.add(row)
        self._session.flush()
        self._audit.append(
            actor, "claim.published", row.id,
            {"title": row.title, "priority": row.priority},
        )
        return row

    # -- 查板 ---------------------------------------------------------------- #

    def list_board(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[TaskClaim]:
        """查板：默认返回全部（新→旧）；``state`` 过滤可选。"""
        from sqlalchemy import select

        stmt = select(TaskClaim)
        if state is not None:
            if state not in CLAIM_STATES:
                raise ValueError(f"unknown claim state: {state!r}")
            stmt = stmt.where(TaskClaim.state == state)
        stmt = stmt.order_by(TaskClaim.created_at.asc(), TaskClaim.id.asc()).limit(limit)
        return list(self._session.execute(stmt).scalars().all())

    def pending_count(self) -> int:
        return len(self.list_board(state=CLAIM_PENDING, limit=10_000))

    # -- 原子认领 ------------------------------------------------------------ #

    def claim_next(
        self,
        actor: Actor,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        capability: str = "",
    ) -> ClaimResult | None:
        """原子认领一条待办：优先级高者先，同级按发布时间 FIFO。

        实现要点：**先**用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 语义选出候选行
        （SQLite 不支持 ``SKIP LOCKED``，用「条件 UPDATE + RETURNING」兜底），
        **再**条件更新。返回 ``None`` 表示板上已无可认领任务（正常态，非错误）。
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        from sqlalchemy import select

        # 候选：pending，按 priority(升) + created_at(升) 排序；capability 过滤可选。
        stmt = select(TaskClaim).where(TaskClaim.state == CLAIM_PENDING)
        if capability:
            stmt = stmt.where(TaskClaim.capability == capability)
        stmt = stmt.order_by(TaskClaim.priority.asc(), TaskClaim.created_at.asc())
        candidate = self._session.execute(stmt.limit(1)).scalars().first()
        if candidate is None:
            return None

        now = utcnow()
        holder = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "") or "unknown"
        expires = now + timedelta(seconds=lease_seconds)

        # 条件更新：只有仍处于 pending 的那一次翻转成功（并发下只有一个赢家）。
        result = self._session.execute(
            update(TaskClaim)
            .where(TaskClaim.id == candidate.id, TaskClaim.state == CLAIM_PENDING)
            .values(
                state=CLAIM_CLAIMED,
                claimed_by=holder,
                claimed_at=now,
                lease_expires_at=expires,
                attempt=TaskClaim.attempt + 1,
            )
            .returning(TaskClaim)
        )
        row = result.scalars().first()
        if row is None:
            # 竞争失败：别的 agent 抢先一步。不抛异常——调用方重新查板即可。
            return None

        self._audit.append(
            actor, "claim.claimed", row.id,
            {"title": row.title, "claimed_by": holder, "attempt": row.attempt,
             "lease_seconds": lease_seconds},
        )
        self._session.flush()
        return ClaimResult(
            id=row.id,
            title=row.title,
            payload=dict(row.payload or {}),
            claimed_by=holder,
            lease_expires_at=expires,
        )

    # -- 心跳续租 ------------------------------------------------------------ #

    def renew(
        self,
        actor: Actor,
        claim_id: str,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """持有者续租：把 ``lease_expires_at`` 往后推。

        只允许**当前持有者**续租（``WHERE claimed_by=holder``），且任务仍在
        ``claimed`` 态——否则返回 ``False``（已被回收或已完成，不再续）。
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        holder = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "") or "unknown"
        expires = utcnow() + timedelta(seconds=lease_seconds)
        result = self._session.execute(
            update(TaskClaim)
            .where(
                TaskClaim.id == claim_id,
                TaskClaim.state == CLAIM_CLAIMED,
                TaskClaim.claimed_by == holder,
            )
            .values(lease_expires_at=expires)
            .returning(TaskClaim.id)
        )
        ok = result.scalars().first() is not None
        if ok:
            self._audit.append(
                actor, "claim.renewed", claim_id, {"lease_seconds": lease_seconds}
            )
            self._session.flush()
        return ok

    # -- 完成 / 失败 --------------------------------------------------------- #

    def complete(
        self, actor: Actor, claim_id: str, *, ok: bool = True, note: str = ""
    ) -> bool:
        """持有者结单：``claimed → done|failed``。非持有者/非 claimed 态返回 False。"""
        holder = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "") or "unknown"
        new_state = CLAIM_DONE if ok else CLAIM_FAILED
        result = self._session.execute(
            update(TaskClaim)
            .where(
                TaskClaim.id == claim_id,
                TaskClaim.state == CLAIM_CLAIMED,
                TaskClaim.claimed_by == holder,
            )
            .values(state=new_state, finished_at=utcnow(), last_note=note[:2000])
            .returning(TaskClaim.id)
        )
        done = result.scalars().first() is not None
        if done:
            self._audit.append(
                actor, "claim.completed", claim_id,
                {"ok": ok, "state": new_state, "note": note[:300]},
            )
            self._session.flush()
        return done

    # -- 过期回收 ------------------------------------------------------------ #

    def reclaim_expired(self, actor: Actor) -> list[str]:
        """把租约过期的 ``claimed`` 行放回 ``pending``（持有者崩溃的兜底）。

        返回被回收的 claim_id 列表。等价于 ``scheduler.reclaim_stale`` 的任务板
        版本——同样只做「标记放回」而非强杀持有者（诚实边界）。
        """
        from sqlalchemy import select

        now = utcnow()
        stmt = select(TaskClaim).where(
            TaskClaim.state == CLAIM_CLAIMED,
            TaskClaim.lease_expires_at.is_not(None),
            TaskClaim.lease_expires_at < now,
        )
        expired = list(self._session.execute(stmt).scalars().all())
        reclaimed: list[str] = []
        for row in expired:
            result = self._session.execute(
                update(TaskClaim)
                .where(TaskClaim.id == row.id, TaskClaim.state == CLAIM_CLAIMED)
                .values(state=CLAIM_PENDING, claimed_by="", claimed_at=None,
                        lease_expires_at=None)
                .returning(TaskClaim.id)
            )
            if result.scalars().first() is not None:
                reclaimed.append(row.id)
        if reclaimed:
            self._audit.append(
                actor, "claim.reclaimed_expired", ",".join(reclaimed[:20]),
                {"count": len(reclaimed)},
            )
            self._session.flush()
        return reclaimed
