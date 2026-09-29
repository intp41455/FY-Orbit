from datetime import datetime, timezone, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Domain(StrEnum):
    personal = "personal"
    work = "work"
    shared = "shared"


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    waiting_input = "waiting_input"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Limits(Strict):
    max_steps: int = Field(default=8, ge=1, le=100)
    max_depth: int = Field(default=2, ge=0, le=4)
    max_concurrency: int = Field(default=2, ge=1, le=8)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_cost_usd: float = Field(default=0.5, gt=0, le=100)
    max_output_tokens: int = Field(default=1500, ge=100, le=8000)


class TaskCreate(Strict):
    goal: str = Field(min_length=1, max_length=12000)
    domain: Domain = Domain.personal
    mode: Literal["listen", "explore", "research", "engineering", "creative"] = "listen"
    strategy: Literal["auto", "single", "delegate", "workflow", "parallel"] = "auto"
    acceptance: list[str] = Field(default_factory=list, max_length=20)
    input_refs: list[str] = Field(default_factory=list, max_length=50)
    conversation_id: str | None = None
    limits: Limits = Field(default_factory=Limits)
    deadline: datetime = Field(default_factory=lambda: now() + timedelta(hours=1))
    idempotency_key: str = Field(default_factory=uid, min_length=8, max_length=100)


class TaskEnvelope(TaskCreate):
    schema_version: str = "1.0"
    task_id: str = Field(default_factory=uid)
    trace_id: str = Field(default_factory=uid)
    parent_task_id: str | None = None
    owner_id: str
    status: TaskStatus = TaskStatus.queued
    result: dict[str, Any] | None = None
    failure: dict[str, str] | None = None
    spent_usd: float = 0
    reserved_usd: float = 0
    steps: int = 0
    depth: int = 0
    stage: str = "requirements"
    created_at: datetime = Field(default_factory=now)


class MemoryRecord(Strict):
    domain: Domain
    content: str = Field(min_length=1, max_length=20000)
    category: Literal["self_report", "tool_fact", "assessment_result", "theory", "hypothesis", "preference"]
    sources: list[str] = Field(min_length=1, max_length=30)
    endorsed: bool = False
    active: bool = True


class AgentManifest(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,50}$")
    version: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(min_length=1, max_length=30)
    domains: list[Domain] = Field(min_length=1)
    endpoint_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,50}$")
    interface_version: str = "1.0"
    max_concurrency: int = Field(default=1, ge=1, le=8)


class ArtifactManifest(Strict):
    artifact_id: str = Field(default_factory=uid)
    task_id: str
    sha256: str
    media_type: str = "text/plain"
    size: int
    domain: Domain
    verified: bool = False
    sources: list[str] = Field(default_factory=list)


class ProposalCreate(Strict):
    operation: Literal["memory.upsert", "memory.delete", "grant.add", "grant.revoke", "agent.register",
                       "agent.drain", "skill.stage", "skill.promote", "skill.disable", "config.model",
                       "conversation.delete", "task.merge", "task.release"]
    target_id: str | None = None
    expected_version: int = Field(default=0, ge=0)
    payload: dict[str, Any]
    reason: str = Field(min_length=3, max_length=3000)
    rollback: str = Field(min_length=3, max_length=3000)
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)


class ChangeProposal(ProposalCreate):
    proposal_id: str = Field(default_factory=uid)
    digest: str
    expires_at: datetime
    status: str = "pending"


class ApprovalRecord(Strict):
    proposal_id: str
    digest: str
    decision: Literal["approve", "reject"]


class MessageCreate(Strict):
    domain: Domain = Domain.personal
    mode: Literal["listen", "explore", "research", "engineering", "creative"] = "listen"
    content: str = Field(min_length=1, max_length=12000)


class LegacyResult(Strict):
    ok: bool
    data: Any = None
    evidence: list[str] = Field(default_factory=list)
    error: str | None = None
    retryable: bool = False
