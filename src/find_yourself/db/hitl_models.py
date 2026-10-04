"""Human-in-the-loop interrupt persistence (需求 12 HITL).

One table, ``hitl_interrupts``, records "an execution stopped at a named
checkpoint and is now waiting for a human decision":

- ``execution_id`` — *which* run is suspended (task id, agent-team run id,
  orchestrator lease id...). Deliberately **not** a foreign key: HITL hangs off
  several execution surfaces that do not share a parent table, and a nullable
  FK to ``tasks`` would silently orphan the agent/lease cases.
- ``checkpoint`` — *where* in that execution the pause happened, so the
  resuming code knows which step to re-enter.
- ``context`` — the JSON the executor hands to the human (what it was about to
  do, why it needs a person).
- ``options`` — the legal decisions, normalised to ``[{"value","label"}]``.
  Enforced in the service, not the schema: a CHECK constraint cannot read a
  JSON array portably across SQLite and Postgres.
- ``status`` — ``pending`` while nobody has answered, then exactly one of
  ``approved`` / ``rejected`` / ``cancelled`` / ``expired``.

The waiting/decided split is a **schema** invariant, not just a service
convention: ``ck_hitl_decided_shape`` rejects any row where a pending interrupt
carries a decision, or a decided interrupt does not. A half-written decision
cannot reach disk even if a future caller forgets a branch.

This module owns its table and registers it on the shared ``Base`` metadata;
it does not modify ``db/models.py``.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow

#: ``pending`` = waiting for a human. Everything else = a decision is recorded.
HITL_STATUSES = (
    "pending", "approved", "rejected", "cancelled", "expired",
)
#: Statuses that mean "a human (or the timeout sweep) already answered".
HITL_DECIDED_STATUSES = ("approved", "rejected", "cancelled", "expired")
#: Synthetic decision recorded when the deadline passes unanswered.
HITL_TIMEOUT_DECISION = "timeout"


class HitlInterrupt(Base):
    __tablename__ = "hitl_interrupts"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    #: Which execution is suspended. Free-form id, see the module docstring.
    execution_id: Mapped[str] = mapped_column(String(200), index=True)
    #: Which step of that execution asked for a human.
    checkpoint: Mapped[str] = mapped_column(String(200))
    #: Shown to the human: what the executor intended to do and why it stopped.
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Normalised to ``[{"value": str, "label": str}]``; never empty.
    options: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: The chosen option value. NULL exactly while ``status == 'pending'``.
    decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Extra data the human supplied when deciding (edited text, new params...).
    resolution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    #: Owner who decided (None while pending).
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Deadline for an answer. NULL means "wait indefinitely".
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','expired')",
            name="ck_hitl_status",
        ),
        # A pending row carries no decision; a decided row must carry one.
        # IS NULL / IS NOT NULL never yield NULL, so this has no NULL hole.
        CheckConstraint(
            "((status = 'pending' AND decision IS NULL) OR "
            "(status <> 'pending' AND decision IS NOT NULL))",
            name="ck_hitl_decided_shape",
        ),
        CheckConstraint(
            "(status <> 'pending' AND decided_at IS NOT NULL) OR "
            "(status = 'pending' AND decided_at IS NULL)",
            name="ck_hitl_decided_at_shape",
        ),
        CheckConstraint("version >= 1", name="ck_hitl_version_positive"),
        Index("ix_hitl_execution_status", "execution_id", "status"),
        Index("ix_hitl_owner_status", "owner_id", "status"),
    )
