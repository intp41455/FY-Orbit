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

from sqlalchemy import func, select, text
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
    warn_threshold_pct: Decimal = Decimal("80.0")


class BudgetService:
    def __init__(self, session: Session, audit: AuditService, limits: BudgetLimits | None = None):
        self.s = session
        self.audit = audit
        self.limits = limits or BudgetLimits()
        self.alert_handlers: list = []

    def register_alert_handler(self, handler) -> None:
        """Register a notification callback for budget threshold warnings and breaches."""
        self.alert_handlers.append(handler)

    @staticmethod
    def month_key(when: datetime) -> str:
        return when.strftime("%Y-%m")

    def _is_postgres(self) -> bool:
        return self.s.bind.dialect.name == "postgresql"

    @staticmethod
    def _root_id(task: Task) -> str:
        return task.root_task_id or task.id

    def _lock_budget(self, root_id: str) -> None:
        """Serialise reservation checks for one tree and the global month.

        Postgres: transaction-scoped advisory locks keyed by the tree root and a
        global month key, so concurrent children cannot interleave their
        sum-check + insert. SQLite runs tests serially and has no advisory locks;
        skip it there.
        """
        if not self._is_postgres():
            return
        self.s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"tree:{root_id}"})
        self.s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "budget:month"})

    def _tree_task_ids(self, root_id: str) -> list[str]:
        """Root + all descendants (root_task_id groups the tree)."""
        rows = self.s.execute(
            select(Task.id).where(
                (Task.root_task_id == root_id) | (Task.id == root_id)
            )
        ).scalars().all()
        return list(rows)

    def _reserved_sum(self, task_ids, period: str) -> Decimal:
        expr = func.coalesce(func.sum(BudgetReservation.amount), 0)
        q = select(expr).where(
            BudgetReservation.state.in_(["reserved", "unknown"]),
            BudgetReservation.period == period,
        )
        if task_ids is not None:
            q = q.where(BudgetReservation.task_id.in_(task_ids))
        return Decimal(str(self.s.execute(q).scalar() or 0))

    def _month_window(self, when: datetime) -> tuple[datetime, datetime]:
        start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end

    def _tree_budget_used(self, tree_ids: list[str]) -> Decimal:
        """Calculate total budget committed by a task tree:
        active reservations (reserved/unknown) + settled actual consumption."""
        if not tree_ids:
            return ZERO
        q_res = select(func.coalesce(func.sum(BudgetReservation.amount), 0)).where(
            BudgetReservation.task_id.in_(tree_ids),
            BudgetReservation.state.in_(["reserved", "unknown"]),
        )
        res_sum = Decimal(str(self.s.execute(q_res).scalar() or 0))

        q_settled = select(func.coalesce(func.sum(BudgetLedger.delta), 0)).where(
            BudgetLedger.task_id.in_(tree_ids),
            BudgetLedger.reason == "settle",
        )
        settled_sum = Decimal(str(self.s.execute(q_settled).scalar() or 0))

        return res_sum + settled_sum

    def _month_budget_used(self, when: datetime) -> Decimal:
        """Calculate total budget committed across all scopes in the rolling month:
        active reservations (reserved/unknown) + settled actual consumption."""
        start, end = self._month_window(when)
        month_str = self.month_key(when)

        # In-flight reservations in this month (covers canvas_dispatch, task, and monthly scopes)
        q_res = select(func.coalesce(func.sum(BudgetReservation.amount), 0)).where(
            BudgetReservation.state.in_(["reserved", "unknown"]),
            (
                ((BudgetReservation.created_at >= start) & (BudgetReservation.created_at < end))
                | (BudgetReservation.period == f"month:{month_str}")
            ),
        )
        res_sum = Decimal(str(self.s.execute(q_res).scalar() or 0))

        # Settled actual consumption in this month
        q_settled = select(func.coalesce(func.sum(BudgetLedger.delta), 0)).where(
            BudgetLedger.reason == "settle",
            BudgetLedger.created_at >= start,
            BudgetLedger.created_at < end,
        )
        settled_sum = Decimal(str(self.s.execute(q_settled).scalar() or 0))

        return res_sum + settled_sum

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

        # Serialise: cap check + insert must be atomic w.r.t. other concurrent
        # reservations in this tree and across the rolling month (T07).
        root = self._root_id(task)
        self._lock_budget(root)
        tree_ids = self._tree_task_ids(root)

        # Per-task cap is aggregated over the WHOLE tree (root + descendants),
        # counting both in-flight reservations and settled actual expenses.
        task_used = self._tree_budget_used(tree_ids)
        task_projected = task_used + amt
        task_pct = (task_projected / self.limits.per_task_usd) * Decimal("100")

        # Monthly cap is global across the rolling month, covering all scopes (task, canvas, monthly)
        # and counting both in-flight reservations and settled actual expenses.
        month_used = self._month_budget_used(now)
        month_projected = month_used + amt
        month_pct = (month_projected / self.limits.per_month_usd) * Decimal("100")

        # Threshold notification chain (O06): alert on >= warn_threshold_pct or hard breach
        if task_pct >= self.limits.warn_threshold_pct or month_pct >= self.limits.warn_threshold_pct:
            level = "warning" if (task_projected <= self.limits.per_task_usd and month_projected <= self.limits.per_month_usd) else "critical"
            alert_payload = {
                "level": level,
                "task_id": task_id,
                "root_id": root,
                "task_used_usd": str(task_used),
                "month_used_usd": str(month_used),
                "requested_usd": str(amt),
                "task_limit_usd": str(self.limits.per_task_usd),
                "month_limit_usd": str(self.limits.per_month_usd),
                "task_usage_pct": f"{task_pct:.1f}%",
                "month_usage_pct": f"{month_pct:.1f}%",
            }
            self.audit.append(actor, "budget.alert", task_id, alert_payload)
            for handler in self.alert_handlers:
                try:
                    handler(alert_payload)
                except Exception:
                    pass

        if task_projected > self.limits.per_task_usd:
            raise Conflict("task_budget_exceeded", f"Tree budget exceeded ({task_used}+{amt})")
        if month_projected > self.limits.per_month_usd:
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
        amt = Decimal(str(settled_amount))
        if amt > row.amount:
            raise Conflict(
                "settlement_exceeds_reservation",
                f"Settled amount (${amt}) exceeds reserved amount (${row.amount})"
            )
        row.state = "settled"
        row.settled_at = utcnow()
        seq = self.s.execute(select(func.coalesce(func.max(BudgetLedger.seq), 0))).scalar() or 0
        self.s.add(BudgetLedger(
            id=uuid4().hex, reservation_id=row.id, task_id=row.task_id,
            delta=amt, reason="settle", seq=seq + 1,
        ))
        self.audit.append(actor, "budget.settled", row.task_id, {"amount": str(amt), "reserved": str(row.amount)})
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

    def hold_unknown_pricing(
        self, actor: Actor, reservation_id: str, *, reason: str = "pricing_unknown"
    ) -> BudgetReservation:
        """Hold a reservation in 'unknown' state when provider pricing is unavailable.

        CRITICAL: Unlike settle(), this does NOT free the budget allocation.
        The reservation continues to count in _reserved_sum() against both task
        and monthly limits, preventing unbudgeted consumption until provider reconciliation.
        """
        row = self.s.get(BudgetReservation, reservation_id)
        if row is None or row.state not in ("reserved", "unknown"):
            raise NotFound("reservation_not_found", "Reservation not found or not active")
        row.state = "unknown"
        self.audit.append(actor, "budget.pricing_unknown", row.task_id, {
            "reservation_id": reservation_id,
            "held_amount": str(row.amount),
            "reason": reason,
        })
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
