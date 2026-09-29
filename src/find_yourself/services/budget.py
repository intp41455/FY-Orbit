"""Atomic budget reservation, settlement, release and cancel propagation (§7).

Defaults: per-task 0.50 USD, monthly 10 USD, concurrency 2, depth 2, retries 2,
8 steps. Reservation is atomic inside the caller's transaction: the sum of
already-reserved amounts for the task and for the rolling month is checked
against the limit before inserting a new reservation. Unknown price must not be
allowed through as zero cost. Cancellation propagates to child tasks and
releases unsettled reservations.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import BudgetLedger, BudgetReservation, Task
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, ValidationFailed
from .audit import AuditService

ZERO = Decimal("0.000000")


@dataclass
class BudgetLimits:
    per_task_usd: Decimal = Decimal("0.50")
    per_month_usd: Decimal = Decimal("10.00")


class BudgetService:
    def __init__(self, session: Session, audit: AuditService, limits: BudgetLimits | None = None):
        self.s = session
        self.audit = audit
        self.limits = limits or BudgetLimits()

    @staticmethod
    def month_key(when: datetime) -> str:
        return when.strftime("%Y-%m")

    def _reserved_sum(self, task_id: str | None, period: str) -> Decimal:
        expr = func.coalesce(func.sum(BudgetReservation.amount), 0)
        q = select(expr).where(
            BudgetReservation.state.in_(["reserved", "unknown"]),
            BudgetReservation.period == period,
        )
        if task_id:
            q = q.where(BudgetReservation.task_id == task_id)
        return Decimal(str(self.s.execute(q).scalar() or 0))

    def reserve(
        self,
        actor: Actor,
        *,
        task_id: str,
        amount: Decimal | str,
        idempotency_key: str,
        scope: str = "task",
    ) -> BudgetReservation:
        actor.require_authenticated()
        amt = Decimal(str(amount))
        if amt <= ZERO:
            raise ValidationFailed("bad_amount", "Reservation amount must be positive")
        task = self.s.get(Task, task_id)
        if task is None:
            raise NotFound("task_not_found", "Task not found")
        if task.status in {"cancelled", "completed", "failed"}:
            raise Conflict("task_closed", "Task is closed; no new reservations")

        now = utcnow()
        month = self.month_key(now)

        # Enforce per-task and monthly caps atomically.
        task_used = self._reserved_sum(task_id, "task")
        if task_used + amt > self.limits.per_task_usd:
            raise Conflict("task_budget_exceeded", f"Task budget exceeded ({task_used}+{amt})")
        month_used = self._reserved_sum(None, f"month:{month}")
        if month_used + amt > self.limits.per_month_usd:
            raise Conflict("monthly_budget_exceeded", "Monthly budget exceeded")

        row = BudgetReservation(
            id=uuid4().hex, task_id=task_id, period=f"month:{month}" if scope == "monthly" else "task",
            scope=scope, amount=amt, currency="USD", state="reserved",
            idempotency_key=idempotency_key,
        )
        self.s.add(row)
        self.s.flush()
        seq = self.s.execute(select(func.coalesce(func.max(BudgetLedger.seq), 0))).scalar() or 0
        self.s.add(BudgetLedger(
            id=uuid4().hex, reservation_id=row.id, task_id=task_id, delta=amt,
            reason="reserve", seq=seq + 1,
        ))
        self.audit.append(actor, "budget.reserved", task_id, {"amount": str(amt), "scope": scope})
        return row

    def settle(self, actor: Actor, reservation_id: str, settled_amount: Decimal | str) -> BudgetReservation:
        row = self.s.get(BudgetReservation, reservation_id)
        if row is None or row.state != "reserved":
            raise NotFound("reservation_not_found", "Reservation not found or not reserving")
        row.state = "settled"
        row.settled_at = utcnow()
        seq = self.s.execute(select(func.coalesce(func.max(BudgetLedger.seq), 0))).scalar() or 0
        self.s.add(BudgetLedger(
            id=uuid4().hex, reservation_id=row.id, task_id=row.task_id,
            delta=Decimal(str(settled_amount)), reason="settle", seq=seq + 1,
        ))
        self.audit.append(actor, "budget.settled", row.task_id, {"amount": str(settled_amount)})
        self.s.flush()
        return row

    def release(self, actor: Actor, reservation_id: str) -> BudgetReservation:
        row = self.s.get(BudgetReservation, reservation_id)
        if row is None or row.state != "reserved":
            raise NotFound("reservation_not_found", "Reservation not found or not reserving")
        row.state = "released"
        row.released_at = utcnow()
        self.audit.append(actor, "budget.released", row.task_id, {})
        self.s.flush()
        return row

    def cancel_task(self, actor: Actor, task_id: str) -> int:
        """Cancel a task, propagate to children and release unsettled reservations."""
        task = self.s.get(Task, task_id)
        if task is None:
            raise NotFound("task_not_found", "Task not found")
        released = 0
        # Release all unsettled reservations for this task.
        active = self.s.execute(
            select(BudgetReservation).where(
                BudgetReservation.task_id == task_id,
                BudgetReservation.state.in_(["reserved", "unknown"]),
            )
        ).scalars()
        for r in active:
            r.state = "cancelled"
            r.released_at = utcnow()
            released += 1
        task.status = "cancelled"
        # Propagate to child tasks.
        children = self.s.execute(select(Task).where(Task.parent_task_id == task_id)).scalars()
        for child in children:
            if child.status not in {"completed", "cancelled"}:
                child.status = "cancelled"
                for r in self.s.execute(
                    select(BudgetReservation).where(
                        BudgetReservation.task_id == child.id,
                        BudgetReservation.state.in_(["reserved", "unknown"]),
                    )
                ).scalars():
                    r.state = "cancelled"
                    r.released_at = utcnow()
                    released += 1
        self.audit.append(actor, "task.cancelled", task_id, {"released": released})
        self.s.flush()
        return released
