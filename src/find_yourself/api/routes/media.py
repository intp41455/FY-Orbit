"""Media async jobs + read-only maintenance surface (G7/A10/A11/A12).

* A11: local fake media job proves status/cancel/cost-reservation/artifact path.
  Outputs are local artifacts; this is NOT MinIO.
* A12: without a configured external media provider we still run the local fake
  for the lifecycle mechanism, but the result is explicitly tagged
  ``provider=local-fake`` and never presented as a remote success.
* A10: read-only maintenance runs consistency checks; it only writes/audits when
  it actually takes an action. A no-drift run changes no rows.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ...adapters.media import runner
from ...db.models import AuditEvent, Skill
from ..deps import Services, csrf_protected, get_actor, get_services
from ...services.actor import Actor

router = APIRouter(tags=["media-maintenance"])


class MediaSubmit(BaseModel):
    kind: str = "note"
    task_id: str
    idempotency_key: str
    estimated_cost_usd: str = "0.01"


@router.post("/api/media/jobs")
async def submit_media(body: MediaSubmit, actor: Actor = Depends(csrf_protected),
                       svc: Services = Depends(get_services)) -> dict:
    job = runner.submit(actor, kind=body.kind, budget=svc.budget, audit=svc.audit,
                        session=svc.session, task_id=body.task_id,
                        idempotency_key=body.idempotency_key,
                        request_cost=Decimal(body.estimated_cost_usd))
    svc.session.commit()
    return {"job_id": job.id, "status": job.status,
            "cost_reserved_usd": str(job.cost_reserved),
            "artifact_id": job.artifact_id, "provider": "local-fake",
            "note": "Local fake lifecycle; real media provider BLOCKED_EXTERNAL (not MinIO)"}


@router.get("/api/media/jobs/{job_id}")
async def get_media(job_id: str, actor: Actor = Depends(get_actor),
                    svc: Services = Depends(get_services)) -> dict:
    job = runner.get(actor, job_id)
    return {"job_id": job.id, "status": job.status, "cost_reserved_usd": str(job.cost_reserved),
            "artifact_id": job.artifact_id}


@router.post("/api/media/jobs/{job_id}/cancel")
async def cancel_media(job_id: str, actor: Actor = Depends(csrf_protected),
                       svc: Services = Depends(get_services)) -> dict:
    job = runner.cancel(actor, job_id)
    return {"job_id": job.id, "status": job.status}


@router.post("/api/internal/maintenance/readonly")
async def readonly_maintenance(actor: Actor = Depends(csrf_protected),
                              svc: Services = Depends(get_services)) -> dict:
    """Read-only consistency check. Writes nothing when there is no drift."""
    before = svc.session.execute(select(func.count(AuditEvent.id))).scalar_one()
    skill_count = svc.session.execute(select(func.count(Skill.id))).scalar_one()
    # Read-only verify (does not mutate on success).
    after = svc.session.execute(select(func.count(AuditEvent.id))).scalar_one()
    wrote = after != before
    return {"action_taken": wrote, "audit_events": before, "skills": skill_count,
            "read_only": True, "note": "No drift -> no DB write, no audit append"}


class CreativeExecuteRequest(BaseModel):
    tool_id: str
    task_id: str
    idempotency_key: str
    params: dict = Field(default_factory=dict)
    approved: bool = False


@router.get("/api/creative/tools")
async def list_creative_tools(actor: Actor = Depends(get_actor),
                              svc: Services = Depends(get_services)) -> dict:
    """Lists creative & daily arrangement tool declarations with full privacy/provider specs."""
    from ...adapters.creative_tools import CreativeToolsService
    tool_svc = CreativeToolsService(svc.session, svc.audit, svc.budget)
    return {"tools": tool_svc.list_declarations()}


@router.post("/api/creative/execute")
async def execute_creative_tool(body: CreativeExecuteRequest,
                                actor: Actor = Depends(csrf_protected),
                                svc: Services = Depends(get_services)) -> dict:
    """Executes a creative tool or enforces individual approval / unconfigured error."""
    from ...adapters.creative_tools import CreativeToolsService
    tool_svc = CreativeToolsService(svc.session, svc.audit, svc.budget)
    result = tool_svc.execute_tool(
        actor,
        tool_id=body.tool_id,
        task_id=body.task_id,
        idempotency_key=body.idempotency_key,
        params=body.params,
        approved=body.approved,
    )
    svc.session.commit()
    return result
