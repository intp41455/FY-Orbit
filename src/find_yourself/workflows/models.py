"""Workflow-local data models.

These DTOs are the contract between the Temporal workflow, its activities and
the injected Core ports. They deliberately live *inside* the workflow shard so
that the orchestration remains decoupled while Core services evolve in parallel.

Cross-boundary values passed to/from Temporal are plain JSON-serializable
objects (dict/str/int/float/bool/None). Pydantic is used *inside* this package
to validate the plain objects; the Temporal data converter only sees JSON.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Stages. The checkpoint key binds task/attempt/stage so LangGraph can resume
# without re-running external side effects or opening new budgets.
# ---------------------------------------------------------------------------
class Stage(StrEnum):
    queued = "queued"
    requirements = "requirements"
    planning = "planning"
    execution = "execution"
    awaiting_approval = "awaiting_approval"
    awaiting_input = "awaiting_input"
    reconciling = "reconciling"
    review = "review"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# Terminal stages do not drive further external work.
TERMINAL_STAGES = frozenset({Stage.completed, Stage.failed, Stage.cancelled})


def checkpoint_key(task_id: str, attempt: int, stage: str) -> str:
    """Frozen contract section 10: ``task:<task_id>:attempt:<n>:stage:<stage>``."""
    return f"task:{task_id}:attempt:{attempt}:stage:{stage}"


# ---------------------------------------------------------------------------
# Workflow input
# ---------------------------------------------------------------------------
class WorkflowLimits(Strict):
    max_steps: int = Field(default=8, ge=1, le=100)
    max_depth: int = Field(default=2, ge=0, le=4)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_cost_usd: float = Field(default=0.5, gt=0, le=100.0)
    deadline: str = Field(description="timezone-aware UTC ISO-8601 instant")


class TaskWorkflowInput(Strict):
    task_id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=12000)
    domain: str = "personal"
    mode: str = "listen"
    depth: int = Field(default=0, ge=0, le=4)
    idempotency_key: str = Field(min_length=8, max_length=100)
    limits: WorkflowLimits
    # The workflow itself is a deterministic orchestrator. The *plan* for what
    # each step does is produced by the injected Core port (LangGraph/runtime in
    # production; a scripted stub in unit tests). We only carry opaque context.
    context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Step proposals returned by Core's planner port
# ---------------------------------------------------------------------------
class StepProposal(Strict):
    kind: Literal[
        "tool_call",
        "external_side_effect",
        "wait_input",
        "finish",
        "fail",
    ]
    tool: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    # For external_side_effect: the proposal already persisted by Core's
    # ProposalService. The workflow never trusts a signal; it re-verifies via
    # the approval port below.
    proposal_id: Optional[str] = None
    expected_digest: Optional[str] = None
    expected_version: int = 0
    proposal_expires_at: Optional[str] = None
    idempotency_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool / model step result (the only place an external model or tool call
# happens). ``unknown`` means the response was lost: the side effect *may*
# have happened remotely. The workflow must never blind-retry it.
# ---------------------------------------------------------------------------
class ToolResult(Strict):
    status: Literal["ok", "error", "unknown", "budget_exceeded", "cancelled"]
    output: dict[str, Any] | None = None
    spent_usd: float = Field(default=0.0, ge=0.0)
    error_code: str | None = None
    error_message: str | None = None  # SAFE message only; never a stack/secret


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
class ReserveResult(Strict):
    reserved: bool
    remaining_usd: float = 0.0
    code: Literal["ok", "budget_exceeded", "price_unknown", "disabled"] = "ok"


# ---------------------------------------------------------------------------
# Approval re-verification. This is the DB-side truth. The approval *signal*
# only wakes the workflow; it is never trusted for authorization.
# ---------------------------------------------------------------------------
class ApprovalVerdict(Strict):
    allowed: bool
    code: Literal[
        "ok",
        "rejected",
        "expired",
        "digest_mismatch",
        "stale_version",
        "not_found",
        "already_executed",
        "not_owner",
    ]
    message: str
    proposal_status: str = ""
    digest: str = ""
    expected_version: int = 0
    # Set when allowed and the proposal may be executed exactly once.
    execution_id: str | None = None


class OutboxClaim(Strict):
    claimed: bool
    code: Literal["claimed", "already_done", "not_found", "conflict"] = "claimed"
    operation_id: str | None = None


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
class ApprovalSignal(Strict):
    proposal_id: str
    digest: str
    decision: Literal["approve", "reject"] = "approve"


class InputSignal(Strict):
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Failure / completion envelopes
# ---------------------------------------------------------------------------
class FailureInfo(Strict):
    code: str
    message: str  # safe, no secrets
    retryable: bool = False


class TaskWorkflowResult(Strict):
    task_id: str
    status: Literal["completed", "failed", "cancelled", "awaiting_reconciliation"]
    stage: str
    steps: int
    spent_usd: float = 0.0
    failure: FailureInfo | None = None
    result: dict[str, Any] | None = None
    last_checkpoint_key: str = ""
