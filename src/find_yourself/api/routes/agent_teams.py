"""API routes for 19 单Agent内部团队与逐节点模型配置.

Every mutating owner route passes ``csrf_protected``; every read resolves the
actor server-side. Nothing here accepts ``owner_id``/``role`` from the body, and
no response contains a credential value — only ``credential_ref`` plus a
boolean saying whether it is configured.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/teams", tags=["agent-teams"])


class MemberSpec(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=200)
    agent_host: str = Field(default="find_yourself", max_length=64)
    provider_id: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=160)
    depends_on: list[str] = Field(default_factory=list)
    goal: str = Field(default="", max_length=1000)


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="system_managed")
    template_id: str | None = None
    members: list[MemberSpec] | None = None
    default_binding: dict[str, Any] | None = None
    role_bindings: dict[str, Any] | None = None
    budget_ref: dict[str, Any] | None = None
    permission_ref: dict[str, Any] | None = None
    canvas_instance_id: str | None = None
    root_task_id: str | None = None
    reason: str = Field(default="initial draft", max_length=500)


class UpdateTeamRequest(BaseModel):
    expected_version: int = Field(ge=1)
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)


class MemberBindingRequest(BaseModel):
    provider_id: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=160)
    params: dict[str, Any] | None = None
    expected_version: int | None = None
    reason: str = Field(default="node model override", max_length=500)


class RoleBindingRequest(BaseModel):
    provider_id: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=160)
    expected_version: int | None = None
    reason: str = Field(default="role default model", max_length=500)


class StartTeamRequest(BaseModel):
    expected_version: int = Field(ge=1)
    root_task_id: str | None = None


class ControlRequestBody(BaseModel):
    operation: str = Field(min_length=1, max_length=32)
    agent_instance_id: str | None = None
    role: str | None = None
    idempotency_key: str | None = None
    expected_version: int | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)


class GoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=1000)
    reason: str = Field(default="goal changed", max_length=500)


class ReserveRequest(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    amount_usd: float = Field(gt=0.0, le=1.0)


class ExecuteRequest(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    run_batch: int | None = None


class ResultRequest(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    run_batch: int = Field(ge=1)
    output: str = Field(default="", max_length=20000)
    status: str = Field(default="completed", max_length=32)
    evidence_refs: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Catalogs (read)
# ----------------------------------------------------------------------
@router.get("/catalog")
async def team_catalog(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return {
        "templates": svc.teams.list_templates(),
        "hosts": svc.teams.probe_hosts(actor),
        **svc.teams.list_models(actor),
    }


@router.get("/templates")
async def team_templates(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return {"items": svc.teams.list_templates()}


@router.get("/hosts")
async def team_hosts(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return {"items": svc.teams.probe_hosts(actor)}


@router.get("/models")
async def team_models(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.teams.list_models(actor)


# ----------------------------------------------------------------------
# Team drafts
# ----------------------------------------------------------------------
@router.get("")
async def list_teams(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rows = svc.teams.list_teams(actor)
    return {
        "items": [
            {
                "id": t.id, "name": t.name, "mode": t.mode, "state": t.state,
                "version": t.version, "plan_version": t.plan_version,
                "root_task_id": t.root_task_id,
                "member_roles": [m.get("role") for m in (t.members or [])],
            }
            for t in rows
        ],
        "count": len(rows),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_team(
    body: CreateTeamRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    members = None
    if body.members is not None:
        members = [m.model_dump() for m in body.members]
    team = svc.teams.create_team(
        actor,
        name=body.name,
        mode=body.mode,
        template_id=body.template_id,
        members=members,
        default_binding=body.default_binding,
        role_bindings=body.role_bindings,
        budget_ref=body.budget_ref,
        permission_ref=body.permission_ref,
        canvas_instance_id=body.canvas_instance_id,
        root_task_id=body.root_task_id,
        reason=body.reason,
    )
    svc.session.commit()
    return svc.teams.get_snapshot(actor, team.id)


@router.get("/{team_id}")
async def get_team(
    team_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    snap = svc.teams.get_snapshot(actor, team_id)
    svc.session.commit()
    return snap


@router.patch("/{team_id}")
async def update_team(
    team_id: str,
    body: UpdateTeamRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    svc.teams.update_team(
        actor, team_id, expected_version=body.expected_version,
        patch=body.patch, reason=body.reason,
    )
    svc.session.commit()
    return svc.teams.get_snapshot(actor, team_id)


class MemberRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)
    reason: str = Field(default="member rename", max_length=500)


@router.patch("/{team_id}/members/{role}/rename")
async def rename_team_member(
    team_id: str,
    role: str,
    body: MemberRenameRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """T2：成员重命名（双击改名）——只改 title，绑定/依赖/模型配置不动。

    版本并发沿用团队 PATCH 语义：版本不符 → 409；role 不存在 → 404 信封。
    """
    svc.teams.rename_member(
        actor, team_id, role=role, title=body.title,
        expected_version=body.expected_version, reason=body.reason,
    )
    svc.session.commit()
    return svc.teams.get_snapshot(actor, team_id)


@router.get("/{team_id}/validation")
async def validate_team(
    team_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    report = svc.teams.validate_start(actor, team_id)
    svc.session.commit()
    return report


@router.post("/{team_id}/start")
async def start_team(
    team_id: str,
    body: StartTeamRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    snap = svc.teams.start_team(
        actor, team_id, expected_version=body.expected_version,
        root_task_id=body.root_task_id,
    )
    svc.session.commit()
    return snap


# ----------------------------------------------------------------------
# Bindings
# ----------------------------------------------------------------------
@router.get("/{team_id}/members/{role}/binding")
async def resolve_binding(
    team_id: str,
    role: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    resolved = svc.teams.resolve_binding(actor, team_id, role)
    svc.session.commit()
    return resolved


@router.put("/{team_id}/members/{role}/binding")
async def set_member_binding(
    team_id: str,
    role: str,
    body: MemberBindingRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    resolved = svc.teams.set_member_binding(
        actor, team_id, role,
        provider_id=body.provider_id, model_id=body.model_id,
        params=body.params, expected_version=body.expected_version,
        reason=body.reason,
    )
    svc.session.commit()
    return resolved


@router.put("/{team_id}/roles/{role}/binding")
async def set_role_binding(
    team_id: str,
    role: str,
    body: RoleBindingRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    resolved = svc.teams.set_role_binding(
        actor, team_id, role, model_id=body.model_id,
        provider_id=body.provider_id, expected_version=body.expected_version,
        reason=body.reason,
    )
    svc.session.commit()
    return resolved


@router.post("/{team_id}/goal")
async def update_goal(
    team_id: str,
    body: GoalRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    result = svc.teams.update_goal(actor, team_id, body.goal, reason=body.reason)
    svc.session.commit()
    return result


# ----------------------------------------------------------------------
# Runtime control
# ----------------------------------------------------------------------
@router.post("/{team_id}/control")
async def control_team(
    team_id: str,
    body: ControlRequestBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    result = svc.teams.control(
        actor, team_id,
        operation=body.operation,
        agent_instance_id=body.agent_instance_id,
        role=body.role,
        idempotency_key=body.idempotency_key,
        expected_version=body.expected_version,
        scope=body.scope,
        reason=body.reason,
    )
    svc.session.commit()
    return result


@router.post("/{team_id}/budget/reserve")
async def reserve_member_budget(
    team_id: str,
    body: ReserveRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    result = svc.teams.reserve_member_budget(actor, team_id, body.role, body.amount_usd)
    svc.session.commit()
    return result


@router.post("/{team_id}/members/execute")
async def execute_member(
    team_id: str,
    body: ExecuteRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    result = svc.teams.execute_member(
        actor, team_id, body.role, prompt=body.prompt,
        max_tokens=body.max_tokens, run_batch=body.run_batch,
    )
    svc.session.commit()
    return result


@router.post("/{team_id}/members/result")
async def report_result(
    team_id: str,
    body: ResultRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    result = svc.teams.report_member_result(
        actor, team_id, body.role, run_batch=body.run_batch,
        output=body.output, status=body.status, evidence_refs=body.evidence_refs,
    )
    svc.session.commit()
    return result


# ----------------------------------------------------------------------
# Events (reconnect replay)
# ----------------------------------------------------------------------
@router.get("/{team_id}/events")
async def team_events(
    team_id: str,
    actor: Actor = Depends(get_actor),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.teams.get_events(actor, team_id, cursor=cursor, limit=limit)
    svc.session.commit()
    return {
        "items": items,
        "count": len(items),
        "cursor": cursor,
        "next_cursor": items[-1]["seq"] if items else cursor,
    }