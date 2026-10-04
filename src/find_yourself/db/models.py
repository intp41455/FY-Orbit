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
    # P1-06 prompt template library governance
    "prompt.stage", "prompt.activate", "prompt.disable",
)
PROPOSAL_STATUSES = (
    "pending", "approved_pending_execution", "executing", "executed",
    "failed", "unknown", "rejected", "expired",
)
GRANT_STATES = ("active", "revoked", "expired")
GRANT_DESTINATIONS = ("internal", "gdrive")
HYPOTHESIS_STATES = ("fact", "hypothesis", "theory", "unverified")
# 需求10 retention tiers. A LIFECYCLE axis, orthogonal to ``category``
# (which says what kind of fact a record is). Order is meaningful: it is the
# promotion ladder, short -> medium -> long.
MEMORY_TIERS = ("short", "medium", "long")
SERVICE_KINDS = ("worker", "agent", "tool_gateway", "executor", "release")
IDENTITY_STATES = ("active", "revoked")
USER_STATUSES = ("active", "deleted", "guest")
# W8 account tiers: 会员位. v1 只留位不接支付 (no payment channel wired).
USER_PLANS = ("free", "pro")
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
    # sha256 of the opaque cookie token; NULL for legacy plaintext-id sessions.
    token_hash: Mapped[str | None] = mapped_column(HASH64, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    rotation_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)


class User(Base):
    """A real end user (Route B multi-tenant). ``id`` is the ``owner_id`` used
    across all ownership-scoped tables; the legacy single-owner rows map to the
    bootstrap user backfilled by migration 0011."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    # W8: membership slot. free/pro only; v1 has no payment channel, so every
    # account is born "free" and nothing but an explicit admin/ops write flips it.
    #
    # `server_default` is NOT redundant with the ORM `default`. Migration 0001
    # builds every table from this metadata via `create_all`, so the column is
    # created NOT NULL from the very first migration — and migration 0011 then
    # backfills the legacy bootstrap owner with a *raw* INSERT that does not name
    # `plan`. An ORM-side default cannot help there, so a fresh
    # `alembic upgrade head` died with "NOT NULL constraint failed: users.plan"
    # before reaching 0021. The database-level default is what makes the column
    # safe for every writer, ORM or not.
    plan: Mapped[str] = mapped_column(String(16), default="free", server_default="free")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("status", USER_STATUSES), name="ck_user_status"),
        CheckConstraint(_in("plan", USER_PLANS), name="ck_user_plan"),
    )


class UserConsent(Base):
    """GDPR-style consent record captured at registration (and later updates)."""

    __tablename__ = "user_consents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    doc_id: Mapped[str] = mapped_column(String(80), default="privacy-policy")
    doc_version: Mapped[str] = mapped_column(String(40), default="v1")
    agreed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")


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
        CheckConstraint(_in("kind", SERVICE_KINDS), name="ck_svcident_kind"),
        CheckConstraint(_in("state", IDENTITY_STATES), name="ck_svcident_state"),
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
        CheckConstraint(_in("domain", DOMAINS), name="ck_conv_domain"),
        CheckConstraint(_in("mode", MODES), name="ck_conv_mode"),
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
        CheckConstraint(_in("role", MSG_ROLES), name="ck_msg_role"),
        UniqueConstraint("conversation_id", "client_message_id", name="uq_msg_conv_clientmsg"),
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
        CheckConstraint(_in("domain", DOMAINS), name="ck_task_domain"),
        CheckConstraint(_in("mode", MODES), name="ck_task_mode"),
        CheckConstraint(_in("strategy", STRATEGIES), name="ck_task_strategy"),
        CheckConstraint(_in("status", TASK_STATUSES), name="ck_task_status"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_task_owner_idem"),
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
        CheckConstraint(_in("status", ATTEMPT_STATUSES), name="ck_attempt_status"),
        UniqueConstraint("task_id", "attempt_no", name="uq_attempt_task_no"),
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
        CheckConstraint(_in("operation", PROPOSAL_OPS), name="ck_prop_operation"),
        CheckConstraint(_in("status", PROPOSAL_STATUSES), name="ck_prop_status"),
        UniqueConstraint("digest", name="uq_prop_digest"),
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
    destination: Mapped[str] = mapped_column(String(32), default="internal")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("source_domain", DOMAINS), name="ck_grant_srccol"),
        CheckConstraint(_in("consumer_domain", DOMAINS), name="ck_grant_consumcol"),
        CheckConstraint(_in("state", GRANT_STATES), name="ck_grant_state"),
        # No wildcard / empty grants: record_ids must be a non-empty list.
        CheckConstraint("json_array_length(record_ids) > 0", name="ck_grant_nonempty_records"),
        CheckConstraint(_in("destination", GRANT_DESTINATIONS), name="ck_grant_destination"),
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
        CheckConstraint(_in("state", OP_STATES), name="ck_op_state"),
        UniqueConstraint("proposal_id", name="uq_op_one_per_proposal"),
        UniqueConstraint("idempotency_key", name="uq_op_idem"),
    )


# ---------------------------------------------------------------------------
# Memory, revisions, source graph
# ---------------------------------------------------------------------------
class Memory(Base):
    """A single remembered item, on a three-tier retention ladder (需求10).

    Retention tiers (``tier``, migration 0026) are a **lifecycle** dimension and
    are deliberately kept separate from ``category``, which is a **semantic**
    dimension (what kind of fact this is). Conflating them would make
    ``SAME_DOMAIN_CATEGORIES``-style reasoning impossible — "a preference" and
    "a fact that outlives this session" are independent axes, and a record is
    routinely both at once.

    Tier semantics and the rules that actually move a record between tiers:

    - ``short``  — session-scoped working memory. Bound to the ``session_id``
      that produced it and carries a ``tier_expires_at`` deadline. It is
      **evicted** once that deadline passes: reads stop returning it and
      :meth:`MemoryService.decay` soft-deletes it. This is the only tier with a
      hard deadline.
    - ``medium`` — project-scoped. Survives sessions and has no hard expiry, but
      *decays by disuse*: a medium record not reinforced within
      ``MEDIUM_MAX_IDLE_DAYS`` is demoted back to short by ``decay()``. It is
      the only tier that can be promoted without a human decision.
    - ``long``  — the durable user profile. Reachable **only** through an
      explicit owner-endorsed promotion; no timer may ever evict or demote it,
      because silently forgetting a stated user profile is data loss.

    ``session_count`` / ``reinforcement_count`` are the evidence the promotion
    gates read, and ``tier_changed_at`` records when the last transition
    happened. Existing rows are backfilled to ``medium`` (see migration 0026),
    which is read-equivalent to the pre-tier behaviour.
    """

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
    # --- retention tier (需求10, migration 0026) -------------------------
    tier: Mapped[str] = mapped_column(String(8), default="medium", server_default="medium")
    #: Session that produced a short-tier record; NULL for medium/long.
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Hard deadline for the short tier. NULL means "no deadline" (medium/long).
    tier_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Distinct sessions this record has been carried into (short->medium gate).
    session_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Times the record was re-derived or explicitly reused (medium->long gate).
    reinforcement_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Decay clock for the medium tier: last time the record was actually used.
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    tier_changed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("domain", DOMAINS), name="ck_mem_domain"),
        CheckConstraint(_in("hypothesis_status", HYPOTHESIS_STATES), name="ck_mem_hyp_status"),
        CheckConstraint(_in("tier", MEMORY_TIERS), name="ck_mem_tier"),
        # A short-tier record MUST carry a deadline, otherwise "evicted when the
        # session ends" is unenforceable and short records would silently become
        # immortal. ``session_id`` is deliberately NOT required here: a record
        # demoted from medium has no originating session, and inventing one
        # would fabricate provenance. Such a record simply is not visible to any
        # *scoped* read (a scoped read only matches its own session id) while
        # staying visible to the owner's unscoped read.
        CheckConstraint(
            "tier != 'short' OR tier_expires_at IS NOT NULL",
            name="ck_mem_short_has_deadline",
        ),
        # The long tier is the user profile: it must never carry a clock, so no
        # sweep can ever evict it.
        CheckConstraint(
            "tier != 'long' OR tier_expires_at IS NULL",
            name="ck_mem_long_never_expires",
        ),
        CheckConstraint(
            "session_count >= 0 AND reinforcement_count >= 0", name="ck_mem_counters_nonneg"
        ),
        # Tier-filtered reads are always scoped to one owner, so the two columns
        # are always queried together. Declared here (not only in migration 0026)
        # because a fresh database is built by ``Base.metadata.create_all`` and
        # would otherwise never get the index.
        Index("ix_memories_owner_tier", "owner_id", "tier"),
    )


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(HASH64)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class MemoryTierSession(Base):
    """One distinct session that picked up a memory (需求10 promotion evidence).

    ``memories.session_count`` is a denormalised cache of ``COUNT(*)`` here. The
    count alone cannot express *which* sessions were involved, and without that
    the "carried across sessions" promotion gate would be trusting a caller to
    self-report novelty. The unique constraint makes a session count **once**,
    so a single chatty session cannot manufacture carry-over evidence by
    repeating itself.
    """

    __tablename__ = "memory_tier_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("memory_id", "session_id", name="uq_mem_tier_session"),
    )


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
        CheckConstraint(_in("relation_type", RELATION_TYPES), name="ck_src_rel_type"),
        UniqueConstraint("source_id", "derived_id", "relation_type", name="uq_src_derived_rel"),
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

    __table_args__ = (CheckConstraint(_in("domain", DOMAINS), name="ck_art_domain"),)


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
        CheckConstraint(_in("state", AGENT_STATES), name="ck_agent_state"),
        UniqueConstraint("name", "semantic_version", name="uq_agent_name_version"),
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

    __table_args__ = (CheckConstraint(_in("state", LEASE_STATES), name="ck_lease_state"),)


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
        CheckConstraint(_in("domain", DOMAINS), name="ck_skill_domain"),
        CheckConstraint(_in("state", SKILL_STATES), name="ck_skill_state"),
        UniqueConstraint("name", "semantic_version", name="uq_skill_name_version"),
        UniqueConstraint("package_hash", name="uq_skill_pkg_hash_immutable"),
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
        CheckConstraint(_in("state", RESERVATION_STATES), name="ck_res_state"),
        UniqueConstraint("idempotency_key", name="uq_res_idem"),
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
        CheckConstraint(_in("domain", DOMAINS), name="ck_sdoc_domain"),
    )


# ---------------------------------------------------------------------------
# Re-export 04 Profile and 05 Canvas models
# ---------------------------------------------------------------------------
from find_yourself.db.profile_models import (  # noqa: E402
    ProfileSubject, ProfileImport, SourceSegment, ProfileEvidence,
    ProfileRun, ProfileRevision, ProfileFeedback,
)
from find_yourself.db.canvas_models import (  # noqa: E402
    CanvasInstance, DispatchRecord, HandoffPacket, CanvasEvent,
)

