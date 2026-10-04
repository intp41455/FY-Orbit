"""Database models for 19 单Agent内部团队与逐节点模型配置.

Implements the contract objects required by
``19_单Agent内部团队与逐节点模型配置实施规格.md`` §6:

- TeamDefinition          : versioned team draft (mode, coordinator, members,
                            dependencies, default model, budget/permission refs).
- AgentInstance           : one member — parent task, role, host, its OWN
                            independent session, model binding, state, run batch.
- ModelBinding            : requested vs effective model, params, credential
                            reference (never the secret), capability snapshot and
                            pricing version.
- AgentControlCapabilities: spawn/events/pause/cancel/reassign/switch_model/
                            usage/checkpoint with verified|unsupported|unknown.
- ControlRequest          : idempotency key, expected revision, target
                            operation, operator, scope, execution result.
- TeamEvent               : monotonic per-team event log with (team, member,
                            batch) attribution for reconnect replay and dedupe.

Two invariants are enforced by the schema, not just by service code:

* ``model_bindings.requested_model`` and ``effective_model`` are separate
  columns so a provider that hides routing can be recorded as ``unknown`` /
  ``auto`` instead of being presented as a precise version (19 §3).
* ``team_events`` carries a unique ``(team_id, seq)`` index so a reconnecting
  client can resume from a cursor without losing or duplicating an event
  (19 §8).

No table stores a reusable token, secret or private memory text. Provider
credentials live behind ``credential_ref``, a server-side reference only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from find_yourself.db.base import Base
from find_yourself.db.types import ID, MONEY, TZDateTime, utcnow

TEAM_MODES = ("system_managed", "product_native")
TEAM_STATES = ("draft", "validating", "ready", "running", "paused", "completed", "failed", "cancelled")
AGENT_STATES = (
    "draft", "starting", "running", "blocked", "paused",
    "waiting_rework", "completed", "failed", "cancelled", "unknown_needs_reconciliation",
)
CAPABILITY_STATES = ("verified", "unsupported", "unknown")
CONTROL_OPERATIONS = (
    "pause", "resume", "reassign", "rework", "cancel", "switch_model", "spawn",
)
CONTROL_STATES = ("pending", "applied", "rejected", "unknown_needs_reconciliation", "superseded")
EFFECTIVE_MODEL_CONFIDENCE = ("exact", "unknown", "auto", "not_executed")
BINDING_SCOPES = ("global", "team", "role", "node")


class TeamDefinition(Base):
    """Versioned team draft. Every mutation bumps ``version`` (19 §2)."""

    __tablename__ = "team_definitions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(32), default="system_managed")
    state: Mapped[str] = mapped_column(String(32), default="draft")
    #: Linked 05 canvas instance so the team shows up as a canvas sub-feature
    #: rather than a second, conflicting orchestration surface.
    canvas_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    root_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    coordinator_role: Mapped[str] = mapped_column(String(64), default="coordinator")
    #: member dicts: role, agent_host, provider_id, depends_on, title...
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: role -> role-level model override (19 §3 hierarchy level 3)
    role_bindings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_binding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    budget_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    permission_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_change_reason: Mapped[str] = mapped_column(Text, default="")
    last_changed_by: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(f"mode IN {TEAM_MODES}", name="team_mode"),
        CheckConstraint(f"state IN {TEAM_STATES}", name="team_state"),
        CheckConstraint("plan_version >= 1", name="team_plan_version_positive"),
        CheckConstraint("version >= 1", name="team_version_positive"),
    )


class ModelBinding(Base):
    """Resolved per-node model binding with requested vs effective model (19 §3)."""

    __tablename__ = "model_bindings"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("team_definitions.id", ondelete="CASCADE"), index=True
    )
    agent_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="node")
    scope_key: Mapped[str] = mapped_column(String(64), default="")
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    requested_model: Mapped[str] = mapped_column(String(160), default="")
    effective_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    effective_confidence: Mapped[str] = mapped_column(String(16), default="not_executed")
    model_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Server-side reference only. The secret itself never enters this table,
    #: a node prompt, a browser response or a log line (19 §5).
    credential_ref: Mapped[str] = mapped_column(String(200), default="")
    credential_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    endpoint_ref: Mapped[str] = mapped_column(String(200), default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capability_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pricing_version: Mapped[str] = mapped_column(String(64), default="")
    budget_reserved_usd: Mapped[float] = mapped_column(MONEY, default=0)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(f"scope IN {BINDING_SCOPES}", name="binding_scope"),
        CheckConstraint(f"effective_confidence IN {EFFECTIVE_MODEL_CONFIDENCE}", name="binding_confidence"),
    )


class AgentInstance(Base):
    """A team member: its own task and its OWN independent session (19 §8)."""

    __tablename__ = "agent_instances"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("team_definitions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    agent_host: Mapped[str] = mapped_column(String(64), default="find_yourself")
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="draft")
    #: Distinct session per member. Two roles on the same model must not share
    #: it, and must not see each other's context (19 §8 first scenario).
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    root_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    subtask_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_binding_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_model: Mapped[str] = mapped_column(String(160), default="")
    effective_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    effective_confidence: Mapped[str] = mapped_column(String(16), default="not_executed")
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list)
    run_batch: Mapped[int] = mapped_column(Integer, default=0)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    max_steps: Mapped[int] = mapped_column(Integer, default=8)
    budget_reserved_usd: Mapped[float] = mapped_column(MONEY, default=0)
    budget_spent_usd: Mapped[float] = mapped_column(MONEY, default=0)
    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    current_goal: Mapped[str] = mapped_column(Text, default="")
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(f"state IN {AGENT_STATES}", name="agent_state"),
        CheckConstraint("run_batch >= 0", name="agent_run_batch_non_negative"),
        UniqueConstraint("team_id", "role", name="uq_agent_team_role"),
    )


class AgentControlCapabilities(Base):
    """What the host product actually exposes, honestly tri-stated (19 §1, §3)."""

    __tablename__ = "agent_control_capabilities"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    agent_host: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    #: {spawn: verified|unsupported|unknown, ...}
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    supports_per_member_model: Mapped[bool] = mapped_column(Boolean, default=False)
    usage_metering: Mapped[str] = mapped_column(String(16), default="unknown")
    probe_source: Mapped[str] = mapped_column(String(64), default="static")
    reason: Mapped[str] = mapped_column(Text, default="")
    probed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            f"usage_metering IN {CAPABILITY_STATES}", name="capability_usage_metering"
        ),
    )


class ControlRequest(Base):
    """An auditable control operation on one member (19 §4)."""

    __tablename__ = "control_requests"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("team_definitions.id", ondelete="CASCADE"), index=True
    )
    agent_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    expected_version: Mapped[int] = mapped_column(Integer, default=0)
    target_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator: Mapped[str] = mapped_column(String(200), default="")
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(f"operation IN {CONTROL_OPERATIONS}", name="control_operation"),
        CheckConstraint(f"state IN {CONTROL_STATES}", name="control_state"),
    )


class TeamEvent(Base):
    """Monotonic per-team event log for reconnect replay and dedupe (19 §8)."""

    __tablename__ = "team_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("team_definitions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_batch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="system")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_team_event_team_seq", "team_id", "seq", unique=True),
        Index("ix_team_event_member", "team_id", "agent_instance_id", "seq"),
    )


# ===========================================================================
# W7 · Agent 通信总线 — 房间共享上下文（bus_context）
#
# 追加段落（共享文件，只加不改他人行）。消息本体是进程内环形缓冲
# （runtime/agent_bus.py），只有「共享上下文附件」需要持久化：登记文件引用 /
# 文本片段，供房间内消息用 refs 引用，画布与工作台可读。
# v1 不做向量化；需要检索时引用 W3 的 kb.search。
# ===========================================================================

BUS_CONTEXT_KINDS = ("file_ref", "text")


class BusContextEntry(Base):
    """One shared-context attachment registered in a bus room (W7)."""

    __tablename__ = "bus_context"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    #: 房间归属者。房间可见性只由 owner 决定，客户端传的 owner 无效。
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    room: Mapped[str] = mapped_column(String(200))
    #: file_ref = 外部文件引用（只存引用与摘要，不存正文）；text = 文本片段
    kind: Mapped[str] = mapped_column(String(32), default="text")
    title: Mapped[str] = mapped_column(String(200), default="")
    #: 引用目标（路径 / artifact id / URL），file_ref 必填
    ref: Mapped[str] = mapped_column(Text, default="")
    #: text 片段正文（截断存储）
    content: Mapped[str] = mapped_column(Text, default="")
    #: 登记者身份（owner:<id> / agent:<role>）
    added_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_bus_context_owner_room", "owner_id", "room"),
        CheckConstraint(f"kind IN {BUS_CONTEXT_KINDS}", name="bus_context_kind"),
    )