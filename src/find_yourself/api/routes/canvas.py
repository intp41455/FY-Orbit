"""API routes for 05 Multi-Agent Collaboration Canvas (多Agent协作可视化画布)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


class CreateInstanceRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=100)
    template_id: str = Field(default="personal", pattern="^(personal|work)$")
    orchestrator_id: str | None = None


class DispatchSubtaskRequest(BaseModel):
    root_task_id: str = Field(min_length=1, max_length=64)
    worker_id: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=300)
    acceptance_criteria: Any = Field(default="")
    budget_slice: float = Field(default=0.05, gt=0.0, le=0.50)
    deadline: datetime | None = None
    input_ref: dict[str, Any] = Field(default_factory=dict)
    auto_run: bool | None = None


class RecordHandoffRequest(BaseModel):
    stage: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=300)
    source_worker_id: str = Field(min_length=1, max_length=64)
    target_worker_id: str = Field(min_length=1, max_length=64)
    source_task_id: str = Field(min_length=1, max_length=64)
    completed_items: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class CompleteSubtaskRequest(BaseModel):
    output: str = Field(default="")
    settled_budget: float | None = None
    artifact_version: str | None = None
    verification_id: str | None = None


class CancelSubtaskRequest(BaseModel):
    reason: str = Field(default="User cancelled subtask")


class RequestReworkRequest(BaseModel):
    feedback: str = Field(min_length=1)
    criteria_unmet: list[str] = Field(default_factory=list)


class ResubmitSubtaskRequest(BaseModel):
    output: str = Field(default="")
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class VerifySubtaskRequest(BaseModel):
    test_results: dict[str, Any] = Field(default_factory=dict)
    artifact_hash: str | None = None


class ExecuteVerificationRequest(BaseModel):
    workspace_dir: str
    command: list[str] = Field(default_factory=list)
    target_files: list[str] = Field(default_factory=list)
    timeout_seconds: float = 30.0


class HarnessRunRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=300)
    executor: str = Field(default="peri", max_length=64)
    ecc_skills: list[str] = Field(default_factory=list)
    budget_limit_usd: float = Field(default=0.05, gt=0.0, le=0.50)
    deadline_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    input_refs: dict[str, Any] = Field(default_factory=dict)
    workspace_dir: str = Field(default="")


class HarnessCancelRequest(BaseModel):
    execution_id: str = Field(min_length=1, max_length=128)


@router.get("/templates")
async def list_templates(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return {"items": svc.canvas.list_templates()}


@router.get("/connectors")
async def probe_connectors(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    # Authenticated: this probe discloses server-side binaries/paths and spawns
    # version-check subprocesses — never anonymous-reachable.
    return {"items": svc.canvas.probe_connectors()}


@router.get("/harnesses")
async def probe_community_harnesses(
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Probe community harness execution candidates (CCB, cc-fleet, Peri, ECC)."""
    from ...adapters.community_harness_adapter import CommunityHarnessRegistry
    registry = CommunityHarnessRegistry()
    return {"items": registry.probe_all()}


@router.post("/instances", status_code=status.HTTP_201_CREATED)
async def create_instance(
    body: CreateInstanceRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    inst = svc.canvas.create_instance(
        actor,
        project_name=body.project_name,
        template_id=body.template_id,
        orchestrator_id=body.orchestrator_id,
    )
    svc.session.commit()
    return {
        "id": inst.id,
        "owner_id": inst.owner_id,
        "project_name": inst.project_name,
        "domain": inst.domain,
        "template_id": inst.template_id,
        "orchestrator_id": inst.orchestrator_id,
        "state": inst.state,
        "config": inst.config,
        "created_at": inst.created_at.isoformat(),
    }


@router.get("/instances")
async def list_instances(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.canvas.list_instances(actor)
    return {
        "items": [
            {
                "id": i.id,
                "project_name": i.project_name,
                "domain": i.domain,
                "template_id": i.template_id,
                "orchestrator_id": i.orchestrator_id,
                "state": i.state,
                "created_at": i.created_at.isoformat(),
            }
            for i in items
        ],
        "count": len(items),
    }


@router.get("/instances/{id}")
async def get_instance(
    id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    inst = svc.canvas.get_instance(actor, id)
    return {
        "id": inst.id,
        "owner_id": inst.owner_id,
        "project_name": inst.project_name,
        "domain": inst.domain,
        "template_id": inst.template_id,
        "orchestrator_id": inst.orchestrator_id,
        "state": inst.state,
        "config": inst.config,
        "created_at": inst.created_at.isoformat(),
    }


@router.get("/instances/{id}/snapshot")
async def get_instance_snapshot(
    id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.canvas.get_snapshot(actor, id)


@router.get("/instances/{id}/events")
async def get_instance_events(
    id: str,
    cursor: int = Query(default=0, ge=0),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    events = svc.canvas.get_events(actor, id, cursor=cursor)
    return {"items": events, "count": len(events)}


@router.post("/instances/{id}/dispatch", status_code=status.HTTP_201_CREATED)
async def dispatch_subtask(
    id: str,
    body: DispatchSubtaskRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    disp = svc.canvas.dispatch_subtask(
        actor,
        instance_id=id,
        root_task_id=body.root_task_id,
        worker_id=body.worker_id,
        goal=body.goal,
        acceptance_criteria=body.acceptance_criteria,
        budget_slice=body.budget_slice,
        deadline=body.deadline,
        input_ref=body.input_ref,
        auto_run=body.auto_run,
    )
    svc.session.commit()
    return {
        "id": disp.id,
        "instance_id": disp.instance_id,
        "root_task_id": disp.root_task_id,
        "subtask_id": disp.subtask_id,
        "orchestrator_id": disp.orchestrator_id,
        "worker_id": disp.worker_id,
        "goal": disp.goal,
        "state": disp.state,
        "budget_slice": float(disp.budget_slice),
        "created_at": disp.created_at.isoformat(),
    }


@router.post("/instances/{id}/handoff", status_code=status.HTTP_201_CREATED)
async def record_handoff(
    id: str,
    body: RecordHandoffRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    hnd = svc.canvas.record_handoff(
        actor,
        instance_id=id,
        stage=body.stage,
        goal=body.goal,
        source_worker_id=body.source_worker_id,
        target_worker_id=body.target_worker_id,
        source_task_id=body.source_task_id,
        completed_items=body.completed_items,
        artifact_refs=body.artifact_refs,
        evidence_refs=body.evidence_refs,
        unresolved_issues=body.unresolved_issues,
        risks=body.risks,
        next_steps=body.next_steps,
    )
    svc.session.commit()
    return {
        "id": hnd.id,
        "instance_id": hnd.instance_id,
        "stage": hnd.stage,
        "source_worker_id": hnd.source_worker_id,
        "target_worker_id": hnd.target_worker_id,
        "completed_items": hnd.completed_items,
        "created_at": hnd.created_at.isoformat(),
    }


@router.post("/instances/{id}/subtasks/{subtask_id}/complete")
async def complete_subtask(
    id: str,
    subtask_id: str,
    body: CompleteSubtaskRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rec = svc.canvas.complete_subtask(
        actor,
        instance_id=id,
        subtask_id=subtask_id,
        output=body.output,
        settled_budget=body.settled_budget,
        artifact_version=body.artifact_version,
        verification_id=body.verification_id,
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "output": (rec.input_ref or {}).get("output", ""),
        "completed_by": (rec.input_ref or {}).get("completed_by"),
        "is_manual_completion": (rec.input_ref or {}).get("is_manual_completion", True),
        "bound_verification_id": (rec.input_ref or {}).get("bound_verification_id"),
        "bound_artifact_hash": (rec.input_ref or {}).get("bound_artifact_hash"),
        "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
    }


@router.post("/instances/{id}/subtasks/{subtask_id}/cancel")
async def cancel_subtask(
    id: str,
    subtask_id: str,
    body: CancelSubtaskRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rec = svc.canvas.cancel_subtask(
        actor,
        instance_id=id,
        subtask_id=subtask_id,
        reason=body.reason,
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "cancel_reason": (rec.input_ref or {}).get("cancel_reason"),
        "process_tree_killed": (rec.input_ref or {}).get("process_tree_killed", False),
        "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
    }


@router.post("/instances/{id}/subtasks/{subtask_id}/rework")
async def request_rework(
    id: str,
    subtask_id: str,
    body: RequestReworkRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rec = svc.canvas.request_rework(
        actor,
        instance_id=id,
        subtask_id=subtask_id,
        feedback=body.feedback,
        criteria_unmet=body.criteria_unmet,
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "attempts": rec.attempts,
        "rework_history": (rec.input_ref or {}).get("rework_history", []),
    }


@router.post("/instances/{id}/subtasks/{subtask_id}/resubmit")
async def resubmit_subtask(
    id: str,
    subtask_id: str,
    body: ResubmitSubtaskRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rec = svc.canvas.resubmit_subtask(
        actor,
        instance_id=id,
        subtask_id=subtask_id,
        output=body.output,
        artifacts=body.artifacts,
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "attempts": rec.attempts,
        "output": (rec.input_ref or {}).get("output", ""),
        "reworked_artifacts": (rec.input_ref or {}).get("reworked_artifacts", []),
    }


@router.post("/instances/{id}/subtasks/{subtask_id}/verify")
async def verify_subtask(
    id: str,
    subtask_id: str,
    body: VerifySubtaskRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rec = svc.canvas.verify_subtask(
        actor,
        instance_id=id,
        subtask_id=subtask_id,
        test_results=body.test_results,
        artifact_hash=body.artifact_hash,
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "verification": (rec.input_ref or {}).get("verification", {}),
    }


@router.post("/instances/{id}/subtasks/{subtask_id}/execute-verification")
async def execute_verification(
    id: str,
    subtask_id: str,
    body: ExecuteVerificationRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """Execute actual test command on workspace code artifacts via TrustedVerificationRunner."""
    rec = svc.canvas.execute_trusted_verification(
        actor,
        instance_id=id,
        subtask_id=subtask_id,
        workspace_dir=body.workspace_dir,
        command=body.command,
        target_files=body.target_files,
        timeout_seconds=body.timeout_seconds,
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "verification": (rec.input_ref or {}).get("verification", {}),
    }


@router.post("/harness-run")
async def run_community_harness(
    body: HarnessRunRequest,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """Execute a task in isolated sandbox using community harness (Peri / Peri+ECC / etc.)."""
    from ...adapters.community_harness_adapter import PeriAdapter, TaskEnvelope
    envelope = TaskEnvelope(
        task_id=body.task_id,
        goal=body.goal,
        workspace_dir=body.workspace_dir,
        budget_limit_usd=body.budget_limit_usd,
        deadline_seconds=body.deadline_seconds,
        input_refs=body.input_refs,
        executor=body.executor,
        ecc_skills=body.ecc_skills,
    )
    adapter = PeriAdapter()
    result = adapter.submit(envelope)
    return {
        "execution_id": result.execution_id,
        "task_id": result.task_id,
        "executor": result.executor,
        "status": result.status,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "cost_status": result.cost_status,
        "estimated_cost_usd": result.estimated_cost_usd,
        "artifacts": result.artifacts,
        "events": result.events,
        "output": result.output,
        "error_message": result.error_message,
        "sandbox_boundary_enforced": result.sandbox_boundary_enforced,
    }


@router.post("/harness-cancel")
async def cancel_community_harness(
    body: HarnessCancelRequest,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """Cancel an active community harness execution."""
    from ...adapters.community_harness_adapter import PeriAdapter
    adapter = PeriAdapter()
    cancelled = adapter.cancel(body.execution_id)
    return {"execution_id": body.execution_id, "cancelled": cancelled}


@router.post("/benchmark/run")
async def run_harness_benchmark(
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """Execute the controlled comparison benchmark across Baseline, Peri, and Peri+ECC."""
    from ...adapters.community_harness_adapter import HarnessBenchmarkRunner
    runner = HarnessBenchmarkRunner()
    report = runner.run_benchmark()
    return report


@router.get("/benchmark/summary")
async def get_benchmark_summary(
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Retrieve the latest community harness benchmark comparison results."""
    import json

    from ...adapters.community_harness_adapter import REPO_ROOT, HarnessBenchmarkRunner

    evidence_file = REPO_ROOT / "evidence" / "community_harness_benchmark.json"
    if evidence_file.exists():
        try:
            return json.loads(evidence_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Run benchmark if not yet run
    runner = HarnessBenchmarkRunner()
    return runner.run_benchmark()

