"""Pydantic request/response schemas for the HTTP API (FROZEN_CONTRACT §5).

These are the wire contract. They do NOT accept caller-supplied ``owner_id``,
``role`` or ``domain`` as authorization claims — identity comes from the
authenticated :class:`Actor`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- auth -------------------------------------------------------------------
class LocalDevTokenRequest(Strict):
    token: str = Field(min_length=1, max_length=200)


class AuthMe(BaseModel):
    subject_type: str
    owner_id: str = ""
    service_id: str = ""
    service_kind: str = ""
    csrf_token: str = ""
    # W8 account tiers. Both carry honest defaults: a service identity or the
    # legacy bootstrap owner is never a guest, and "unknown" plan means "this
    # owner has no users row" rather than a silently claimed "free" tier.
    is_guest: bool = False
    plan: str = "unknown"


# --- conversations / messages -----------------------------------------------
class ConversationCreate(Strict):
    title: str = Field(default="", max_length=300)
    domain: Literal["personal", "work", "shared"] = "personal"
    mode: Literal["listen", "explore", "research", "engineering", "creative"] = "listen"


class MessageCreate(Strict):
    content: str = Field(min_length=1, max_length=12000)
    role: Literal["user", "assistant"] = "user"
    client_message_id: str = Field(min_length=1, max_length=120)
    source: str | None = Field(default=None, max_length=200)


# --- tasks ------------------------------------------------------------------
class TaskCreate(Strict):
    goal: str = Field(min_length=1, max_length=12000)
    domain: Literal["personal", "work", "shared"] = "personal"
    mode: Literal["listen", "explore", "research", "engineering", "creative"] = "listen"
    strategy: Literal["auto", "single", "delegate", "workflow", "parallel"] = "auto"
    idempotency_key: str = Field(min_length=8, max_length=100)
    deadline: datetime | None = None


# --- proposals ---------------------------------------------------------------
class ProposalCreate(Strict):
    operation: str = Field(min_length=1, max_length=64)
    target_id: str | None = None
    expected_version: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=3000)
    rollback: str = Field(min_length=3, max_length=3000)
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)


class ProposalDecision(Strict):
    digest: str = Field(min_length=1, max_length=64)
    decision: Literal["approve", "reject"]


# --- assessments ------------------------------------------------------------
class AssessmentAnswers(Strict):
    answers: dict[str, int]


# --- export ------------------------------------------------------------------
class ExportRequest(Strict):
    confirm: bool = False
