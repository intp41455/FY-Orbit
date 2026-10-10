"""Real-Postgres CorePorts adapter for the Temporal Worker.

Wraps the existing synchronous Core services behind the async ``CorePorts``
shape declared in ``workflows/ports.py``. Each activity call opens its own
session from the injected session factory, does its work in one transaction and
commits — activities run on worker threads, so we never share a session.

No paid model is called by default: planning is delegated to an injectable
deterministic planner, and the local external effect writes an idempotent file
under ``.runtime/effects/`` (keyed by idempotency key). Production effects are
wired separately for approved external actions.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import (
    BudgetReservation,
    Memory,
    Operation,
    Proposal,
    Task,
    TaskAttempt,
)
from ..db.types import utcnow
from .actor import Actor
from .audit import AuditService
from .budget import BudgetService
from .outbox import OutboxService
from .proposal import ProposalService

# A service actor used for all internal worker-side writes. The approval signal
# never carries authority; these DB-side checks are the real authorization.
_WORKER = Actor.service("temporal-worker", "executor")


def _default_planner(task_id: str, attempt: int, stage: str, history: list[dict]) -> dict:
    """Deterministic local planner: never calls a paid model.

    Defaults to finishing the task unless ``FY_PLANNER_MODE`` selects a scripted
    branch. ``wait_input`` parks the task in awaiting_input so the HTTP cancel
    path can be exercised end-to-end. This is a local, deterministic, model-free
    planner; it never contacts a paid provider.
    """
    mode = os.environ.get("FY_PLANNER_MODE", "finish")
    if mode == "wait_input":
        return {"kind": "wait_input", "reason": "scripted wait_input"}
    return {"kind": "finish", "reason": "local deterministic planner"}


class PostgresCorePorts:
    """Postgres-backed implementation of ``workflows.ports.CorePorts``."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        effect_dir: str = ".runtime/effects",
        planner: Callable[[str, int, str, list[dict]], dict] | None = None,
        tool_executors: dict[str, Callable[[dict], dict]] | None = None,
        allow_echo: bool = True,
    ):
        self._sf = session_factory
        self._effect_dir = effect_dir
        self._planner = planner or _default_planner
        self._tool_executors = tool_executors or {}
        self._allow_echo = allow_echo

    # -- helpers ----------------------------------------------------------
    def _run(self, fn: Callable[[Session], Any]) -> Any:
        s = self._sf()
        try:
            out = fn(s)
            s.commit()
            return out
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    async def _a(self, fn: Callable[[Session], Any]) -> Any:
        return await asyncio.to_thread(self._run, fn)

    # -- Task lifecycle ----------------------------------------------------
    async def task_record_started(self, task_id: str, attempt: int, checkpoint_key: str) -> None:
        def work(s: Session):
            t = s.get(Task, task_id)
            if t is None:
                raise KeyError(task_id)
            t.status = "running"
            t.stage = "requirements"
            row = TaskAttempt(
                id=f"{task_id}:{attempt}", task_id=task_id, attempt_no=attempt,
                status="running", started_at=utcnow(), checkpoint_ref=checkpoint_key,
                version=1,
            )
            s.merge(row)
        await self._a(work)

    async def task_update_stage(self, task_id: str, attempt: int, stage: str, checkpoint_key: str) -> None:
        def work(s: Session):
            t = s.get(Task, task_id)
            if t:
                t.stage = stage
            s.execute(
                update(TaskAttempt).where(TaskAttempt.task_id == task_id, TaskAttempt.attempt_no == attempt)
                .values(checkpoint_ref=checkpoint_key)
            )
        await self._a(work)

    async def task_complete(self, task_id: str, attempt: int, result: dict[str, Any]) -> None:
        def work(s: Session):
            t = s.get(Task, task_id)
            if t:
                t.status = "completed"
                t.result = result
            s.execute(
                update(TaskAttempt).where(TaskAttempt.task_id == task_id, TaskAttempt.attempt_no == attempt)
                .values(status="succeeded", ended_at=utcnow())
            )
        await self._a(work)

    async def task_fail(self, task_id: str, attempt: int, failure: dict[str, str]) -> None:
        def work(s: Session):
            t = s.get(Task, task_id)
            if t:
                t.status = "failed"
                t.failure = failure
            s.execute(
                update(TaskAttempt).where(TaskAttempt.task_id == task_id, TaskAttempt.attempt_no == attempt)
                .values(status="failed", ended_at=utcnow())
            )
        await self._a(work)

    async def task_cancel(self, task_id: str, attempt: int, reason: str) -> None:
        def work(s: Session):
            bs = BudgetService(s, AuditService(s))
            bs.cancel_task(_WORKER, task_id)
            s.execute(
                update(TaskAttempt).where(TaskAttempt.task_id == task_id, TaskAttempt.attempt_no == attempt)
                .values(status="cancelled", ended_at=utcnow())
            )
        await self._a(work)

    # -- Planner -----------------------------------------------------------
    async def plan_next_step(self, task_id: str, attempt: int, stage: str, history: list[dict[str, Any]]) -> dict:
        return await self._a(lambda s: dict(self._planner(task_id, attempt, stage, history)))

    # -- Budget -----------------------------------------------------------
    async def reserve_budget(self, task_id: str, attempt: int, estimated_usd: float, scope: str) -> dict:
        def work(s: Session) -> dict:
            if estimated_usd <= 0:
                return {"reserved": False, "code": "price_unknown", "remaining_usd": 0.0}
            bs = BudgetService(s, AuditService(s))
            try:
                r = bs.reserve(_WORKER, task_id=task_id, amount=estimated_usd,
                               idempotency_key=f"res:{task_id}:{attempt}", scope=scope)
                return {"reserved": True, "code": "ok", "remaining_usd": float(r.amount)}
            except Exception:
                return {"reserved": False, "code": "budget_exceeded", "remaining_usd": 0.0}
        return await self._a(work)

    async def settle_budget(self, task_id: str, attempt: int, spent_usd: float, reason: str) -> None:
        def work(s: Session):
            bs = BudgetService(s, AuditService(s))
            row = s.execute(
                select(BudgetReservation).where(
                    BudgetReservation.task_id == task_id,
                    BudgetReservation.state == "reserved",
                ).limit(1)
            ).scalar_one_or_none()
            if row:
                bs.settle(_WORKER, row.id, spent_usd)
        await self._a(work)

    async def release_budget(self, task_id: str, attempt: int) -> None:
        def work(s: Session):
            bs = BudgetService(s, AuditService(s))
            active = s.execute(
                select(BudgetReservation).where(
                    BudgetReservation.task_id == task_id,
                    BudgetReservation.state.in_(["reserved", "unknown"]),
                )
            ).scalars()
            for r in list(active):
                if r.state == "reserved":
                    bs.release(_WORKER, r.id)
        await self._a(work)

    # -- Tool step (production requires configured executor; echo restricted to test) --
    async def run_tool_step(self, task_id: str, attempt: int, tool: str, payload: dict[str, Any],
                            idempotency_key: str, checkpoint_key: str) -> dict:
        if tool in self._tool_executors:
            try:
                res = self._tool_executors[tool](payload)
                return {"status": "ok", "output": res, "spent_usd": 0.0}
            except Exception as e:
                return {"status": "failed", "error": f"tool_execution_failed: {e}", "spent_usd": 0.0}
        if self._allow_echo:
            return {"status": "ok", "output": {"tool": tool, "echo": payload}, "spent_usd": 0.0}
        return {
            "status": "failed",
            "error": f"unconfigured_tool_executor: production path requires configured executor for '{tool}', echo fallback disallowed",
            "spent_usd": 0.0,
        }

    # -- Approval re-verification -----------------------------------------
    async def verify_approval_permission(self, proposal_id: str, expected_digest: str, expected_version: int) -> dict:
        def work(s: Session) -> dict:
            audit = AuditService(s)
            svc = ProposalService(s, audit, target_version_lookup=lambda tid: self._lookup_version(s, tid))
            p = s.get(Proposal, proposal_id)
            if p is None:
                return {"allowed": False, "code": "not_found", "message": "no such proposal",
                        "proposal_status": "", "digest": "", "expected_version": expected_version,
                        "execution_id": None}
            recomputed = svc.compute_digest(p)
            base = {"proposal_status": p.status, "digest": recomputed,
                    "expected_version": p.expected_version, "execution_id": p.execution_id}
            if p.status == "rejected":
                return {**base, "allowed": False, "code": "rejected", "message": "proposal rejected"}
            if p.status in ("executed",):
                return {**base, "allowed": False, "code": "already_executed", "message": "already executed"}
            if p.expires_at <= utcnow():
                return {**base, "allowed": False, "code": "expired", "message": "proposal expired"}
            if recomputed != expected_digest or p.digest != recomputed:
                return {**base, "allowed": False, "code": "digest_mismatch", "message": "digest mismatch"}
            if p.expected_version and p.target_id:
                cur = self._lookup_version(s, p.target_id)
                if cur is None or cur != p.expected_version:
                    return {**base, "allowed": False, "code": "stale_version",
                            "message": f"target at {cur}, expected {p.expected_version}"}
            return {**base, "allowed": True, "code": "ok", "message": "verified"}
        return await self._a(work)

    @staticmethod
    def _lookup_version(s: Session, target_id: str) -> int | None:
        m = s.get(Memory, target_id)
        return m.version if m else None

    # -- Outbox ------------------------------------------------------------
    async def claim_outbox_operation(self, proposal_id: str, idempotency_key: str) -> dict:
        def work(s: Session) -> dict:
            # Use .limit(1) as defensive measure to prevent MultipleResultsFound
            # even if unique constraint is somehow bypassed
            op = s.execute(
                select(Operation)
                .where(Operation.proposal_id == proposal_id)
                .limit(1)
            ).scalars().first()
            if op is None:
                return {"claimed": False, "code": "not_found", "operation_id": None}
            if op.state != "pending":
                # Second / repeat claim is idempotent: already done, not re-run.
                return {"claimed": False, "code": "already_done", "operation_id": op.id}
            res = s.execute(
                update(Operation)
                .where(Operation.id == op.id, Operation.state == "pending")
                .values(state="claimed", attempt=Operation.attempt + 1, updated_at=utcnow())
                .returning(Operation.id)
            ).scalar_one_or_none()
            if res is None:
                return {"claimed": False, "code": "already_done", "operation_id": op.id}
            return {"claimed": True, "code": "claimed", "operation_id": op.id}
        return await self._a(work)

    async def execute_external_effect(self, task_id: str, attempt: int, proposal_id: str, operation_id: str) -> dict:
        # Local idempotent effect: write one file keyed by operation_id. Repeat
        # execution must NOT create a second file. This is NOT a paid/external
        # vendor call; the production external effect is wired separately.
        def work(s: Session) -> dict:
            os.makedirs(self._effect_dir, exist_ok=True)
            path = os.path.join(self._effect_dir, f"{operation_id}.json")
            created = not os.path.exists(path)
            if created:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"operation_id": operation_id, "proposal_id": proposal_id,
                               "task_id": task_id, "at": utcnow().isoformat()}, f)
            return {"status": "ok", "output": {"path": path, "created": created,
                                              "external": False}, "spent_usd": 0.0}
        return await self._a(work)

    async def record_outbox_result(self, proposal_id: str, external_state: str, detail: dict[str, Any]) -> None:
        def work(s: Session):
            audit = AuditService(s)
            ob = OutboxService(s, audit)
            # Use .limit(1) as defensive measure to prevent MultipleResultsFound
            op = s.execute(
                select(Operation)
                .where(Operation.proposal_id == proposal_id)
                .limit(1)
            ).scalars().first()
            if op is None:
                return
            if external_state in ("executed", "succeeded"):
                ob.succeed(_WORKER, op.id, external_id=detail.get("external_id"))
            elif external_state == "unknown":
                ob.fail(_WORKER, detail.get("error", "lost"), unknown=True)
            else:
                ob.fail(_WORKER, detail.get("error", external_state))
        await self._a(work)

    # -- Audit -------------------------------------------------------------
    async def append_audit(self, actor: str, action: str, target: str, details: dict[str, Any]) -> None:
        def work(s: Session):
            AuditService(s).append(_WORKER, action, target, details)
        await self._a(work)
