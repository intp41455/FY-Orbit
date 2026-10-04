"""Prompt template library HTTP surface (行动项 #13 / 工单 P1-06).

Reads (login only, 免审):
* ``GET  /api/prompts``                — list (search / scope / status filters,
  pending-proposal badge per row),
* ``GET  /api/prompts/{name}``         — detail + version history,
* ``GET  /api/prompts/{name}/render``  — read-only preview; validation failures
  produce NO render log and NO cost; success produces NO render log either
  (previews are not usages — the programmatic service path audits),
* ``GET  /api/prompts/{name}/logs``    — render audit trail (hashes only).

Writes (governance per design §5 / UI §三):
* ``POST /api/prompts`` — creates a DRAFT template (is_active=false; the UI's
  「仅保存草稿」). A draft can never render until activated via proposal.
* ``PUT  /api/prompts/{name}`` — metadata (description/scope) only; any
  content/variables_schema field is REJECTED (content changes must be
  proposed — acceptance criterion 4).
* ``POST /api/prompts/{name}/stage``    — propose a new version (prompt.stage).
* ``PUT  /api/prompts/{name}/activate`` — propose switching the effective
  version pointer (prompt.activate).
* ``POST /api/prompts/{name}/disable``  — propose disabling (prompt.disable).
* ``POST /api/prompts/{name}/apply``    — owner-only deterministic executor for
  an approved prompt.* proposal (re-verifies status + digest; idempotent).

The write endpoints create proposals through the existing
``/api/proposals/{id}/decision`` flow; ``apply`` then lands the change.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ...db.models import Proposal
from ...db.prompt_models import PROMPT_SCOPES, PromptRenderLog, PromptTemplate
from ..deps import Services, csrf_protected, get_actor, get_services
from ...services.actor import Actor
from ...services.errors import ValidationFailed
from ...services.prompt import PromptService

router = APIRouter(prefix="/api/prompts", tags=["prompt-templates"])


class _Strict(BaseModel):
    """Reject unknown fields so a smuggled ``content``/``variables_schema``
    can never bypass the proposal gate on the metadata endpoint."""

    model_config = ConfigDict(extra="forbid")


class PromptCreateBody(_Strict):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    variables_schema: dict = Field(default_factory=dict)
    scope: str = "platform"
    description: str = ""
    owner: str | None = None


class PromptMetaBody(_Strict):
    description: str | None = None
    scope: str | None = None
    version: int | None = None  # optimistic lock


class PromptStageBody(_Strict):
    content: str = Field(min_length=1)
    variables_schema: dict | None = None
    reason: str = Field(min_length=3, max_length=3000)
    rollback: str = ""
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)


class PromptActivateBody(_Strict):
    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=3000)
    rollback: str = ""
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)


class PromptReasonBody(_Strict):
    reason: str = Field(min_length=3, max_length=3000)
    rollback: str = ""
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)


class PromptApplyBody(_Strict):
    proposal_id: str = Field(min_length=8)
    digest: str = Field(min_length=8, max_length=64)


def _svc(svc: Services) -> PromptService:
    return PromptService(svc.session, svc.audit)


def _serialize_template(tpl: PromptTemplate) -> dict:
    return {
        "id": tpl.id, "name": tpl.name, "latest_version": tpl.latest_version,
        "variables_schema": tpl.variables_schema, "owner": tpl.owner,
        "scope": tpl.scope, "description": tpl.description,
        "is_active": tpl.is_active,
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
        "version": tpl.version,
    }


def _serialize_version(v) -> dict:
    return {
        "version": v.version, "content": v.content, "content_hash": v.content_hash,
        "variables_schema": v.variables_schema, "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _pending_proposals(svc: Services, names: list[str]) -> dict[str, dict]:
    """Latest pending prompt proposal per template (UI badge)."""
    if not names:
        return {}
    rows = svc.session.execute(
        select(Proposal)
        .where(
            Proposal.operation.in_(("prompt.stage", "prompt.activate", "prompt.disable")),
            Proposal.target_id.in_(names),
            Proposal.status == "pending",
        )
        .order_by(Proposal.created_at.desc())
    ).scalars()
    out: dict[str, dict] = {}
    for p in rows:
        if p.target_id not in out:
            out[p.target_id] = {
                "proposal_id": p.id, "operation": p.operation, "digest": p.digest,
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
    return out


# -- reads (免审, login only) ---------------------------------------------------

@router.get("")
async def list_prompts(
    q: str | None = Query(default=None, max_length=200),
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None, description="active | disabled | draft"),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> list[dict]:
    if scope is not None and scope not in PROMPT_SCOPES:
        raise ValidationFailed("bad_scope", f"scope must be one of {PROMPT_SCOPES}")
    if status is not None and status not in ("active", "disabled", "draft"):
        raise ValidationFailed("bad_status", "status must be active | disabled | draft")
    stmt = select(PromptTemplate).order_by(PromptTemplate.name.asc())
    rows = list(svc.session.execute(stmt).scalars())
    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in r.name.lower() or needle in (r.description or "").lower()]
    if scope:
        rows = [r for r in rows if r.scope == scope]
    if status == "active":
        rows = [r for r in rows if r.is_active]
    elif status == "disabled":
        rows = [r for r in rows if not r.is_active and r.latest_version > 0]
    elif status == "draft":
        rows = [r for r in rows if not r.is_active]
    pending = _pending_proposals(svc, [r.name for r in rows])
    result = []
    for r in rows:
        item = _serialize_template(r)
        item["pending_proposal"] = pending.get(r.name)
        result.append(item)
    return result


@router.get("/{name}")
async def get_prompt(name: str, actor: Actor = Depends(get_actor),
                     svc: Services = Depends(get_services)) -> dict:
    ps = _svc(svc)
    tpl = ps.get_template(name)
    item = _serialize_template(tpl)
    item["versions"] = [_serialize_version(v) for v in ps.get_versions(name)]
    item["pending_proposal"] = _pending_proposals(svc, [name]).get(name)
    return item


@router.get("/{name}/render")
async def render_preview(
    name: str,
    variables: str = Query(default="{}", description="JSON object of variables"),
    version: int | None = Query(default=None, ge=1),
    task_id: str | None = Query(default=None, max_length=64),
    scope: str | None = Query(default=None),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        parsed = json.loads(variables) if variables else {}
    except json.JSONDecodeError as exc:
        raise ValidationFailed("bad_variables", f"variables must be valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise ValidationFailed("bad_variables", "variables must be a JSON object")
    # Preview: no render log, no cost (UI §2.3; acceptance criterion 3).
    rendered = _svc(svc).render(
        name, parsed, version=version, task_id=task_id, scope=scope, log=False,
    )
    return {
        "template": rendered.name, "version": rendered.version,
        "text": rendered.text, "content_hash": rendered.content_hash,
        "variables_hash": rendered.variables_hash,
    }


@router.get("/{name}/logs")
async def render_logs(name: str, limit: int = Query(default=50, ge=1, le=200),
                      actor: Actor = Depends(get_actor),
                      svc: Services = Depends(get_services)) -> list[dict]:
    _svc(svc).get_template(name)  # 404 when unknown
    rows = svc.session.execute(
        select(PromptRenderLog)
        .where(PromptRenderLog.template_name == name)
        .order_by(PromptRenderLog.created_at.desc())
        .limit(limit)
    ).scalars()
    return [{
        "id": r.id, "template_name": r.template_name, "version": r.version,
        "variables_hash": r.variables_hash, "scope": r.scope, "task_id": r.task_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


# -- writes ---------------------------------------------------------------------

@router.post("")
async def create_prompt(body: PromptCreateBody, actor: Actor = Depends(csrf_protected),
                        svc: Services = Depends(get_services)) -> dict:
    tpl = _svc(svc).create(
        actor, name=body.name, content=body.content,
        variables_schema=body.variables_schema, scope=body.scope,
        description=body.description, owner=body.owner,
    )
    svc.session.commit()
    return _serialize_template(tpl)


@router.put("/{name}")
async def update_prompt_meta(name: str, body: PromptMetaBody,
                             actor: Actor = Depends(csrf_protected),
                             svc: Services = Depends(get_services)) -> dict:
    if body.scope is None and body.description is None:
        raise ValidationFailed(
            "nothing_to_update",
            "Only description/scope may be edited directly; content and "
            "variables_schema changes must go through a prompt.stage proposal",
        )
    tpl = _svc(svc).update_meta(
        actor, name, description=body.description, scope=body.scope,
        expected_version=body.version,
    )
    svc.session.commit()
    return _serialize_template(tpl)


@router.post("/{name}/stage")
async def stage_prompt(name: str, body: PromptStageBody,
                       actor: Actor = Depends(csrf_protected),
                       svc: Services = Depends(get_services)) -> dict:
    p = _svc(svc).propose_stage(
        actor, name, content=body.content, variables_schema=body.variables_schema,
        reason=body.reason, rollback=body.rollback,
        expires_in_minutes=body.expires_in_minutes,
    )
    svc.session.commit()
    return {"proposal_id": p.id, "operation": p.operation, "digest": p.digest,
            "status": p.status, "expires_at": p.expires_at.isoformat()}


@router.put("/{name}/activate")
async def activate_prompt(name: str, body: PromptActivateBody,
                          actor: Actor = Depends(csrf_protected),
                          svc: Services = Depends(get_services)) -> dict:
    p = _svc(svc).propose_activate(
        actor, name, version=body.version, reason=body.reason,
        rollback=body.rollback, expires_in_minutes=body.expires_in_minutes,
    )
    svc.session.commit()
    return {"proposal_id": p.id, "operation": p.operation, "digest": p.digest,
            "status": p.status, "expires_at": p.expires_at.isoformat()}


@router.post("/{name}/disable")
async def disable_prompt(name: str, body: PromptReasonBody,
                         actor: Actor = Depends(csrf_protected),
                         svc: Services = Depends(get_services)) -> dict:
    p = _svc(svc).propose_disable(
        actor, name, reason=body.reason, rollback=body.rollback,
        expires_in_minutes=body.expires_in_minutes,
    )
    svc.session.commit()
    return {"proposal_id": p.id, "operation": p.operation, "digest": p.digest,
            "status": p.status, "expires_at": p.expires_at.isoformat()}


@router.post("/{name}/apply")
async def apply_prompt_proposal(name: str, body: PromptApplyBody,
                                actor: Actor = Depends(csrf_protected),
                                svc: Services = Depends(get_services)) -> dict:
    result = _svc(svc).apply_approved(actor, body.proposal_id, body.digest)
    svc.session.commit()
    return result
