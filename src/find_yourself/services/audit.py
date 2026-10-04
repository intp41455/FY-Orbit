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


#: Key under which an optional ``message_id`` travels inside ``details``.
#:
#: It is deliberately NOT a new column: ``audit_events`` is hash-chained and
#: adding a top-level attribute would require a migration *and* would change the
#: digest of every pre-existing row. Storing it inside ``details`` — which is
#: already part of the hashed body — means:
#:
#: * events written **before** this feature carry no such key, so their
#:   ``digest`` is bit-for-bit identical to the old implementation
#:   (backward compatibility is provable, not aspirational);
#: * events written **with** a ``message_id`` have it covered by the content
#:   hash, so tampering with the attribution is detected by :meth:`verify`;
#: * the "提问 → trace 帧" index needs no schema change or migration.
#:
#: The key is only injected when a ``message_id`` is actually supplied, so a
#: ``None`` never leaks a spurious ``"message_id": null`` into the hash.
MESSAGE_ID_KEY = "message_id"


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

    def append(
        self,
        actor: Actor,
        action: str,
        target: str | None = None,
        details: dict | None = None,
        *,
        message_id: str | None = None,
    ) -> AuditEvent:
        seq, prev_hash = self._head()
        event_seq = seq + 1
        # Copy so the caller's dict is never mutated, then fold the optional
        # message_id into it. When message_id is None the key is NOT written,
        # which is what keeps legacy digests reproducible (see MESSAGE_ID_KEY).
        detail_body = dict(details) if details else {}
        if message_id is not None:
            existing = detail_body.get(MESSAGE_ID_KEY)
            if existing is not None and existing != message_id:
                raise ValueError(
                    f"details[{MESSAGE_ID_KEY!r}]={existing!r} conflicts with message_id={message_id!r}"
                )
            detail_body[MESSAGE_ID_KEY] = message_id
        body = {
            "seq": event_seq,
            "actor": actor.owner_id or actor.service_id or "anonymous",
            "action": action,
            "target": target,
            "details": detail_body,
            "previous_hash": prev_hash,
        }
        row = AuditEvent(
            id=__import__("uuid").uuid4().hex,
            seq=event_seq,
            actor=body["actor"],
            action=action,
            target=target,
            details=detail_body,
            previous_hash=prev_hash,
            hash=digest(body),
        )
        self.s.add(row)
        self.s.flush()
        return row

    # -- 提问 ↔ trace 双向索引 ------------------------------------------------
    #
    # The index is derived from ``details[MESSAGE_ID_KEY]`` on the hash-chained
    # events. Two directions are supported:
    #
    #   * ``frames_for_message(actor, message_id)`` — every frame attributable
    #     to one question, ordered by ``seq`` (the canonical chain order).
    #   * ``message_for_frame(actor, seq)``         — which question a frame
    #     belongs to (``None`` for un-attributed / legacy frames).
    #
    # Owner isolation is a *query predicate*, never a post-filter the caller
    # can forget: only events whose recorded ``actor`` equals the caller's
    # identity are ever selected. A caller therefore cannot enumerate another
    # owner's frames by guessing a message_id, and gets the same empty/None
    # answer for "not mine" as for "does not exist" — no existence leak.

    @staticmethod
    def _identity(actor: Actor) -> str | None:
        """The ``audit_events.actor`` value this caller is allowed to read."""
        if actor.subject_type == "owner":
            return actor.owner_id or None
        if actor.subject_type == "service":
            return actor.service_id or None
        return None

    def frames_for_message(self, actor: Actor, message_id: str) -> list[AuditEvent]:
        """Return the frames produced by ``message_id``, ordered by ``seq``.

        Only events visible to ``actor`` are considered. An un-attributed or
        unknown ``message_id`` yields ``[]`` (never an error).
        """
        if not message_id:
            return []
        identity = self._identity(actor)
        if identity is None:
            return []
        # The message attribution is stored inside the JSON ``details`` column
        # and filtered in the query itself.
        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.actor == identity,
                AuditEvent.details[MESSAGE_ID_KEY].as_string() == message_id,
            )
            .order_by(AuditEvent.seq.asc())
        )
        return list(self.s.execute(stmt).scalars())

    def message_for_frame(self, actor: Actor, seq: int) -> str | None:
        """Return the ``message_id`` the frame at ``seq`` belongs to.

        ``None`` when the frame is unknown, not visible to ``actor``, or an
        un-attributed (legacy) frame that carries no ``message_id``.
        """
        identity = self._identity(actor)
        if identity is None:
            return None
        ev = self.s.execute(
            select(AuditEvent).where(AuditEvent.seq == seq, AuditEvent.actor == identity)
        ).scalar_one_or_none()
        if ev is None:
            return None
        return message_id_of(ev)

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


def message_id_of(event: AuditEvent) -> str | None:
    """Read the ``message_id`` attribution off an audit frame, or ``None``.

    Pure accessor — it performs no authorization. Callers that obtained the
    event through :meth:`AuditService.message_for_frame` have already passed
    the owner-isolation predicate.
    """
    value = (event.details or {}).get(MESSAGE_ID_KEY)
    return value if isinstance(value, str) and value else None
