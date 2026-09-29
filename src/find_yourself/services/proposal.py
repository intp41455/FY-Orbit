"""Proposal digest, approval state machine and outbox hand-off (§6).

- Digest is recomputed server-side from the stored canonical fields and must
  match both the stored digest and the client-supplied digest (§6.1).
- Only an owner actor may decide; service identities are refused (S05).
- Concurrent approvals are serialised by a conditional UPDATE on
  ``status='pending'`` so exactly one wins (S09).
- Instant/in-transaction proposals go ``pending -> executed``; external
  side-effects (task.merge/task.release) go ``pending ->
  approved_pending_execution`` and enqueue a single-use outbox operation.
"""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db.models import Operation, Proposal
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from .hasher import digest
from .audit import AuditService

EXTERNAL_OPS = {"task.merge", "task.release"}
IMMEDIATE_OPS = {
    "memory.upsert", "memory.delete", "grant.add", "grant.revoke",
    "agent.register", "agent.drain", "skill.stage", "skill.promote",
    "skill.disable", "config.model", "conversation.delete",
}

_ALLOWED = {
    "pending": {"rejected", "expired", "approved_pending_execution", "executed"},
    "approved_pending_execution": {"executing"},
    "executing": {"executed", "failed", "unknown"},
    "unknown": {"executed", "failed"},
}


def new_id() -> str:
    return uuid4().hex


class ProposalService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    # -- digest -----------------------------------------------------------
    @staticmethod
    def canonical_fields(p: Proposal) -> dict:
        return {
            "operation": p.operation,
            "target_id": p.target_id,
            "expected_version": p.expected_version,
            "payload": p.payload,
            "reason": p.reason,
            "rollback": p.rollback,
            "expires_at": p.expires_at,
        }

    def compute_digest(self, p: Proposal) -> str:
        return digest(self.canonical_fields(p))

    # -- creation ---------------------------------------------------------
    def create(
        self,
        actor: Actor,
        *,
        operation: str,
        payload: dict,
        reason: str,
        rollback: str,
        target_id: str | None = None,
        expected_version: int = 0,
        expires_in_minutes: int = 30,
    ) -> Proposal:
        actor.require_authenticated()
        if operation not in set(IMMEDIATE_OPS) | EXTERNAL_OPS:
            raise ValidationFailed("bad_operation", f"Unknown operation {operation}")
        if not (1 <= expires_in_minutes <= 1440):
            raise ValidationFailed("bad_expiry", "expires_in_minutes out of range")
        expires_at = utcnow() + timedelta(minutes=expires_in_minutes)
        p = Proposal(
            id=new_id(), operation=operation, target_id=target_id,
            expected_version=expected_version, payload=payload, reason=reason,
            rollback=rollback, digest="", status="pending", expires_at=expires_at,
        )
        p.digest = self.compute_digest(p)
        self.s.add(p)
        self.s.flush()
        self.audit.append(actor, "proposal.created", p.id, {"operation": operation, "digest": p.digest})
        return p

    # -- decision ---------------------------------------------------------
    def decide(self, actor: Actor, proposal_id: str, client_digest: str, approve: bool) -> Proposal:
        # S05: only the owner may approve/reject proposals.
        actor.require_owner()

        p = self.s.get(Proposal, proposal_id)
        if p is None:
            raise NotFound("proposal_not_found", "Proposal not found")

        # Digest must match stored AND what the client saw (S06).
        recomputed = self.compute_digest(p)
        if p.digest != recomputed or client_digest != recomputed:
            raise Conflict("digest_mismatch", "Approval does not match exact proposal content")

        # Determine the legal target state BEFORE the claim, so the conditional
        # UPDATE only ever writes a CHECK-allowed status (no transient states).
        expired = p.expires_at <= utcnow()
        if not approve:
            target = "rejected"
        elif expired:
            target = "expired"
        elif p.operation in EXTERNAL_OPS:
            target = "approved_pending_execution"
        else:
            target = "executed"

        # Conditional claim: only one concurrent decision wins (S09).
        res = self.s.execute(
            update(Proposal).where(Proposal.id == proposal_id, Proposal.status == "pending")
            .values(status=target, decided_at=utcnow())
        )
        if res.rowcount == 0:
            raise Conflict("already_decided", "Proposal already decided or not pending")

        p.status = target
        p.decided_at = utcnow()

        if target == "rejected":
            self.audit.append(actor, "proposal.rejected", proposal_id, {"digest": p.digest})
            return p
        if target == "expired":
            self.audit.append(actor, "proposal.expired", proposal_id, {"digest": p.digest})
            raise Conflict("expired", "Approval expired")

        if target == "approved_pending_execution":
            op = Operation(
                id=new_id(), proposal_id=p.id,
                idempotency_key=f"fy:{p.id}:{p.digest[:12]}",
                op_type=p.operation, state="pending", target=p.target_id,
                payload_digest=p.digest,
            )
            self.s.add(op)
            p.execution_id = op.id
            self.audit.append(actor, "proposal.approved_pending_execution", p.id,
                              {"digest": p.digest, "operation_id": op.id})
        else:
            # Instant, in-transaction completion (§6.2).
            self.audit.append(actor, "proposal.executed", p.id, {"digest": p.digest})
        self.s.flush()
        return p
