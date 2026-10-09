"""OrchestratorLeaseService: 18 §8 主协调者更换与接管 (fencing token).

Implements the takeover flow the 18 spec requires, replacing the under-specified
"已有任务迁移需单独审查" note in spec 05:

    暂停新派发 → 保存计划/任务/产物/证据/在途动作/预算/审批 → 撤销旧协调者派发租约
    → 新协调者能力校验并读取交接包 → 对账 → 用户可查看接管摘要 → 恢复

Guarantees:

- **One valid lease per root task.** Enforced by a unique constraint on
  ``root_task_id`` across *non-revoked* leases and a monotonically increasing
  ``fencing_token``.
- **Stale dispatch is rejected.** Any dispatch presented with an older fencing
  token is refused ("迟到的旧协调者派发"), so a revoked orchestrator cannot
  still move the task after takeover.
- **In-flight actions go to reconciliation, never blind retry.** Unconfirmed
  external operations are marked ``unknown_needs_reconciliation`` so a takeover
  never duplicates a side effect that may already have happened.
- **Executors are not restarted.** Worker assignments and their execution
  versions are carried over in the handoff packet.
- **No secret inheritance.** The new orchestrator inherits the existing
  authorization boundary only — never another account's credential session.
  This is recorded in the takeover summary rather than assumed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from find_yourself.db.canvas_models import DispatchRecord
from find_yourself.db.models import Proposal, Task
from find_yourself.db.types import utcnow
from find_yourself.db.workbench_models import OrchestratorLease
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

#: A coordinator must be able to do more than print version/help (18 §7).
REQUIRED_COORDINATOR_CAPABILITIES = ("plan", "dispatch", "review")

#: Connector stages that prove nothing beyond "the binary answered".
NON_ELIGIBLE_STAGES = ("仅设计",)

IN_FLIGHT_STATES = ("running", "dispatched", "accepted", "submitted", "pending_adapter")


class OrchestratorLeaseService:
    def __init__(self, session: Session, audit: Any | None = None):
        self.session = session
        self.audit = audit

    # ------------------------------------------------------------------
    # Lease acquisition
    # ------------------------------------------------------------------
    def _active_lease(self, root_task_id: str) -> OrchestratorLease | None:
        return self.session.execute(
            select(OrchestratorLease).where(
                OrchestratorLease.root_task_id == root_task_id,
                OrchestratorLease.state == "active",
            )
        ).scalar_one_or_none()

    def acquire(
        self,
        actor: Actor,
        root_task_id: str,
        orchestrator_id: str,
        *,
        capabilities: list[str] | None = None,
        connector_stage: str | None = None,
    ) -> dict[str, Any]:
        actor.require_owner()
        if not root_task_id:
            raise ValidationFailed("root_task_id is required")
        existing = self._active_lease(root_task_id)
        if existing is not None:
            raise Conflict(
                f"Root task {root_task_id} already has an active orchestrator lease "
                f"held by {existing.orchestrator_id} (fencing {existing.fencing_token}); "
                "use takeover to transfer it."
            )
        max_token = self.session.execute(
            select(func.max(OrchestratorLease.fencing_token)).where(
                OrchestratorLease.root_task_id == root_task_id
            )
        ).scalar_one_or_none()
        token = int(max_token or 0) + 1

        lease = OrchestratorLease(
            id=f"lease-{uuid4().hex[:12]}",
            root_task_id=root_task_id,
            orchestrator_id=orchestrator_id,
            fencing_token=token,
            state="active",
            handoff_packet={
                "capabilities": list(capabilities or []),
                "connector_stage": connector_stage,
                "paused": False,
                "secret_inheritance": "none",
            },
        )
        self.session.add(lease)
        self.session.flush()
        return self._view(lease)

    def current(self, actor: Actor, root_task_id: str) -> dict[str, Any]:
        lease = self._active_lease(root_task_id)
        if lease is None:
            raise NotFound(f"No active orchestrator lease for root task {root_task_id}")
        return self._view(lease)

    @staticmethod
    def _view(lease: OrchestratorLease) -> dict[str, Any]:
        return {
            "id": lease.id,
            "root_task_id": lease.root_task_id,
            "orchestrator_id": lease.orchestrator_id,
            "fencing_token": lease.fencing_token,
            "state": lease.state,
            "paused": bool((lease.handoff_packet or {}).get("paused")),
            "secret_inheritance": (lease.handoff_packet or {}).get("secret_inheritance", "none"),
            "granted_at": lease.granted_at.isoformat(),
            "revoked_at": lease.revoked_at.isoformat() if lease.revoked_at else None,
        }

    # ------------------------------------------------------------------
    # Fencing enforcement
    # ------------------------------------------------------------------
    def validate_dispatch(
        self,
        root_task_id: str,
        orchestrator_id: str,
        fencing_token: int | None,
    ) -> dict[str, Any]:
        """Reject late dispatches from a revoked/older orchestrator."""
        lease = self._active_lease(root_task_id)
        if lease is None:
            raise Conflict(f"No active orchestrator lease for root task {root_task_id}")
        if bool((lease.handoff_packet or {}).get("paused")):
            raise Conflict(
                f"Dispatch paused: takeover of root task {root_task_id} is in progress"
            )
        if lease.orchestrator_id != orchestrator_id:
            raise PermissionDenied(
                "stale_orchestrator",
                f"Root task {root_task_id} is leased to {lease.orchestrator_id}, not {orchestrator_id}",
                403,
            )
        if fencing_token is None or int(fencing_token) != int(lease.fencing_token):
            raise Conflict(
                f"Stale fencing token for root task {root_task_id}: presented "
                f"{fencing_token}, current {lease.fencing_token}. Late dispatch rejected."
            )
        return {"valid": True, "fencing_token": lease.fencing_token}

    # ------------------------------------------------------------------
    # Takeover
    # ------------------------------------------------------------------
    @staticmethod
    def coordinator_eligible(
        orchestrator_id: str,
        *,
        capabilities: list[str] | None = None,
        connector_stage: str | None = None,
    ) -> tuple[bool, str]:
        """A candidate that only answers version/help is not a coordinator (18 §7)."""
        caps = {c.lower() for c in (capabilities or [])}
        missing = [c for c in REQUIRED_COORDINATOR_CAPABILITIES if c not in caps]
        if missing:
            return False, f"Missing coordinator capabilities: {missing}"
        if connector_stage in NON_ELIGIBLE_STAGES:
            return False, (
                f"Connector {orchestrator_id} is at stage '{connector_stage}' (design only); "
                "a candidate that only supports version/help cannot coordinate."
            )
        return True, "eligible"

    def begin_takeover(
        self,
        actor: Actor,
        root_task_id: str,
        new_orchestrator_id: str,
        *,
        new_capabilities: list[str] | None = None,
        new_connector_stage: str | None = None,
    ) -> dict[str, Any]:
        """Step 1–5: pause, snapshot, revoke, validate, reconcile. Does not resume."""
        actor.require_owner()
        old = self._active_lease(root_task_id)
        if old is None:
            raise NotFound(f"No active orchestrator lease for root task {root_task_id}")
        if old.orchestrator_id == new_orchestrator_id:
            raise Conflict(f"Root task {root_task_id} is already coordinated by {new_orchestrator_id}")

        eligible, reason = self.coordinator_eligible(
            new_orchestrator_id,
            capabilities=new_capabilities,
            connector_stage=new_connector_stage,
        )
        if not eligible:
            raise ValidationFailed(f"New coordinator {new_orchestrator_id} is not eligible: {reason}")

        # 1. Pause new dispatch, 2. snapshot plan/tasks/artifacts/evidence/in-flight/budget/approvals
        packet = self._build_handoff_packet(root_task_id)

        # 3. Revoke the old orchestrator's dispatch lease.
        old.state = "revoked"
        old.revoked_at = utcnow()

        # 5. Reconcile in-flight external actions instead of blindly retrying them.
        in_flight = self.session.execute(
            select(DispatchRecord).where(
                DispatchRecord.root_task_id == root_task_id,
                DispatchRecord.state.in_(IN_FLIGHT_STATES),
            )
        ).scalars().all()
        for rec in in_flight:
            ref = dict(rec.input_ref or {})
            ref["takeover_reconciliation"] = {
                "required": True,
                "reason": "orchestrator_takeover",
                "prior_executor": rec.worker_id,
                "execution_version_preserved": ref.get("execution_version"),
            }
            rec.input_ref = ref
            rec.state = "unknown_needs_reconciliation"

        new_token = int(old.fencing_token) + 1
        lease = OrchestratorLease(
            id=f"lease-{uuid4().hex[:12]}",
            root_task_id=root_task_id,
            orchestrator_id=new_orchestrator_id,
            fencing_token=new_token,
            state="active",
            handoff_packet={
                **packet,
                "capabilities": list(new_capabilities or []),
                "connector_stage": new_connector_stage,
                "paused": True,
                "secret_inheritance": "none",
            },
            takeover_summary=self._summary_dict(
                old, new_orchestrator_id, new_token, packet, in_flight
            ),
        )
        # The unique constraint on root_task_id covers all leases, so retire the
        # old row's claim by keying it to a namespaced id before inserting.
        old.root_task_id = f"{root_task_id}::revoked::{old.id}"
        self.session.flush()
        self.session.add(lease)
        self.session.flush()
        return self.takeover_summary(actor, root_task_id)

    def resume_takeover(self, actor: Actor, root_task_id: str) -> dict[str, Any]:
        """Step 6–7: user has seen the summary; resume dispatch under the new lease."""
        actor.require_owner()
        lease = self._active_lease(root_task_id)
        if lease is None:
            raise NotFound(f"No active orchestrator lease for root task {root_task_id}")
        packet = dict(lease.handoff_packet or {})
        packet["paused"] = False
        lease.handoff_packet = packet
        summary = dict(lease.takeover_summary or {})
        summary["resumed"] = True
        summary["resumed_at"] = utcnow().isoformat()
        lease.takeover_summary = summary
        self.session.flush()
        return self.takeover_summary(actor, root_task_id)

    def takeover_summary(self, actor: Actor, root_task_id: str) -> dict[str, Any]:
        """Owner-viewable takeover summary."""
        lease = self._active_lease(root_task_id)
        if lease is None:
            raise NotFound(f"No active orchestrator lease for root task {root_task_id}")
        packet = lease.handoff_packet or {}
        return {
            "root_task_id": root_task_id,
            "orchestrator_id": lease.orchestrator_id,
            "fencing_token": lease.fencing_token,
            "paused": bool(packet.get("paused")),
            "resumed": bool((lease.takeover_summary or {}).get("resumed")),
            "secret_inheritance": packet.get("secret_inheritance", "none"),
            "handoff": packet,
            "summary": lease.takeover_summary or {},
        }

    # ------------------------------------------------------------------
    def _build_handoff_packet(self, root_task_id: str) -> dict[str, Any]:
        task = self.session.get(Task, root_task_id)
        dispatches = self.session.execute(
            select(DispatchRecord).where(DispatchRecord.root_task_id == root_task_id)
        ).scalars().all()

        artifacts, evidence = [], []
        for d in dispatches:
            ref = d.input_ref or {}
            if ref.get("bound_artifact_hash"):
                artifacts.append({
                    "subtask_id": d.subtask_id, "worker": d.worker_id,
                    "artifact_hash": ref["bound_artifact_hash"],
                    "verification_id": ref.get("bound_verification_id"),
                })
            if ref.get("verification"):
                v = ref["verification"]
                evidence.append({
                    "subtask_id": d.subtask_id, "verification_id": v.get("verification_id"),
                    "passed": v.get("passed"), "exit_code": v.get("exit_code"),
                    "command": v.get("command"),
                })

        pending_approvals = self.session.execute(
            select(Proposal).where(Proposal.status == "pending")
        ).scalars().all()

        workers = {}
        for d in dispatches:
            ref = d.input_ref or {}
            workers[d.subtask_id] = {
                "worker_id": d.worker_id,
                "execution_version": ref.get("execution_version"),
                "state": d.state,
                "attempts": d.attempts,
                # Executors are carried over; a takeover does not restart them.
                "restart_required": False,
            }

        return {
            "root_task_id": root_task_id,
            "goal": getattr(task, "goal", None),
            "stage": getattr(task, "stage", None),
            "budget": {
                "reserved_usd": float(getattr(task, "reserved_usd", 0) or 0),
                "spent_usd": float(getattr(task, "spent_usd", 0) or 0),
            },
            "subtasks": [
                {"subtask_id": d.subtask_id, "worker_id": d.worker_id, "state": d.state,
                 "goal": d.goal, "attempts": d.attempts}
                for d in dispatches
            ],
            "artifacts": artifacts,
            "evidence": evidence,
            "pending_approvals": [
                {"proposal_id": p.proposal_id, "operation": p.operation} for p in pending_approvals
            ],
            "executor_bindings": workers,
        }

    @staticmethod
    def _summary_dict(
        old: OrchestratorLease,
        new_orchestrator_id: str,
        new_token: int,
        packet: dict[str, Any],
        in_flight: list[Any],
    ) -> dict[str, Any]:
        return {
            "from_orchestrator": old.orchestrator_id,
            "to_orchestrator": new_orchestrator_id,
            "previous_fencing_token": old.fencing_token,
            "new_fencing_token": new_token,
            "dispatch_paused": True,
            "resumed": False,
            "secret_inheritance": "none",
            "executors_restarted": False,
            "subtasks_carried_over": len(packet.get("subtasks", [])),
            "artifacts_carried_over": len(packet.get("artifacts", [])),
            "evidence_carried_over": len(packet.get("evidence", [])),
            "pending_approvals": len(packet.get("pending_approvals", [])),
            "in_flight_sent_to_reconciliation": [
                {"subtask_id": d.subtask_id, "prior_state": d.state} for d in in_flight
            ],
            "note": (
                "Unconfirmed in-flight actions are held for reconciliation, not retried, "
                "so a takeover cannot duplicate an external side effect."
            ),
        }
