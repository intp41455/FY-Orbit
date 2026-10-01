"""Database models for 05 Multi-Agent Collaboration Canvas (多Agent协作可视化画布).

Defines:
- CanvasInstance: Active collaboration workspace session per project & domain.
- DispatchRecord: Explicit structured subtask dispatches from orchestrator to workers.
- HandoffPacket: Structured handoff packets between agents (no raw conversation dumping).
- CanvasEvent: Monotonic event log for canvas snapshot & cursor replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from find_yourself.db.base import Base
from find_yourself.db.types import ID, MONEY, TZDateTime, utcnow

CANVAS_DOMAINS = ("personal", "work")
CANVAS_STATES = ("active", "paused", "completed", "cancelled")
DISPATCH_STATES = (
    "planned", "pending_adapter", "dispatched", "accepted", "running", "waiting_rework", "completed", "failed", "cancelled", "unknown_needs_reconciliation"
)



class CanvasInstance(Base):
    __tablename__ = "canvas_instances"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    project_name: Mapped[str] = mapped_column(String(100))
    domain: Mapped[str] = mapped_column(String(16), default="personal")
    template_id: Mapped[str] = mapped_column(String(32), default="personal")
    orchestrator_id: Mapped[str] = mapped_column(String(64), default="Hermes")
    state: Mapped[str] = mapped_column(String(32), default="active")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(f"domain IN {CANVAS_DOMAINS}", name="ck_canvas_domain"),
        CheckConstraint(f"state IN {CANVAS_STATES}", name="ck_canvas_state"),
    )


class DispatchRecord(Base):
    __tablename__ = "dispatch_records"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("canvas_instances.id", ondelete="CASCADE"), index=True
    )
    root_task_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subtask_id: Mapped[str] = mapped_column(String(64), index=True)
    orchestrator_id: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    input_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    goal: Mapped[str] = mapped_column(String(300))
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    budget_slice: Mapped[float] = mapped_column(MONEY, default=0)
    deadline: Mapped[datetime] = mapped_column(TZDateTime)
    state: Mapped[str] = mapped_column(String(32), default="planned")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(f"state IN {DISPATCH_STATES}", name="ck_dispatch_state"),
    )


class HandoffPacket(Base):
    __tablename__ = "handoff_packets"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("canvas_instances.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(64))
    goal: Mapped[str] = mapped_column(String(300))
    completed_items: Mapped[list[str]] = mapped_column(JSON, default=list)
    artifact_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    unresolved_issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    next_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_task_id: Mapped[str] = mapped_column(String(64))
    source_worker_id: Mapped[str] = mapped_column(String(64))
    target_worker_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class CanvasEvent(Base):
    __tablename__ = "canvas_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("canvas_instances.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_canvas_evt_instance_seq", "instance_id", "seq", unique=True),
    )
