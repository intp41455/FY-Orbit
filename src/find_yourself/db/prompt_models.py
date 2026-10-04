"""Prompt template library models (行动项 #13 / 工单 P1-06).

Three tables:

- ``prompt_templates``  — the addressable template (unique ``name``), whose
  ``latest_version`` doubles as the *effective version pointer*: rendering
  without an explicit version resolves through it, so rollback is a pointer
  switch, never a rewrite ("变更只增不改").
- ``prompt_versions``   — append-only content snapshots; rollback/history keep
  every prior version row untouched.
- ``prompt_render_logs``— deterministic-render audit: hashes only, never the
  plaintext variables (privacy alignment R03).

This module owns its tables and registers them on the shared ``Base`` metadata;
it does not modify ``db/models.py`` (in-flight file owned by another task).
"""

from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import HASH64, ID, TZDateTime, utcnow

PROMPT_SCOPES = ("platform", "workbench", "game_tree")
PROMPT_VAR_TYPES = ("str", "int", "float", "bool", "list")


def _in(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    latest_version: Mapped[int] = mapped_column(Integer, default=0)
    variables_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    owner: Mapped[str] = mapped_column(String(200), default="")
    scope: Mapped[str] = mapped_column(String(20), default="platform")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(_in("scope", PROMPT_SCOPES), name="ck_prompt_tpl_scope"),
        Index("ux_prompt_templates_name", "name", unique=True),
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt_templates.id"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(HASH64)
    variables_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("prompt_id", "version", name="uq_prompt_version_no"),
    )


class PromptRenderLog(Base):
    __tablename__ = "prompt_render_logs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    template_name: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer)
    variables_hash: Mapped[str] = mapped_column(HASH64)
    scope: Mapped[str | None] = mapped_column(String(20), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
