"""Ports: the boundary between the Temporal workflow shard and Core services.

The workflow and its activities must *never* talk to the database, the model
provider, HTTP or object storage directly. They call these port methods on an
object injected by the Worker at startup. In production that object adapts the
real Core services (TaskService / ProposalService / BudgetService /
OutboxService / a model-gateway tool); during Core's parallel development a
deterministic in-memory fake implements the same shape for unit tests.

Adding a method here is a contract change: propose it to the coordinator before
extending it. Do not let the workflow reach around these ports.
"""
from __future__ import annotations

from typing import Any, Awaitable, Protocol, runtime_checkable

from .models import (
    ApprovalVerdict,
    OutboxClaim,
    ReserveResult,
    StepProposal,
    ToolResult,
)


@runtime_checkable
class CorePorts(Protocol):
    """Everything the workflow needs from Core. Async by convention."""

    # -- Task lifecycle (TaskService) --------------------------------------
    def task_record_started(self, task_id: str, attempt: int, checkpoint_key: str) -> Awaitable[None]:
        """Move the task to running and bind attempt/checkpoint."""
        ...

    def task_update_stage(
        self, task_id: str, attempt: int, stage: str, checkpoint_key: str
    ) -> Awaitable[None]:
        """Persist the current stage + LangGraph checkpoint key (for resume)."""
        ...

    def task_complete(self, task_id: str, attempt: int, result: dict[str, Any]) -> Awaitable[None]: ...

    def task_fail(self, task_id: str, attempt: int, failure: dict[str, str]) -> Awaitable[None]: ...

    def task_cancel(self, task_id: str, attempt: int, reason: str) -> Awaitable[None]:
        """Mark cancelled and release un-settled budget reservations."""
        ...

    # -- Planner (runtime/LangGraph) --------------------------------------
    def plan_next_step(
        self, task_id: str, attempt: int, stage: str, history: list[dict[str, Any]]
    ) -> Awaitable[StepProposal]:
        """Decide the next step. Determinism note: this runs as an activity,
        so it may call LangGraph; the workflow only consumes the decision."""
        ...

    # -- Budget (BudgetService) -------------------------------------------
    def reserve_budget(
        self, task_id: str, attempt: int, estimated_usd: float, scope: str
    ) -> Awaitable[ReserveResult]:
        """Atomic reservation before any billable call. Unknown price must
        return code='price_unknown' and reserved=False."""
        ...

    def settle_budget(
        self, task_id: str, attempt: int, spent_usd: float, reason: str
    ) -> Awaitable[None]: ...

    def release_budget(self, task_id: str, attempt: int) -> Awaitable[None]:
        """Release un-settled reservations on cancel/stop."""
        ...

    # -- Tool / model gateway (the only billable external call) -----------
    def run_tool_step(
        self,
        task_id: str,
        attempt: int,
        tool: str,
        payload: dict[str, Any],
        idempotency_key: str,
        checkpoint_key: str,
    ) -> Awaitable[ToolResult]:
        """Execute one bounded model/tool step. On lost response return
        status='unknown' rather than guessing; the workflow must not retry
        irreversible actions."""
        ...

    # -- Approval (ProposalService) ---------------------------------------
    def verify_approval_permission(
        self, proposal_id: str, expected_digest: str, expected_version: int
    ) -> Awaitable[ApprovalVerdict]:
        """RE-VERIFY the proposal against the approval database: status, digest
        recompute, expected_version, expiry and execution permission. The
        approval signal only wakes us; this is the real authorization check."""
        ...

    def claim_outbox_operation(
        self, proposal_id: str, idempotency_key: str
    ) -> Awaitable[OutboxClaim]:
        """Conditional single-consumption claim (outbox). Second claim returns
        already_done. Guarantees the external effect runs at most once."""
        ...

    def execute_external_effect(
        self, task_id: str, attempt: int, proposal_id: str, operation_id: str
    ) -> Awaitable[ToolResult]:
        """Execute the approved irreversible effect. Outbox claim already
        guarantees at-most-once; on lost response return status='unknown'."""
        ...

    def record_outbox_result(
        self, proposal_id: str, external_state: str, detail: dict[str, Any]
    ) -> Awaitable[None]:
        """Persist executed/failed/unknown for reconciliation."""
        ...

    # -- Audit (AuditService) ---------------------------------------------
    def append_audit(
        self, actor: str, action: str, target: str, details: dict[str, Any]
    ) -> Awaitable[None]: ...
