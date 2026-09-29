"""The durable task workflow.

Workflow ID convention (frozen contract section 10): ``fy-task:<task_id>``.

Responsibilities that live *here* (deterministic orchestration only):
- task queued -> running -> stage loop -> completed/failed/cancelled;
- bounded step / retry / budget / deadline enforcement;
- waiting for approval and for external input;
- ``cancel`` and ``approval`` signals; a ``status`` query.

Responsibilities that are NEVER here (all in activities / ports):
- any model, HTTP, database or object-storage call;
- deciding whether an approval is authorized — that always goes through the
  :class:`Activities.verify_approval_permission` activity which re-checks the
  approval database. A signal alone is never trusted.

LangGraph checkpoint key: ``task:<task_id>:attempt:<n>:stage:<stage>``. On
replay, completed activities are not re-executed, so resuming neither repeats
external side effects nor opens new budget.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import Activities  # noqa: F401  (registered by Worker)
    from .models import (
        ApprovalVerdict,
        OutboxClaim,
        ReserveResult,
        Stage,
        StepProposal,
        TaskWorkflowInput,
        TaskWorkflowResult,
        ToolResult,
        checkpoint_key,
    )

# Activity timeouts. All external work is bounded; nothing waits forever.
_ACT_TASK_TIMEOUT = timedelta(seconds=15)
_ACT_PLAN_TIMEOUT = timedelta(seconds=20)
_ACT_TOOL_TIMEOUT = timedelta(seconds=60)
_ACT_APPROVAL_TIMEOUT = timedelta(seconds=15)


def _now() -> datetime:
    return workflow.now()


def _await_pred(pred) -> Any:
    """Wait on a predicate. The SDK exposes this as ``wait_condition``."""
    return workflow.wait_condition(pred)


@workflow.defn(name="fy-task-workflow", sandboxed=True)
class TaskWorkflow:
    def __init__(self) -> None:
        # Signal latches (set by signal handlers, awaited by the run loop).
        self._cancel_requested = False
        self._cancel_reason = ""
        self._approval_seen = False
        self._pending_approval: dict[str, Any] | None = None
        self._approval_processed = False
        self._input_seen = False
        self._pending_input: dict[str, Any] = {}
        # Mutable run state surfaced through the query.
        self._status: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    @workflow.signal
    async def cancel(self, reason: str = "") -> None:
        self._cancel_requested = True
        self._cancel_reason = reason or "cancelled_by_signal"

    @workflow.signal
    async def approval(self, proposal_id: str, digest: str, decision: str = "approve") -> None:
        # Store the signal only to wake the workflow. Authorization is decided
        # later by the verify_approval_permission activity against the DB.
        self._pending_approval = {
            "proposal_id": proposal_id,
            "digest": digest,
            "decision": decision,
        }
        self._approval_seen = True

    @workflow.signal
    async def provide_input(self, payload: dict[str, Any]) -> None:
        self._pending_input = dict(payload or {})
        self._input_seen = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return dict(self._status)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def _set_status(self, **fields: Any) -> None:
        self._status.update(fields)

    async def _act(self, name: str, *args: Any) -> Any:
        return await workflow.execute_activity(
            name,
            args=list(args),
            start_to_close_timeout=_ACT_TASK_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=1),  # lifecycle writes are idempotent; no blind retry
        )

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    @workflow.run
    async def run(self, input_dict: dict[str, Any]) -> dict[str, Any]:
        inp = TaskWorkflowInput.model_validate(input_dict)
        info = workflow.info()
        attempt = int(info.attempt or 1)
        task_id = inp.task_id
        limits = inp.limits
        deadline = datetime.fromisoformat(limits.deadline)
        if deadline.tzinfo is None:
            # Should have been normalized to UTC upstream; guard determinism.
            raise ValueError("deadline must be timezone-aware UTC")

        stage = Stage.queued.value
        ck = checkpoint_key(task_id, attempt, stage)
        self._set_status(
            task_id=task_id, stage=stage, attempt=attempt, steps=0,
            state="running", cancel_requested=False,
        )

        await self._act("task_record_started", task_id, attempt, ck)
        await self._act("append_audit", "workflow", "task.started", task_id,
                        {"mode": inp.mode, "domain": inp.domain})

        history: list[dict[str, Any]] = []
        steps = 0
        spent = 0.0

        try:
            while steps < limits.max_steps:
                if self._cancel_requested:
                    return await self._do_cancel(task_id, attempt, steps, spent, ck)

                remaining = (deadline - _now()).total_seconds()
                if remaining <= 0:
                    await self._act("release_budget", task_id, attempt)
                    await self._act("task_fail", task_id, attempt,
                                    {"code": "deadline_exceeded", "message": "Task deadline passed"})
                    return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                        code="deadline_exceeded")

                stage = Stage.execution.value
                ck = checkpoint_key(task_id, attempt, stage)
                await self._act("task_update_stage", task_id, attempt, stage, ck)
                self._set_status(stage=stage, last_checkpoint_key=ck)

                proposal_dict = await workflow.execute_activity(
                    "plan_next_step",
                    args=[task_id, attempt, stage, history],
                    start_to_close_timeout=_ACT_PLAN_TIMEOUT,
                    retry_policy=RetryPolicy(maximum_attempts=limits.max_retries + 1),
                )
                proposal = StepProposal.model_validate(proposal_dict)

                if proposal.kind == "finish":
                    break
                if proposal.kind == "fail":
                    await self._act("release_budget", task_id, attempt)
                    await self._act("task_fail", task_id, attempt,
                                    {"code": proposal.payload.get("code", "planned_failure"),
                                     "message": proposal.reason or "planned failure"})
                    return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                        code=proposal.payload.get("code", "planned_failure"))

                if proposal.kind == "wait_input":
                    got = await self._wait_input(task_id, attempt, deadline)
                    if got is None:
                        return await self._do_timeout_or_cancel(task_id, attempt, steps, spent, ck)
                    history.append({"type": "input", "payload": got})
                    continue

                if proposal.kind == "external_side_effect":
                    side = await self._run_side_effect(inp, proposal, attempt, deadline, ck)
                    if side == "cancelled":
                        return await self._do_cancel(task_id, attempt, steps, spent, ck)
                    if side == "timeout":
                        await self._act("release_budget", task_id, attempt)
                        return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                            code="deadline_exceeded")
                    if side == "reconcile":
                        # Lost remote response: stop and await reconciliation.
                        return self._finish(task_id, "awaiting_reconciliation",
                                            Stage.reconciling, steps, spent,
                                            code="external_unknown")
                    if side == "rejected":
                        await self._act("release_budget", task_id, attempt)
                        return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                            code="side_effect_rejected")
                    # ok -> continue loop

                elif proposal.kind == "tool_call":
                    done = await self._run_tool(inp, proposal, attempt, ck)
                    if done == "cancelled":
                        return await self._do_cancel(task_id, attempt, steps, spent, ck)
                    if done == "reconcile":
                        return self._finish(task_id, "awaiting_reconciliation",
                                            Stage.reconciling, steps, spent,
                                            code="tool_unknown")
                    if done == "budget_exceeded":
                        return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                            code="budget_exceeded")
                    if done == "error":
                        return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                            code="tool_error")
                    spent += done if isinstance(done, float) else 0.0
                    history.append({"type": "tool", "tool": proposal.tool, "ok": True})

                steps += 1
                self._set_status(steps=steps)

            # Loop exit conditions.
            if steps >= limits.max_steps and not self._cancel_requested:
                await self._act("release_budget", task_id, attempt)
                await self._act("task_fail", task_id, attempt,
                                {"code": "max_steps_reached", "message": "Step limit reached"})
                return self._finish(task_id, "failed", Stage.failed, steps, spent,
                                    code="max_steps_reached")

            await self._act("task_complete", task_id, attempt,
                             {"history_tail": history[-5:]})
            return self._finish(task_id, "completed", Stage.completed, steps, spent)
        except asyncio.CancelledError:
            # Native Temporal cancellation: propagate as a cancel result.
            await self._safe_release(task_id, attempt)
            raise
        finally:
            pass

    # ------------------------------------------------------------------
    # Step runners
    # ------------------------------------------------------------------
    async def _run_tool(self, inp: TaskWorkflowInput, proposal: StepProposal,
                        attempt: int, ck: str) -> str | float:
        """Returns spent_usd on ok, or a sentinel string."""
        task_id = inp.task_id
        est = float(proposal.payload.get("estimated_usd", 0.0))
        reserve = ReserveResult.model_validate(await self._act(
            "reserve_budget", task_id, attempt, est, proposal.tool or "tool"))
        if not reserve.reserved:
            return "budget_exceeded"

        idem = proposal.idempotency_key or f"fy:{task_id}:step:{attempt}:{proposal.tool}"
        res = ToolResult.model_validate(await workflow.execute_activity(
            "run_tool_step",
            args=[task_id, attempt, proposal.tool, proposal.payload, idem, ck],
            start_to_close_timeout=_ACT_TOOL_TIMEOUT,
            retry_policy=RetryPolicy(
                maximum_attempts=inp.limits.max_retries + 1,
                non_retryable_error_types=["BusinessError", "ValidationError"],
            ),
        ))
        if self._cancel_requested:
            return "cancelled"
        if res.status == "ok":
            await self._act("settle_budget", task_id, attempt, res.spent_usd,
                            proposal.tool or "tool")
            return res.spent_usd
        if res.status == "unknown":
            # Response lost: do NOT blind retry. Record and stop for reconciliation.
            await self._act("record_outbox_result", f"tool:{idem}", "unknown",
                            {"tool": proposal.tool})
            return "reconcile"
        if res.status == "budget_exceeded":
            return "budget_exceeded"
        return "error"

    async def _run_side_effect(self, inp: TaskWorkflowInput, proposal: StepProposal,
                               attempt: int, deadline: datetime, ck: str) -> str:
        """Returns ok / cancelled / timeout / reconcile / rejected."""
        task_id = inp.task_id
        stage = Stage.awaiting_approval.value
        ck = checkpoint_key(task_id, attempt, stage)
        await self._act("task_update_stage", task_id, attempt, stage, ck)
        self._set_status(stage=stage, last_checkpoint_key=ck, state="awaiting_approval")

        # Reset the approval latch; we only react to signals sent from now on.
        self._approval_seen = False
        self._pending_approval = None

        expires_at = (
            datetime.fromisoformat(proposal.proposal_expires_at)
            if proposal.proposal_expires_at else deadline
        )

        woke = await self._race_approval(expires_at)
        if self._cancel_requested:
            return "cancelled"
        if not woke:
            # Timed out waiting for the (expired) approval.
            await self._act("record_outbox_result", proposal.proposal_id, "expired", {})
            return "rejected"

        # CRITICAL: re-verify against the approval database. The signal content
        # is never trusted for authorization.
        verdict = ApprovalVerdict.model_validate(await workflow.execute_activity(
            "verify_approval_permission",
            args=[proposal.proposal_id, proposal.expected_digest, proposal.expected_version],
            start_to_close_timeout=_ACT_APPROVAL_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=1),
        ))
        if not verdict.allowed:
            await self._act("record_outbox_result", proposal.proposal_id, verdict.code,
                            {"digest": proposal.expected_digest})
            return "rejected"

        # Single-consumption outbox claim. Replays / duplicate signals hit the
        # already_done branch and never execute twice.
        idem = f"fy:{proposal.proposal_id}:{(proposal.expected_digest or '')[:12]}"
        claim = OutboxClaim.model_validate(await self._act(
            "claim_outbox_operation", proposal.proposal_id, idem))
        if claim.code == "already_done":
            self._approval_processed = True
            return "ok"
        if not claim.claimed:
            return "rejected"

        res = ToolResult.model_validate(await workflow.execute_activity(
            "execute_external_effect",
            args=[task_id, attempt, proposal.proposal_id, claim.operation_id],
            start_to_close_timeout=_ACT_TOOL_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=1),  # never blind-retry irreversible effects
        ))
        self._approval_processed = True
        if res.status == "unknown":
            await self._act("record_outbox_result", proposal.proposal_id, "unknown", {})
            return "reconcile"
        if res.status == "error":
            await self._act("record_outbox_result", proposal.proposal_id, "failed", {})
            return "rejected"
        await self._act("record_outbox_result", proposal.proposal_id, "executed", {})
        return "ok"

    async def _wait_input(self, task_id: str, attempt: int, deadline: datetime) -> dict[str, Any] | None:
        stage = Stage.awaiting_input.value
        ck = checkpoint_key(task_id, attempt, stage)
        await self._act("task_update_stage", task_id, attempt, stage, ck)
        self._set_status(stage=stage, last_checkpoint_key=ck, state="awaiting_input")
        self._input_seen = False
        self._pending_input = {}
        remaining = (deadline - _now()).total_seconds()
        if remaining <= 0:
            return None
        timer_task = asyncio.create_task(workflow.sleep(timedelta(seconds=remaining)))
        waiter_task = asyncio.create_task(_await_pred(lambda: self._input_seen or self._cancel_requested))
        done, pending = await workflow.wait([timer_task, waiter_task], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
        for t in pending:  # never leave a half-run timer/signal task blocking the workflow
            t.cancel()
        if self._cancel_requested or not self._input_seen:
            return None
        return dict(self._pending_input)

    async def _race_approval(self, expires_at: datetime) -> bool:
        remaining = (expires_at - _now()).total_seconds()
        if remaining <= 0:
            return False
        timer_task = asyncio.create_task(workflow.sleep(timedelta(seconds=remaining)))
        waiter_task = asyncio.create_task(_await_pred(lambda: self._approval_seen or self._cancel_requested))
        done, pending = await workflow.wait([timer_task, waiter_task], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
        for t in pending:
            t.cancel()
        return self._approval_seen and self._pending_approval is not None

    # ------------------------------------------------------------------
    # Terminal helpers
    # ------------------------------------------------------------------
    async def _safe_release(self, task_id: str, attempt: int) -> None:
        try:
            await self._act("release_budget", task_id, attempt)
        except Exception:  # pragma: no cover - best effort during cancel
            workflow.logger.warning("budget_release_failed_on_cancel",
                                    extra={"task_id": task_id})

    async def _do_cancel(self, task_id: str, attempt: int, steps: int,
                         spent: float, ck: str) -> dict[str, Any]:
        await self._safe_release(task_id, attempt)
        await self._act("task_cancel", task_id, attempt, self._cancel_reason)
        self._set_status(state="cancelled")
        return self._finish(task_id, "cancelled", Stage.cancelled, steps, spent,
                            code="cancelled", message=self._cancel_reason)

    async def _do_timeout_or_cancel(self, task_id: str, attempt: int, steps: int,
                                     spent: float, ck: str) -> dict[str, Any]:
        if self._cancel_requested:
            return await self._do_cancel(task_id, attempt, steps, spent, ck)
        await self._act("release_budget", task_id, attempt)
        return self._finish(task_id, "failed", Stage.failed, steps, spent,
                            code="deadline_exceeded")

    def _finish(self, task_id: str, status: str, stage: Stage, steps: int,
                spent: float, code: str = "", message: str = "") -> dict[str, Any]:
        result = TaskWorkflowResult(
            task_id=task_id,
            status=status,  # type: ignore[arg-type]
            stage=stage.value,
            steps=steps,
            spent_usd=spent,
            last_checkpoint_key=self._status.get("last_checkpoint_key", ""),
            failure={"code": code, "message": message} if code else None,
        )
        self._set_status(state=status, stage=stage.value, steps=steps)
        return result.model_dump()
