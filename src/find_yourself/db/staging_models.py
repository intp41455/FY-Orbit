"""Work-stash (记录暂存区) models (工单 P1-04).

A user's working-record scratchpad: records staged via ``POST /api/stash``
survive backend restarts (persisted in the primary database, never
memory-only). One table:

- ``work_stashes`` — content + metadata owned by ``owner_id``; restored,
  listed and cleared through the stash API.

This module owns its table and registers it on the shared ``Base`` metadata;
it does not modify ``db/models.py`` (in-flight file owned by another task).
"""

from datetime import datetime

from sqlalchemy import Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class WorkStash(Base):
    __tablename__ = "work_stashes"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="text/plain")
    # ``metadata`` is reserved by DeclarativeBase, so the attribute is
    # ``stash_metadata`` while the column keeps the API-facing name.
    stash_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_work_stashes_owner_created", "owner_id", "created_at"),)
