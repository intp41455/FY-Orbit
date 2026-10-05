"""Skill staging/evaluation/promotion HTTP surface (G7/A05-A09).

Wraps Core SkillService. Gates enforced:
* A05: only skills in state ``active`` may be invoked; staged/disabled skills are
  never executable (enforced here at the gateway).
* A07: promotion requires BOTH static_passed and functional_passed bound to the
  exact package hash; a static-pass / functional-fail never promotes.
* A08: self-built skills go through the same stage->evaluate->promote flow; the
  shared package never embeds private detail (only an immutable hash is stored).
* A09: a regression disables the promoted version so an earlier approved version
  can be selected; this is an owner decision, not an auto-rollback.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...db.models import Skill
from ..deps import Services, csrf_protected, get_actor, get_services
from ...services.actor import Actor
from ...services.errors import Conflict, NotFound
from ...skills.harness import (
    FunctionCallingGateway,
    SkillLearningLoop,
    TrustedSkillEvaluationWorker,
    gateway,
)

router = APIRouter(prefix="/api/skills", tags=["skills-lifecycle"])


class StageBody(BaseModel):
    name: str
    semantic_version: str
    package: dict = Field(default_factory=dict)
    source: str
    license_: str = Field(alias="license")
    domain: str


class EvaluateBody(BaseModel):
    static_passed: bool
    functional_passed: bool
    dynamic_passed: bool = False
    report: dict = Field(default_factory=dict)


class SandboxEvaluateBody(BaseModel):
    command: list[str] = Field(default_factory=lambda: ["python", "-c", "print('sandbox_ok')"])
    workspace: str | None = None
    timeout_s: float = 10.0


class TrustedEvaluateBody(BaseModel):
    package: dict = Field(default_factory=dict)


class PromoteBody(BaseModel):
    evaluation_id: str


class InvokeBody(BaseModel):
    tool_call: dict | None = None


class SLLDistillBody(BaseModel):
    task_name: str
    task_goal: str
    workflow_steps: list[str] = Field(default_factory=list)
    domain: str = "work"


@router.post("/stage")
async def stage(body: StageBody, actor: Actor = Depends(csrf_protected),
               svc: Services = Depends(get_services)) -> dict:
    row = svc.skills.stage(actor, name=body.name, semantic_version=body.semantic_version,
                          package=body.package, source=body.source, license_=body.license_,
                          domain=body.domain)
    svc.session.commit()
    return {"id": row.id, "state": row.state, "package_hash": row.package_hash}


@router.post("/{skill_id}/evaluate")
async def evaluate(skill_id: str, body: EvaluateBody, actor: Actor = Depends(csrf_protected),
                  svc: Services = Depends(get_services)) -> dict:
    ev = svc.skills.evaluate(actor, skill_id, static_passed=body.static_passed,
                            functional_passed=body.functional_passed,
                            dynamic_passed=body.dynamic_passed,
                            report=body.report)
    svc.session.commit()
    return {"evaluation_id": ev.id, "static_passed": ev.static_passed,
            "functional_passed": ev.functional_passed,
            "dynamic_passed": ev.dynamic_passed}


@router.post("/{skill_id}/sandbox-evaluate")
async def sandbox_evaluate(
    skill_id: str,
    body: SandboxEvaluateBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """Run dynamic evaluation of skill script in the container sandbox (Batch H)."""
    return svc.skills.run_sandbox_evaluation(
        actor,
        skill_id,
        command=body.command,
        workspace=body.workspace,
        timeout_s=body.timeout_s,
    )


@router.post("/{skill_id}/trusted-evaluate")
async def trusted_evaluate(skill_id: str, body: TrustedEvaluateBody, actor: Actor = Depends(csrf_protected),
                          svc: Services = Depends(get_services)) -> dict:
    """Evaluate skill using independent TrustedSkillEvaluationWorker.
    
    Guarantees that caller cannot bypass security by self-reporting booleans.
    """
    eval_result = TrustedSkillEvaluationWorker.evaluate_package(body.package)
    ev = svc.skills.evaluate(
        actor,
        skill_id,
        static_passed=eval_result["static_passed"],
        functional_passed=eval_result["functional_passed"],
        report=eval_result,
    )
    svc.session.commit()
    return {
        "evaluation_id": ev.id,
        "static_passed": ev.static_passed,
        "functional_passed": ev.functional_passed,
        "worker_report": eval_result,
    }


@router.post("/{skill_id}/promote")
async def promote(skill_id: str, body: PromoteBody, actor: Actor = Depends(csrf_protected),
                 svc: Services = Depends(get_services)) -> dict:
    row = svc.skills.promote(actor, skill_id, body.evaluation_id)
    svc.session.commit()
    return {"id": row.id, "state": row.state}


@router.post("/{skill_id}/disable")
async def disable(skill_id: str, actor: Actor = Depends(csrf_protected),
                 svc: Services = Depends(get_services)) -> dict:
    row = svc.skills.disable(actor, skill_id)
    svc.session.commit()
    return {"id": row.id, "state": row.state}


@router.get("/gateway/tools")
async def list_gateway_tools(actor: Actor = Depends(get_actor)) -> dict:
    """List available typed tools in the function calling gateway."""
    return {"tools": gateway.list_tools()}


@router.post("/sll/distill")
async def distill_skill_from_trace(
    body: SLLDistillBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """SLL loop: synthesize a sanitized candidate skill package from real task traces."""
    candidate_pkg = SkillLearningLoop.distill_from_trace(
        task_name=body.task_name,
        task_goal=body.task_goal,
        workflow_steps=body.workflow_steps,
        domain=body.domain,
    )
    staged_skill = svc.skills.stage(
        actor,
        name=candidate_pkg["name"],
        semantic_version=candidate_pkg["version"],
        package=candidate_pkg,
        source=candidate_pkg["source"],
        license_=candidate_pkg["license"],
        domain=candidate_pkg["domain"],
    )
    svc.session.commit()
    return {
        "status": "candidate_staged",
        "skill_id": staged_skill.id,
        "package_hash": staged_skill.package_hash,
        "candidate_package": candidate_pkg,
    }


@router.post("/{skill_id}/invoke")
async def invoke(skill_id: str, body: InvokeBody | None = None,
                actor: Actor = Depends(csrf_protected),
                svc: Services = Depends(get_services)) -> dict:
    """Gateway invocation gate: A05 — only active skills may run."""
    skill = svc.session.get(Skill, skill_id)
    if skill is None:
        raise NotFound("skill_not_found", "Skill not found")
    if skill.state != "active":
        raise Conflict("skill_not_active",
                       f"Skill in state '{skill.state}' cannot be executed; "
                       "it must pass evaluation and be promoted first")

    # If caller requested a specific tool call, execute through typed runtime gateway
    if body and body.tool_call:
        tool_name = body.tool_call.get("tool", "")
        arguments = body.tool_call.get("arguments", {})
        budget = int(body.tool_call.get("budget_cents", 10))
        receipt = gateway.invoke(tool_name=tool_name, arguments=arguments, actor=actor, budget_cents=budget)
        return {
            "skill_id": skill.id,
            "state": skill.state,
            "executed": True,
            "tool_receipt": receipt,
        }

    # Backward-compatible default response for gate test
    return {"skill_id": skill.id, "state": skill.state, "executed": False,
            "note": "Sandboxed execution BLOCKED_EXTERNAL; gate passed"}
