"""Session-state persistence models (工单 P1-21).

Persists restorable session-state snapshots so that, after a backend restart,
the runtime state (short-term memory window, tool registry, DSL run archive)
can be rebuilt from the primary database and reproduce the pre-restart
snapshot byte-for-byte. One table:

- ``session_state_snapshots`` — one row per ``session_key`` (upsert: the
  latest snapshot wins), holding the JSON payload produced by
  :mod:`find_yourself.services.state_persistence`.

This module owns its table and registers it on the shared ``Base`` metadata;
it does not modify ``db/models.py``.
"""

from datetime import datetime

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class SessionStateSnapshot(Base):
    __tablename__ = "session_state_snapshots"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    session_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow, onupdate=utcnow)
