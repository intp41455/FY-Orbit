"""Degraded-mode task reaping (C0 / R-13).

Problem this solves
-------------------
``api/routes/tasks.py`` commits a task row with ``status="queued"`` **before**
checking whether Temporal is wired. When Temporal is not configured (the default
in ``.env`` for this deployment) the whole start branch is skipped, yet the API
still answers 200 and the UI shows "进行中" forever. There is no deadline sweep,
no ``queued`` reaper, and the worker only consumes Temporal queues — so nothing
will ever move the row. That is R-13: HTTP 200, task never finishes.

Why not "return 503 and do not persist"
---------------------------------------
That was the original architectural proposal (T-1). Measured impact: 81 call
sites across ``tests/api`` and ``tests/integration`` assert ``200`` on task
creation, and ``GET /api/tasks/{id}`` plus the SSE stream are part of the frozen
contract. Refusing to create tasks would be a breaking API change traded for a
bug fix, and it would also remove the audit trail of what the user asked for.

What we do instead
------------------
1. The create response **stops lying**: ``workflow.status`` becomes
   ``temporal_unavailable`` and a top-level ``degraded`` list names the cause, so
   a client can tell "queued and running" from "queued and never will be".
2. This module provides a reaper that is **independent of Temporal** — it talks
   to the database only. If the reaper depended on Temporal it would be dead code
   in exactly the situation it exists for.
3. ``/health/ready`` reports ``degraded: ["temporal"]`` so an operator can tell
   "Temporal is down" from "Temporal was never configured".

The reaper is idempotent, only touches rows that are genuinely stuck, and records
an audit event for every row it reclaims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Task
from ..db.types import utcnow

#: A queued task older than this is considered stuck. Chosen well above the
#: normal start latency (single-digit seconds) and well below the default
#: one-hour task deadline, so a legitimately slow Temporal start is not reaped.
DEFAULT_STUCK_MINUTES = 5

#: Terminal states a reaped task may be moved to. ``failed`` is the only one the
#: reaper itself sets; the others exist so callers can pass a narrower view.
REAPABLE_STATUSES = ("queued",)

#: Reason string written to the audit trail. Stable, so dashboards can group on it.
REAP_REASON = "queued_task_never_started: temporal not enabled or worker never picked it up"


@dataclass
class ReapReport:
    """Outcome of one reaping pass. Pure data so it is easy to assert on."""

    scanned: int = 0
    reaped: list[str] = field(default_factory=list)
    skipped_not_stuck: int = 0

    @property
    def reaped_count(self) -> int:
        return len(self.reaped)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "reaped_count": self.reaped_count,
            "reaped_ids": list(self.reaped),
            "skipped_not_stuck": self.skipped_not_stuck,
        }


def find_stuck_queued_tasks(
    session: Session,
    *,
    stuck_minutes: int = DEFAULT_STUCK_MINUTES,
    owner_id: str | None = None,
    statuses: tuple[str, ...] = REAPABLE_STATUSES,
) -> list[Task]:
    """Return queued tasks older than ``stuck_minutes``, oldest first.

    Read-only. Exposed separately so ``cli doctor`` can report without mutating.
    """
    cutoff = utcnow() - timedelta(minutes=stuck_minutes)
    stmt = select(Task).where(Task.status.in_(statuses), Task.created_at < cutoff)
    if owner_id is not None:
        stmt = stmt.where(Task.owner_id == owner_id)
    return list(session.scalars(stmt.order_by(Task.created_at)))


def reap_stuck_queued_tasks(
    session: Session,
    *,
    stuck_minutes: int = DEFAULT_STUCK_MINUTES,
    owner_id: str | None = None,
    audit: object | None = None,
    actor: object | None = None,
) -> ReapReport:
    """Move stuck ``queued`` tasks to ``failed`` and record why.

    Deliberately does **not** consult Temporal: the whole point is to work in the
    degraded mode where Temporal is unavailable. Idempotent — a second pass over
    the same rows finds nothing because they are no longer ``queued``.

    ``audit`` is an ``AuditService``-like object exposing ``append(actor, action,
    target_id, payload)``. It is optional so the function stays usable from a
    bare session in tests.
    """
    report = ReapReport()
    stuck = find_stuck_queued_tasks(
        session, stuck_minutes=stuck_minutes, owner_id=owner_id
    )
    report.scanned = len(stuck)
    if not stuck:
        return report

    now = utcnow()
    for task in stuck:
        task.status = "failed"
        task.updated_at = now
        report.reaped.append(task.id)
        if audit is not None:
            try:
                audit.append(  # type: ignore[attr-defined]
                    actor,
                    "task.reaped",
                    task.id,
                    {
                        "reason": REAP_REASON,
                        "stuck_minutes": stuck_minutes,
                        "previous_status": "queued",
                        "age_seconds": int((now - task.created_at).total_seconds()),
                    },
                )
            except Exception:  # noqa: BLE001 - reaping must not abort on audit
                continue

    session.commit()
    return report


def degraded_markers(temporal_enabled: bool | None) -> list[str]:
    """Names of subsystems currently in a degraded state.

    An empty list means fully healthy. Kept as a helper so ``/health/ready`` and
    the task create response cannot drift apart on what counts as degraded.
    """
    markers: list[str] = []
    # ``None`` means we could not determine the state at all — that is *not*
    # the same as healthy, so it is reported as degraded too. Claiming "ok"
    # because we failed to look would hide exactly the failure this module
    # exists to surface.
    if temporal_enabled is not True:
        markers.append("temporal")
    return markers
