"""Database models for selective data synchronization and offline-first persistence.

Supports Phase A & D of the multi-client product specification:
- Explicit sync mode per owner ('local_only' default, 'sync_opt_in' user-confirmed)
- Strict classification: raw dialogues/attachments and credentials are strictly local_only
- Versioned sync journal with immutable tombstones for reliable deletion propagation
- Concurrency conflict tracking preserving divergent versions for explicit user resolution
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import HASH64, ID, TZDateTime, utcnow

SYNC_MODES = ("local_only", "sync_opt_in")
CONFLICT_STATUSES = ("pending", "resolved_local", "resolved_remote")

# Category classifications
# Strictly local categories cannot be synchronized regardless of user settings
LOCAL_ONLY_CATEGORIES = frozenset([
    "raw_dialog_attachments",
    "credentials_and_keys",
    "audit_events",
    "local_hardware_probes",
])

# Opt-in categories eligible for selective cross-device synchronization
OPT_IN_SYNC_CATEGORIES = frozenset([
    "profiles_and_corrections",
    "canvas_topology_tasks",
    "official_memories",
    "chart_records",
])


class SyncSetting(Base):
    """User-level synchronization preferences and device identity."""

    __tablename__ = "sync_settings"

    owner_id: Mapped[str] = mapped_column(ID, primary_key=True)
    mode: Mapped[str] = mapped_column(
        String(32),
        CheckConstraint("mode IN ('local_only', 'sync_opt_in')", name="ck_sync_settings_mode"),
        default="local_only",
        nullable=False,
    )
    enabled_categories: Mapped[list[str]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        default=lambda: ["profiles_and_corrections", "canvas_topology_tasks"],
        nullable=False,
    )
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[Any | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SyncJournal(Base):
    """Monotonically versioned changelog of synchronizable entities with deletion tombstones."""

    __tablename__ = "sync_journals"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ID, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    payload_hash: Mapped[str] = mapped_column(HASH64, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    is_tombstone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_id", "entity_type", "entity_id", "version", name="uq_sync_journal_version"),
        Index("ix_sync_journal_cursor", "owner_id", "created_at"),
    )


class SyncConflict(Base):
    """Concurrency conflict preserving diverging local and remote changes for user resolution."""

    __tablename__ = "sync_conflicts"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ID, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    local_version: Mapped[int] = mapped_column(Integer, nullable=False)
    local_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    remote_version: Mapped[int] = mapped_column(Integer, nullable=False)
    remote_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    resolution_status: Mapped[str] = mapped_column(
        String(32),
        CheckConstraint("resolution_status IN ('pending', 'resolved_local', 'resolved_remote')", name="ck_sync_conflicts_status"),
        default="pending",
        nullable=False,
    )
    created_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[Any | None] = mapped_column(TZDateTime, nullable=True)
