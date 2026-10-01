"""API routes for 05 Multi-Agent Collaboration Canvas (多Agent协作可视化画布)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from ..deps import csrf_protected, get_actor, get_services, Services
from ...services.actor import Actor

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


@router.get("/templates")
async def list_templates(
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return {"items": svc.canvas.list_templates()}


@router.get("/connectors")
async def probe_connectors(
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return {"items": svc.canvas.probe_connectors()}


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
    )
    svc.session.commit()
    return {
        "id": rec.id,
        "instance_id": rec.instance_id,
        "subtask_id": rec.subtask_id,
        "worker_id": rec.worker_id,
        "state": rec.state,
        "output": (rec.input_ref or {}).get("output", ""),
        "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
    }
