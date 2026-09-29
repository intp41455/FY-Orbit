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
from ...services.errors import Conflict

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
    report: dict = Field(default_factory=dict)


class PromoteBody(BaseModel):
    evaluation_id: str


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
                            functional_passed=body.functional_passed, report=body.report)
    svc.session.commit()
    return {"evaluation_id": ev.id, "static_passed": ev.static_passed,
            "functional_passed": ev.functional_passed}


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


@router.post("/{skill_id}/invoke")
async def invoke(skill_id: str, actor: Actor = Depends(csrf_protected),
               svc: Services = Depends(get_services)) -> dict:
    """Gateway invocation gate: A05 — only active skills may run."""
    skill = svc.session.get(Skill, skill_id)
    if skill is None:
        from ...services.errors import NotFound
        raise NotFound("skill_not_found", "Skill not found")
    if skill.state != "active":
        raise Conflict("skill_not_active",
                       f"Skill in state '{skill.state}' cannot be executed; "
                       "it must pass evaluation and be promoted first")
    # Real execution happens in the isolated sandbox (BLOCKED_EXTERNAL). We only
    # prove the gate; no privileged action runs here.
    return {"skill_id": skill.id, "state": skill.state, "executed": False,
            "note": "Sandboxed execution BLOCKED_EXTERNAL; gate passed"}
