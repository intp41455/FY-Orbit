"""Outbox: exactly-once claim and receipt reconciliation (§4, §6.2).

External side-effects (task.merge / task.release) are not executed at approval
time. Approval only enqueues an :class:`Operation`. A worker claims it once via
a conditional UPDATE (``pending -> claimed``), performs the external call, then
records a receipt. Lost responses move the row to ``unknown`` for reconciliation
rather than blind retry.
"""

from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db.models import Operation, Proposal
from ..db.types import utcnow
from .errors import Conflict, NotFound
from .actor import Actor
from .audit import AuditService


class OutboxService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def claim_next(self, actor: Actor) -> Operation | None:
        """Atomically claim the oldest pending operation. Returns None if empty.

        The conditional UPDATE guarantees a single consumer wins even under
        concurrency (S09/T04).
        """
        row = self.s.execute(
            update(Operation)
            .where(Operation.state == "pending")
            .values(state="claimed", attempt=Operation.attempt + 1, updated_at=utcnow())
            .returning(Operation)
        ).scalar_one_or_none()
        if row is not None:
            self.audit.append(actor, "outbox.claimed", row.id, {"proposal_id": row.proposal_id})
        return row

    def succeed(self, actor: Actor, operation_id: str, external_id: str | None = None) -> Operation:
        op = self.s.get(Operation, operation_id)
        if op is None:
            raise NotFound("operation_not_found", "Operation not found")
        op.state = "succeeded"
        op.external_id = external_id
        op.external_state = "executed"
        op.updated_at = utcnow()
        # Advance the proposal to executed.
        self.s.execute(
            update(Proposal).where(Proposal.id == op.proposal_id)
            .values(status="executed", decided_at=utcnow())
        )
        self.audit.append(actor, "outbox.succeeded", operation_id, {"proposal_id": op.proposal_id})
        self.s.flush()
        return op

    def fail(self, actor: Actor, operation_id: str, error: str, *, unknown: bool = False) -> Operation:
        op = self.s.get(Operation, operation_id)
        if op is None:
            raise NotFound("operation_not_found", "Operation not found")
        op.state = "unknown" if unknown else "failed"
        op.last_error = error[:1000]
        op.updated_at = utcnow()
        new_status = "unknown" if unknown else "failed"
        self.s.execute(
            update(Proposal).where(Proposal.id == op.proposal_id).values(status=new_status)
        )
        self.audit.append(actor, f"outbox.{op.state}", operation_id, {"error": error[:200]})
        self.s.flush()
        return op
