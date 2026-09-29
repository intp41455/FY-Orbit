"""In-memory deterministic Core ports — FOR LOCAL UNIT TESTS AND `--adapter fake`.

This implements the same :class:`CorePorts` shape as the future production
adapter, but with no database, no network and no model calls. It is driven by
an explicit script so Temporal test-env runs are deterministic.

It MUST NOT be used for integration acceptance. Real Postgres/Temporal
verification is marked NOT_RUN until containers exist.
"""
from __future__ import annotations

from typing import Any

from temporalio.exceptions import ApplicationError

from .models import (
    ApprovalVerdict,
    OutboxClaim,
    ReserveResult,
    StepProposal,
    ToolResult,
)


class InMemoryPorts:
    def __init__(self) -> None:
        # Scripted planner: popped in order; exhausted -> finish.
        self.plan_script: list[dict[str, Any]] = []
        # Scripted tool results: popped per run_tool_step call. A dict with
        # "_raise" raises a retryable activity error instead.
        self.tool_script: list[dict[str, Any]] = []
        # Scripted external-effect results: popped per execute_external_effect.
        self.external_script: list[dict[str, Any]] = []
        # Approval DB: proposal_id -> state.
        self.proposals: dict[str, dict[str, Any]] = {}
        # Budget ledger.
        self.budget_remaining: float = 1.0
        self.price_unknown: bool = False
        self.reserved_total: float = 0.0
        # Observation counters / logs.
        self.tool_calls: list[dict[str, Any]] = []
        self.external_calls: list[dict[str, Any]] = []
        self.claim_keys: list[str] = []
        self._claim_set: set[str] = set()
        self.lifecycle: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.completed_result: dict[str, Any] | None = None
        self.failure_info: dict[str, str] | None = None
        self.cancelled_reason: str | None = None

    # -- task lifecycle ----------------------------------------------------
    async def task_record_started(self, task_id: str, attempt: int, ck: str) -> None:
        self.lifecycle.append({"op": "started", "task_id": task_id, "attempt": attempt, "ck": ck})

    async def task_update_stage(self, task_id: str, attempt: int, stage: str, ck: str) -> None:
        self.lifecycle.append({"op": "stage", "task_id": task_id, "attempt": attempt,
                               "stage": stage, "ck": ck})

    async def task_complete(self, task_id: str, attempt: int, result: dict[str, Any]) -> None:
        self.lifecycle.append({"op": "complete", "task_id": task_id, "attempt": attempt})
        self.completed_result = result

    async def task_fail(self, task_id: str, attempt: int, failure: dict[str, str]) -> None:
        self.lifecycle.append({"op": "fail", "task_id": task_id, "attempt": attempt,
                               "code": failure.get("code")})
        self.failure_info = failure

    async def task_cancel(self, task_id: str, attempt: int, reason: str) -> None:
        self.lifecycle.append({"op": "cancel", "task_id": task_id, "attempt": attempt,
                               "reason": reason})
        self.cancelled_reason = reason

    # -- planner -----------------------------------------------------------
    async def plan_next_step(self, task_id, attempt, stage, history) -> StepProposal:
        if not self.plan_script:
            return StepProposal(kind="finish", reason="no more steps")
        raw = self.plan_script.pop(0)
        return StepProposal.model_validate(raw)

    # -- budget ------------------------------------------------------------
    async def reserve_budget(self, task_id, attempt, estimated_usd, scope) -> ReserveResult:
        if self.price_unknown:
            return ReserveResult(reserved=False, code="price_unknown", remaining_usd=0.0)
        if estimated_usd > self.budget_remaining + 1e-9:
            return ReserveResult(reserved=False, code="budget_exceeded",
                                 remaining_usd=self.budget_remaining)
        self.budget_remaining -= estimated_usd
        self.reserved_total += estimated_usd
        return ReserveResult(reserved=True, remaining_usd=self.budget_remaining)

    async def settle_budget(self, task_id, attempt, spent_usd, reason) -> None:
        # Finalize: the reserved estimate was already deducted; release the
        # difference between estimate and actual spend.
        self.reserved_total = max(0.0, self.reserved_total - spent_usd)

    async def release_budget(self, task_id, attempt) -> None:
        self.budget_remaining += self.reserved_total
        self.reserved_total = 0.0

    # -- tool / model step -------------------------------------------------
    async def run_tool_step(self, task_id, attempt, tool, payload, idempotency_key, ck) -> ToolResult:
        self.tool_calls.append({"tool": tool, "payload": payload,
                                "idempotency_key": idempotency_key})
        raw = self.tool_script.pop(0) if self.tool_script else {"status": "ok", "spent_usd": 0.01}
        if raw.get("_raise"):
            # Transient, retryable failure. Temporal's activity retry policy
            # will re-run; unknown/irreversible paths never take this branch.
            raise ApplicationError(raw["_raise"], type="RetryableError")
        return ToolResult.model_validate(raw)

    # -- approval ----------------------------------------------------------
    async def verify_approval_permission(self, proposal_id, expected_digest, expected_version) -> ApprovalVerdict:
        p = self.proposals.get(proposal_id)
        if not p:
            return ApprovalVerdict(allowed=False, code="not_found", message="missing")
        if p["status"] in {"executed", "done"}:
            return ApprovalVerdict(allowed=False, code="already_executed",
                                   message="already executed", proposal_status=p["status"],
                                   digest=p["digest"], expected_version=p["expected_version"])
        if p["status"] == "rejected":
            return ApprovalVerdict(allowed=False, code="rejected", message="rejected by owner",
                                   proposal_status=p["status"])
        if p["status"] == "expired" or p.get("expired"):
            return ApprovalVerdict(allowed=False, code="expired", message="expired",
                                   proposal_status=p["status"])
        if p["digest"] != expected_digest:
            return ApprovalVerdict(allowed=False, code="digest_mismatch", message="digest mismatch",
                                   proposal_status=p["status"])
        if p["expected_version"] != expected_version:
            return ApprovalVerdict(allowed=False, code="stale_version", message="version drift",
                                   proposal_status=p["status"])
        if p["status"] not in {"pending", "approved_pending_execution"}:
            return ApprovalVerdict(allowed=False, code="rejected",
                                   message=f"status {p['status']} not executable",
                                   proposal_status=p["status"])
        return ApprovalVerdict(allowed=True, code="ok", message="allowed",
                               proposal_status=p["status"], digest=p["digest"],
                               expected_version=p["expected_version"],
                               execution_id=f"exec-{proposal_id}")

    async def claim_outbox_operation(self, proposal_id, idempotency_key) -> OutboxClaim:
        if idempotency_key in self._claim_set:
            return OutboxClaim(claimed=False, code="already_done")
        self._claim_set.add(idempotency_key)
        self.claim_keys.append(idempotency_key)
        return OutboxClaim(claimed=True, code="claimed", operation_id=f"op-{proposal_id}")

    async def execute_external_effect(self, task_id, attempt, proposal_id, operation_id) -> ToolResult:
        self.external_calls.append({"proposal_id": proposal_id, "operation_id": operation_id})
        raw = self.external_script.pop(0) if self.external_script else {"status": "ok", "spent_usd": 0.0}
        return ToolResult.model_validate(raw)

    async def record_outbox_result(self, proposal_id, external_state, detail) -> None:
        self.lifecycle.append({"op": "outbox", "proposal_id": proposal_id,
                               "state": external_state})
        if external_state == "executed":
            p = self.proposals.get(proposal_id)
            if p:
                p["status"] = "executed"

    async def append_audit(self, actor, action, target, details) -> None:
        self.audit.append({"actor": actor, "action": action, "target": target})
