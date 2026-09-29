"""ORM models for the frozen contract (FROZEN_CONTRACT.md §3.1).

All tables use:
- explicit foreign keys (no silent orphaning),
- database CHECK constraints on every status field,
- optimistic ``version`` (default 1; updates use ``WHERE id=? AND version=?``),
- ``NUMERIC(12,6)`` money,
- timezone-aware UTC timestamps.

Postgres-only artifacts (pgvector extension, GIN FTS index, HNSW vector index,
generated tsvector column) live in the Alembic migration; on SQLite the
``embedding`` column degrades to JSON so unit tests still exercise the same
rows and constraints.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from .base import Base
from .types import HASH64, ID, MONEY, TZDateTime, utcnow

# ---------------------------------------------------------------------------
# Allowed status values (mirrored as DB CHECK constraints; single source here)
# ---------------------------------------------------------------------------
DOMAINS = ("personal", "work", "shared")
MSG_ROLES = ("user", "assistant", "system")
TASK_STATUSES = (
    "queued", "running", "waiting_input", "waiting_approval",
    "completed", "failed", "cancelled",
)
ATTEMPT_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
PROPOSAL_OPS = (
    "memory.upsert", "memory.delete", "grant.add", "grant.revoke",
    "agent.register", "agent.drain", "skill.stage", "skill.promote",
    "skill.disable", "config.model", "conversation.delete",
    "task.merge", "task.release",
)
PROPOSAL_STATUSES = (
    "pending", "approved_pending_execution", "executing", "executed",
    "failed", "unknown", "rejected", "expired",
)
GRANT_STATES = ("active", "revoked", "expired")
HYPOTHESIS_STATES = ("fact", "hypothesis", "theory", "unverified")
SERVICE_KINDS = ("worker", "agent", "tool_gateway", "executor", "release")
IDENTITY_STATES = ("active", "revoked")
AGENT_STATES = (
    "candidate", "registered", "healthy", "enabled", "draining", "offline", "revoked",
)
LEASE_STATES = ("active", "released", "expired", "revoked")
SKILL_STATES = ("staged", "active", "disabled", "deprecated")
RESERVATION_STATES = ("reserved", "settled", "released", "cancelled", "unknown")
OP_STATES = ("pending", "claimed", "succeeded", "failed", "unknown")
RELATION_TYPES = ("derives", "quotes", "cites", "supports")
MODES = ("listen", "explore", "research", "engineering", "creative")
STRATEGIES = ("auto", "single", "delegate", "workflow", "parallel")


def _in(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


# ---------------------------------------------------------------------------
# Identity & access
# ---------------------------------------------------------------------------
class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    csrf_secret: Mapped[str] = mapped_column(HASH64)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    rotation_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ServiceIdentity(Base):
    __tablename__ = "service_identities"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    semantic_version: Mapped[str] = mapped_column(String(80), default="1.0")
    task_binding: Mapped[str | None] = mapped_column(ID, nullable=True)
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    secret_hash: Mapped[str] = mapped_column(HASH64)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("kind", SERVICE_KINDS), name="kind"),
        CheckConstraint(_in("state", IDENTITY_STATES), name="state"),
    )


# ---------------------------------------------------------------------------
# Conversations & messages
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    domain: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16), default="listen")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("domain", DOMAINS), name="domain"),
        CheckConstraint(_in("mode", MODES), name="mode"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    client_message_id: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("role", MSG_ROLES), name="role"),
        UniqueConstraint("conversation_id", "client_message_id", name="conv_client_msg"),
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    root_task_id: Mapped[str | None] = mapped_column(ID, index=True, nullable=True)
    goal: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(16), default="personal")
    mode: Mapped[str] = mapped_column(String(16), default="listen")
    strategy: Mapped[str] = mapped_column(String(16), default="auto")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    stage: Mapped[str] = mapped_column(String(40), default="requirements")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    max_steps: Mapped[int] = mapped_column(Integer, default=8)
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deadline: Mapped[datetime] = mapped_column(TZDateTime)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("domain", DOMAINS), name="domain"),
        CheckConstraint(_in("mode", MODES), name="mode"),
        CheckConstraint(_in("strategy", STRATEGIES), name="strategy"),
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
        UniqueConstraint("owner_id", "idempotency_key", name="owner_idem"),
    )


class TaskAttempt(Base):
    __tablename__ = "task_attempts"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    checkpoint_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("status", ATTEMPT_STATUSES), name="status"),
        UniqueConstraint("task_id", "attempt_no", name="task_attempt_no"),
    )


# ---------------------------------------------------------------------------
# Proposals, grants, operations (outbox)
# ---------------------------------------------------------------------------
class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    operation: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    expected_version: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    rollback: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(HASH64)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    execution_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("operation", PROPOSAL_OPS), name="operation"),
        CheckConstraint(_in("status", PROPOSAL_STATUSES), name="status"),
        UniqueConstraint("digest", name="digest"),
    )


class Grant(Base):
    __tablename__ = "grants"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    source_domain: Mapped[str] = mapped_column(String(16))
    consumer_domain: Mapped[str] = mapped_column(String(16))
    record_ids: Mapped[list[str]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    state: Mapped[str] = mapped_column(String(16), default="active")
    scope_hash: Mapped[str] = mapped_column(HASH64)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("source_domain", DOMAINS), name="source_domain"),
        CheckConstraint(_in("consumer_domain", DOMAINS), name="consumer_domain"),
        CheckConstraint(_in("state", GRANT_STATES), name="state"),
        # No wildcard / empty grants: record_ids must be a non-empty list.
        CheckConstraint("json_array_length(record_ids) > 0", name="nonempty_records"),
    )


class Operation(Base):
    """Outbox row for external side effects; consumed exactly once."""

    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    op_type: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    target: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payload_digest: Mapped[str] = mapped_column(HASH64)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(_in("state", OP_STATES), name="state"),
        UniqueConstraint("proposal_id", name="one_op_per_proposal"),
        UniqueConstraint("idempotency_key", name="op_idem"),
    )


# ---------------------------------------------------------------------------
# Memory, revisions, source graph
# ---------------------------------------------------------------------------
class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    domain: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(HASH64)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    endorsed: Mapped[bool] = mapped_column(Boolean, default=False)
    hypothesis_status: Mapped[str] = mapped_column(String(16), default="unverified")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("domain", DOMAINS), name="domain"),
        CheckConstraint(_in("hypothesis_status", HYPOTHESIS_STATES), name="hypothesis_status"),
    )


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(HASH64)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class SourceRelation(Base):
    __tablename__ = "source_relations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    source_id: Mapped[str] = mapped_column(ID, index=True)
    source_kind: Mapped[str] = mapped_column(String(24))
    derived_id: Mapped[str] = mapped_column(ID, index=True)
    derived_kind: Mapped[str] = mapped_column(String(24))
    relation_type: Mapped[str] = mapped_column(String(16))
    permission_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("relation_type", RELATION_TYPES), name="relation_type"),
        UniqueConstraint("source_id", "derived_id", "relation_type", name="src_derived_rel"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    domain: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(HASH64)
    size: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verifier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (CheckConstraint(_in("domain", DOMAINS), name="domain"),)


# ---------------------------------------------------------------------------
# Agents, leases, skills, evaluations
# ---------------------------------------------------------------------------
class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    semantic_version: Mapped[str] = mapped_column(String(80))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    endpoint_key: Mapped[str] = mapped_column(String(80))
    protocol_version: Mapped[str] = mapped_column(String(40), default="1.0")
    state: Mapped[str] = mapped_column(String(16), default="candidate")
    healthy: Mapped[bool] = mapped_column(Boolean, default=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("state", AGENT_STATES), name="state"),
        UniqueConstraint("name", "semantic_version", name="name_version"),
    )


class AgentLease(Base):
    __tablename__ = "agent_leases"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[str] = mapped_column(ID, index=True)
    starts_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    state: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (CheckConstraint(_in("state", LEASE_STATES), name="state"),)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    semantic_version: Mapped[str] = mapped_column(String(80))
    package_hash: Mapped[str] = mapped_column(HASH64)
    domain: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="staged")
    source: Mapped[str] = mapped_column(String(300))
    license: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("domain", DOMAINS), name="domain"),
        CheckConstraint(_in("state", SKILL_STATES), name="state"),
        UniqueConstraint("name", "semantic_version", name="name_version"),
        UniqueConstraint("package_hash", name="package_hash_immutable"),
    )


class SkillEvaluation(Base):
    __tablename__ = "skill_evaluations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    subject_digest: Mapped[str] = mapped_column(HASH64, index=True)
    static_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    dynamic_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    functional_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    professional_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluator: Mapped[str] = mapped_column(String(120), default="core")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
class BudgetReservation(Base):
    __tablename__ = "budget_reservations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    period: Mapped[str] = mapped_column(String(32))  # e.g. "task" | "month:2026-09"
    scope: Mapped[str] = mapped_column(String(32))  # task | root | monthly
    amount: Mapped[Any] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    state: Mapped[str] = mapped_column(String(16), default="reserved")
    idempotency_key: Mapped[str] = mapped_column(String(120))
    settled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("state", RESERVATION_STATES), name="state"),
        UniqueConstraint("idempotency_key", name="reservation_idem"),
    )


class BudgetLedger(Base):
    __tablename__ = "budget_ledger"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    reservation_id: Mapped[str | None] = mapped_column(ForeignKey("budget_reservations.id"), nullable=True)
    task_id: Mapped[str] = mapped_column(ID, index=True)
    delta: Mapped[Any] = mapped_column(MONEY)
    reason: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Audit chain & anchors
# ---------------------------------------------------------------------------
class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, unique=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_hash: Mapped[str] = mapped_column(HASH64)
    hash: Mapped[str] = mapped_column(HASH64)


class AuditAnchor(Base):
    __tablename__ = "audit_anchors"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    storage: Mapped[str] = mapped_column(String(120))
    head_hash: Mapped[str] = mapped_column(HASH64)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Tombstone(Base):
    __tablename__ = "tombstones"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    target_id: Mapped[str] = mapped_column(ID, index=True)
    target_kind: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(300))
    deleted_by: Mapped[str] = mapped_column(String(200))
    dep_graph_hash: Mapped[str] = mapped_column(HASH64)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Search index (FTS + vector; tsvector is a Postgres generated column)
# ---------------------------------------------------------------------------
class SearchDocument(Base):
    __tablename__ = "search_documents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    record_id: Mapped[str] = mapped_column(ID, index=True)
    record_kind: Mapped[str] = mapped_column(String(24))
    domain: Mapped[str] = mapped_column(String(16), index=True)
    content_hash: Mapped[str] = mapped_column(HASH64)
    # Postgres: pgvector Vector(1536); SQLite tests: JSON placeholder.
    embedding: Mapped[Any] = mapped_column(Vector(1536).with_variant(SQLiteJSON(), "sqlite"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    tombstoned_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        Index("ix_search_rec", "record_id", "record_kind", unique=True),
        CheckConstraint(_in("domain", DOMAINS), name="domain"),
    )
