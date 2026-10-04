"""Unit tests: 18 §8 主协调者更换与接管 — fencing token, reconciliation, resume.

Verifies the guarantees the spec demands:
- one valid lease per root task;
- late dispatch from a revoked/older orchestrator is rejected;
- an ineligible candidate (version/help only) cannot take over;
- in-flight actions go to reconciliation instead of blind retry;
- executors are carried over, not restarted;
- no secret session inheritance.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from find_yourself.db.canvas_models import CanvasInstance, DispatchRecord
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.orchestrator_lease import (
    IN_FLIGHT_STATES,
    OrchestratorLeaseService,
)


@pytest.fixture()
def leases(session, audit) -> OrchestratorLeaseService:
    return OrchestratorLeaseService(session, audit)


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1", csrf_token="")


CAPS = ["plan", "dispatch", "review"]


def _dispatch(session, root_task_id: str, subtask_id: str, worker: str, state: str = "running"):
    inst = CanvasInstance(
        id=f"inst-{subtask_id}", owner_id="owner-1", project_name="p",
        domain="work", template_id="work", orchestrator_id="Codex", state="active",
    )
    session.add(inst)
    rec = DispatchRecord(
        id=f"dr-{subtask_id}",
        instance_id=inst.id,
        root_task_id=root_task_id,
        subtask_id=subtask_id,
        orchestrator_id="Codex",
        worker_id=worker,
        idempotency_key=f"idem-{subtask_id}",
        input_ref={"execution_version": "v3"},
        goal="修复缺陷并通过验证",
        acceptance_criteria="pytest 全绿",
        budget_slice=0.05,
        deadline=utcnow() + timedelta(hours=1),
        state=state,
    )
    session.add(rec)
    session.flush()
    return rec


# ----------------------------------------------------------------------
def test_acquire_then_duplicate_is_rejected(leases: OrchestratorLeaseService, owner: Actor):
    first = leases.acquire(owner, "root-1", "Codex", capabilities=CAPS)
    assert first["fencing_token"] == 1
    with pytest.raises(Conflict):
        leases.acquire(owner, "root-1", "Hermes", capabilities=CAPS)


def test_current_lease_lookup(leases: OrchestratorLeaseService, owner: Actor):
    leases.acquire(owner, "root-2", "Codex", capabilities=CAPS)
    cur = leases.current(owner, "root-2")
    assert cur["orchestrator_id"] == "Codex"
    with pytest.raises(NotFound):
        leases.current(owner, "root-none")


def test_valid_dispatch_passes(leases: OrchestratorLeaseService, owner: Actor):
    leases.acquire(owner, "root-3", "Codex", capabilities=CAPS)
    res = leases.validate_dispatch("root-3", "Codex", 1)
    assert res["valid"] is True


def test_stale_fencing_token_is_rejected(leases: OrchestratorLeaseService, owner: Actor):
    leases.acquire(owner, "root-4", "Codex", capabilities=CAPS)
    with pytest.raises(Conflict):
        leases.validate_dispatch("root-4", "Codex", 0)
    with pytest.raises(Conflict):
        leases.validate_dispatch("root-4", "Codex", None)


def test_dispatch_from_another_orchestrator_is_rejected(
    leases: OrchestratorLeaseService, owner: Actor
):
    leases.acquire(owner, "root-5", "Codex", capabilities=CAPS)
    with pytest.raises(PermissionDenied):
        leases.validate_dispatch("root-5", "Hermes", 1)


# ----------------------------------------------------------------------
def test_takeover_requires_eligible_coordinator(leases: OrchestratorLeaseService, owner: Actor):
    leases.acquire(owner, "root-6", "Codex", capabilities=CAPS)
    # Only answers version/help → not a coordinator (18 §7).
    with pytest.raises(ValidationFailed):
        leases.begin_takeover(owner, "root-6", "StaticDocGen", new_capabilities=["version"])
    with pytest.raises(ValidationFailed):
        leases.begin_takeover(
            owner, "root-6", "DesignOnly", new_capabilities=CAPS, new_connector_stage="仅设计"
        )


def test_takeover_same_orchestrator_is_refused(leases: OrchestratorLeaseService, owner: Actor):
    leases.acquire(owner, "root-7", "Codex", capabilities=CAPS)
    with pytest.raises(Conflict):
        leases.begin_takeover(owner, "root-7", "Codex", new_capabilities=CAPS)


def test_takeover_pauses_dispatch_until_resumed(leases: OrchestratorLeaseService, owner: Actor):
    leases.acquire(owner, "root-8", "Codex", capabilities=CAPS)
    summary = leases.begin_takeover(owner, "root-8", "Hermes", new_capabilities=CAPS)
    token = summary["fencing_token"]

    assert summary["orchestrator_id"] == "Hermes"
    assert summary["paused"] is True
    assert summary["resumed"] is False
    assert token == 2

    # New orchestrator with the new token still cannot dispatch while paused.
    with pytest.raises(Conflict):
        leases.validate_dispatch("root-8", "Hermes", token)

    resumed = leases.resume_takeover(owner, "root-8")
    assert resumed["paused"] is False
    assert resumed["resumed"] is True
    assert leases.validate_dispatch("root-8", "Hermes", token)["valid"] is True


def test_old_orchestrator_cannot_dispatch_after_takeover(
    leases: OrchestratorLeaseService, owner: Actor
):
    leases.acquire(owner, "root-9", "Codex", capabilities=CAPS)
    leases.begin_takeover(owner, "root-9", "Hermes", new_capabilities=CAPS)
    leases.resume_takeover(owner, "root-9")
    # The revoked orchestrator still holds token 1; its dispatch must be refused.
    with pytest.raises(PermissionDenied):
        leases.validate_dispatch("root-9", "Codex", 1)


# ----------------------------------------------------------------------
def test_in_flight_actions_go_to_reconciliation_not_retry(
    leases: OrchestratorLeaseService, owner: Actor, session
):
    leases.acquire(owner, "root-10", "Codex", capabilities=CAPS)
    rec = _dispatch(session, "root-10", "sub-1", "Peri", state="running")

    summary = leases.begin_takeover(owner, "root-10", "Hermes", new_capabilities=CAPS)

    assert rec.state == "unknown_needs_reconciliation"
    assert rec.input_ref["takeover_reconciliation"]["required"] is True
    # The executor binding is preserved — a takeover does not restart workers.
    assert rec.input_ref["takeover_reconciliation"]["execution_version_preserved"] == "v3"
    assert summary["summary"]["executors_restarted"] is False
    assert summary["summary"]["in_flight_sent_to_reconciliation"]


def test_takeover_packet_carries_artifacts_evidence_and_approvals(
    leases: OrchestratorLeaseService, owner: Actor, session
):
    leases.acquire(owner, "root-11", "Codex", capabilities=CAPS)
    rec = _dispatch(session, "root-11", "sub-2", "Peri", state="completed")
    rec.input_ref = {
        "execution_version": "v1",
        "bound_artifact_hash": "a" * 64,
        "bound_verification_id": "verif-abc",
        "verification": {
            "verification_id": "verif-abc", "passed": True, "exit_code": 0,
            "command": ["python", "-m", "pytest"],
        },
    }
    session.flush()

    summary = leases.begin_takeover(owner, "root-11", "Hermes", new_capabilities=CAPS)
    handoff = summary["handoff"]
    assert handoff["artifacts"] and handoff["artifacts"][0]["artifact_hash"] == "a" * 64
    assert handoff["evidence"] and handoff["evidence"][0]["verification_id"] == "verif-abc"
    assert handoff["executor_bindings"]["sub-2"]["restart_required"] is False
    assert summary["secret_inheritance"] == "none"


def test_completed_subtasks_are_not_sent_to_reconciliation(
    leases: OrchestratorLeaseService, owner: Actor, session
):
    leases.acquire(owner, "root-12", "Codex", capabilities=CAPS)
    rec = _dispatch(session, "root-12", "sub-3", "Peri", state="completed")
    leases.begin_takeover(owner, "root-12", "Hermes", new_capabilities=CAPS)
    assert rec.state == "completed"
    assert rec.state not in IN_FLIGHT_STATES


def test_summary_is_owner_viewable_after_takeover(
    leases: OrchestratorLeaseService, owner: Actor
):
    leases.acquire(owner, "root-13", "Codex", capabilities=CAPS)
    leases.begin_takeover(owner, "root-13", "Hermes", new_capabilities=CAPS)
    s = leases.takeover_summary(owner, "root-13")
    assert s["orchestrator_id"] == "Hermes"
    assert s["fencing_token"] == 2
    assert s["secret_inheritance"] == "none"
    assert s["summary"]["from_orchestrator"] == "Codex"
    assert s["summary"]["to_orchestrator"] == "Hermes"
