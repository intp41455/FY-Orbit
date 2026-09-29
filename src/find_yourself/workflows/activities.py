"""Temporal activities: the *only* place external side effects happen.

The workflow must stay deterministic. Every database, model, HTTP and object
storage call lives here, delegating to an injected :class:`CorePorts` adapter.
Activities are constructed by the Worker with the real adapter; unit tests
construct them with the in-memory fake.

Retry semantics (frozen contract section 7/10):
- Billable steps reserve budget first (inside ``run_tool_step`` the adapter
  enforces the reserve; we keep the orchestration explicit).
- ``unknown`` is returned as a *result* (status="unknown"), not as an
  exception, so Temporal does NOT auto-retry an irreversible effect.
- External effects are claimed through the outbox so they execute at most once.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from temporalio import activity

from .models import (
    ApprovalVerdict,
    OutboxClaim,
    ReserveResult,
    StepProposal,
    ToolResult,
)
from .ports import CorePorts


def _dump(obj: Any) -> Any:
    """Pydantic model / dataclass -> plain JSON-able dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if is_dataclass(obj):
        return asdict(obj)
    return obj


class Activities:
    """Bound activity methods. Instantiate once per Worker with the ports."""

    def __init__(self, ports: CorePorts, actor: str = "workflow-worker"):
        self._ports = ports
        self._actor = actor

    # -- registration list -------------------------------------------------
    def all(self) -> list:
        return [
            self.task_record_started,
            self.task_update_stage,
            self.task_complete,
            self.task_fail,
            self.task_cancel,
            self.plan_next_step,
            self.reserve_budget,
            self.settle_budget,
            self.release_budget,
            self.run_tool_step,
            self.verify_approval_permission,
            self.claim_outbox_operation,
            self.execute_external_effect,
            self.record_outbox_result,
            self.append_audit,
        ]

    # -- task lifecycle ----------------------------------------------------
    @activity.defn
    async def task_record_started(self, task_id: str, attempt: int, checkpoint_key: str) -> None:
        await self._ports.task_record_started(task_id, attempt, checkpoint_key)

    @activity.defn
    async def task_update_stage(
        self, task_id: str, attempt: int, stage: str, checkpoint_key: str
    ) -> None:
        await self._ports.task_update_stage(task_id, attempt, stage, checkpoint_key)

    @activity.defn
    async def task_complete(self, task_id: str, attempt: int, result: dict[str, Any]) -> None:
        await self._ports.task_complete(task_id, attempt, result)

    @activity.defn
    async def task_fail(self, task_id: str, attempt: int, failure: dict[str, str]) -> None:
        await self._ports.task_fail(task_id, attempt, failure)

    @activity.defn
    async def task_cancel(self, task_id: str, attempt: int, reason: str) -> None:
        await self._ports.task_cancel(task_id, attempt, reason)

    # -- planner -----------------------------------------------------------
    @activity.defn
    async def plan_next_step(
        self, task_id: str, attempt: int, stage: str, history: list[dict[str, Any]]
    ) -> dict[str, Any]:
        proposal = await self._ports.plan_next_step(task_id, attempt, stage, history)
        return _dump(proposal)

    # -- budget ------------------------------------------------------------
    @activity.defn
    async def reserve_budget(
        self, task_id: str, attempt: int, estimated_usd: float, scope: str
    ) -> dict[str, Any]:
        res = await self._ports.reserve_budget(task_id, attempt, estimated_usd, scope)
        return _dump(res)

    @activity.defn
    async def settle_budget(
        self, task_id: str, attempt: int, spent_usd: float, reason: str
    ) -> None:
        await self._ports.settle_budget(task_id, attempt, spent_usd, reason)

    @activity.defn
    async def release_budget(self, task_id: str, attempt: int) -> None:
        await self._ports.release_budget(task_id, attempt)

    # -- tool / model step -------------------------------------------------
    @activity.defn
    async def run_tool_step(
        self,
        task_id: str,
        attempt: int,
        tool: str,
        payload: dict[str, Any],
        idempotency_key: str,
        checkpoint_key: str,
    ) -> dict[str, Any]:
        res = await self._ports.run_tool_step(
            task_id, attempt, tool, payload, idempotency_key, checkpoint_key
        )
        return _dump(res)

    # -- approval / outbox / external effect -------------------------------
    @activity.defn
    async def verify_approval_permission(
        self, proposal_id: str, expected_digest: str, expected_version: int
    ) -> dict[str, Any]:
        res = await self._ports.verify_approval_permission(
            proposal_id, expected_digest, expected_version
        )
        return _dump(res)

    @activity.defn
    async def claim_outbox_operation(
        self, proposal_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        res = await self._ports.claim_outbox_operation(proposal_id, idempotency_key)
        return _dump(res)

    @activity.defn
    async def execute_external_effect(
        self, task_id: str, attempt: int, proposal_id: str, operation_id: str
    ) -> dict[str, Any]:
        res = await self._ports.execute_external_effect(
            task_id, attempt, proposal_id, operation_id
        )
        return _dump(res)

    @activity.defn
    async def record_outbox_result(
        self, proposal_id: str, external_state: str, detail: dict[str, Any]
    ) -> None:
        await self._ports.record_outbox_result(proposal_id, external_state, detail)

    @activity.defn
    async def append_audit(
        self, actor: str, action: str, target: str, details: dict[str, Any]
    ) -> None:
        await self._ports.append_audit(actor, action, target, details)


# Re-exported for type hints / tests.
__all__ = ["Activities", "CorePorts", "ApprovalVerdict", "OutboxClaim",
           "ReserveResult", "StepProposal", "ToolResult"]
