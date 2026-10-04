"""Append-only audit hash chain with anchors in an independent store (FROZEN_CONTRACT §9).

Each event links to the prior head by ``previous_hash`` and stores its own
``hash = H(seq, actor, action, target, details, previous_hash)``. Verification
detects: missing seq numbers, re-ordering, prev-hash mismatch, current-head
mismatch, and a mismatch against an independently stored anchor.

Threat model (stated honestly)
-----------------------------
The hash chain provides **post-hoc tamper evidence only**. It is not, and does
not claim to be, tamper-proofing:

- A principal with full write access to the primary DB can rewrite the in-DB
  chain and its head. Detecting that requires an anchor the attacker cannot
  rewrite with the same grant.
- Anchors are therefore written to an **independent retention store** outside the
  primary database (see :mod:`find_yourself.services.anchor_store`), never to the
  in-DB ``audit_anchors`` table. Rewriting both the chain and its anchor now
  requires **two separate write permissions** — primary DB *and* anchor store —
  rather than one. That raises the bar; it does not eliminate the attacker.
- When the anchor store is a ``FileAnchorStore`` co-located with the database
  (the default, ``~/.find-yourself/anchors/``), a principal with host-level write
  access — ``root``, or the DB service account — can still rewrite both.
  **This is explicitly not absolute tamper-proofing.** Deployments that need a
  real second trust boundary must use WORM/object-lock storage, a separate
  retention account, or an independently-operated instance (``S3AnchorStore``).
- ``verify()`` reports ``anchored=False`` when no anchor is available. That is
  "no anchor-based evidence", **not** a mismatch and **not** a pass: callers must
  not read ``ok=True`` with ``anchored=False`` as "chain is tamper-proof".
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AuditEvent
from ..db.types import utcnow
from .anchor_store import AnchorRecord, AnchorStore, FileAnchorStore
from .hasher import digest
from .actor import Actor


@dataclass
class AuditVerifyResult:
    ok: bool
    checked: int
    problems: list[str]
    #: True when an anchor was available in the independent store and was
    #: actually compared against the chain tail. False means no anchor-based
    #: evidence existed (e.g. ``NullAnchorStore``, or no anchor written yet) —
    #: that is not a mismatch, but it is also not a clean bill of health.
    anchored: bool = False


class AuditService:
    def __init__(self, session: Session, anchor_store: AnchorStore | None = None):
        self.s = session
        # Default keeps the constructor backward compatible (anchor() has no
        # callers yet) while ensuring anchors never land in the primary DB.
        self._anchor_store: AnchorStore = anchor_store if anchor_store is not None else FileAnchorStore()

    @property
    def anchor_store(self) -> AnchorStore:
        return self._anchor_store

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

    def anchor(self, storage: str | None = None) -> AnchorRecord:
        """Anchor the current chain head into the independent retention store.

        This writes **only** to ``self._anchor_store`` — never to the primary
        ``audit_anchors`` table (retired by migration 0010; see R39). The
        ``storage`` argument is descriptive provenance only; the actual write
        location is whatever the store writes to, and is what gets recorded.
        """
        seq, head_hash = self._head()
        written_at = utcnow()
        location = storage or getattr(self._anchor_store, "location", lambda: type(self._anchor_store).__name__)()
        record = AnchorRecord(
            seq=seq,
            head_hash=head_hash,
            storage=location,
            evidence={
                "note": "anchor written to external store; primary-DB write path no longer used",
                "storage": location,
                "store_class": type(self._anchor_store).__name__,
                "written_at": written_at.isoformat(),
                "anchor_content_hash": digest({
                    "seq": seq, "head_hash": head_hash, "storage": location,
                    "written_at": written_at.isoformat(),
                }),
                "contains_private_text": False,
            },
            created_at=written_at,
        )
        self._anchor_store.put(record)
        return record

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
        # Anchor check against the independent store. The anchor is NOT read from
        # the primary DB: reading it from the DB would let a single write grant
        # rewrite both sides and make this check vacuous.
        anchored = False
        anchor = self._anchor_store.latest()
        if anchor is not None:
            anchored = True
            tail = events[-1] if events else None
            if tail is None or anchor.seq != tail.seq or anchor.head_hash != tail.hash:
                problems.append("anchor does not match current chain head")
        # No anchor available => no anchor-based evidence. Deliberately NOT a
        # problem: `anchored` carries that distinction so callers can tell
        # "unanchored" apart from "anchored and consistent".
        return AuditVerifyResult(
            ok=not problems, checked=len(events), problems=problems, anchored=anchored
        )
