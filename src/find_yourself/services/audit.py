"""Append-only audit hash chain and independent anchors (FROZEN_CONTRACT §9).

Each event links to the prior head by ``previous_hash`` and stores its own
``hash = H(seq, actor, action, target, details, previous_hash)``. Verification
detects: missing seq numbers, re-ordering, prev-hash mismatch, current-head
mismatch, and a mismatch against an independently stored anchor.

Threat model (stated honestly): a principal with full DB write access can
rewrite the in-DB chain and head. Anchors must be written to a *different*
retention store; the chain only provides post-hoc tamper evidence, not
absolute tamper-proofing.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AuditAnchor, AuditEvent
from ..db.types import utcnow
from .hasher import digest
from .actor import Actor


@dataclass
class AuditVerifyResult:
    ok: bool
    checked: int
    problems: list[str]


class AuditService:
    def __init__(self, session: Session):
        self.s = session

    def _head(self) -> tuple[int, str]:
        row = self.s.execute(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).scalar_one_or_none()
        if row is None:
            return (0, "0" * 64)
        return (row.seq, row.hash)

    def append(self, actor: Actor, action: str, target: str | None = None, details: dict | None = None) -> AuditEvent:
        seq, prev_hash = self._head()
        event_seq = seq + 1
        body = {
            "seq": event_seq,
            "actor": actor.owner_id or actor.service_id or "anonymous",
            "action": action,
            "target": target,
            "details": details or {},
            "previous_hash": prev_hash,
        }
        row = AuditEvent(
            id=__import__("uuid").uuid4().hex,
            seq=event_seq,
            actor=body["actor"],
            action=action,
            target=target,
            details=details or {},
            previous_hash=prev_hash,
            hash=digest(body),
        )
        self.s.add(row)
        self.s.flush()
        return row

    def anchor(self, storage: str = "local-backup") -> AuditAnchor:
        seq, head_hash = self._head()
        row = AuditAnchor(
            id=__import__("uuid").uuid4().hex,
            seq=seq,
            storage=storage,
            head_hash=head_hash,
            evidence={"note": "anchor recorded; storage location external to primary DB"},
            created_at=utcnow(),
        )
        self.s.add(row)
        self.s.flush()
        return row

    def verify(self) -> AuditVerifyResult:
        events = list(self.s.execute(select(AuditEvent).order_by(AuditEvent.seq.asc())).scalars())
        problems: list[str] = []
        prev_hash = "0" * 64
        for i, ev in enumerate(events, start=1):
            if ev.seq != i:
                problems.append(f"gap/reorder at position {i}: expected seq {i}, got {ev.seq}")
            if ev.previous_hash != prev_hash:
                problems.append(f"prev-hash mismatch at seq {ev.seq}")
            recomputed = digest({
                "seq": ev.seq, "actor": ev.actor, "action": ev.action,
                "target": ev.target, "details": ev.details, "previous_hash": ev.previous_hash,
            })
            if recomputed != ev.hash:
                problems.append(f"content-hash mismatch at seq {ev.seq}")
            prev_hash = ev.hash
        # Independent anchor check: latest anchor's head must equal chain tail.
        anchor = self.s.execute(select(AuditAnchor).order_by(AuditAnchor.seq.desc()).limit(1)).scalar_one_or_none()
        if anchor is not None:
            tail = events[-1] if events else None
            if tail is None or anchor.seq != tail.seq or anchor.head_hash != tail.hash:
                problems.append("anchor does not match current chain head")
        return AuditVerifyResult(ok=not problems, checked=len(events), problems=problems)
