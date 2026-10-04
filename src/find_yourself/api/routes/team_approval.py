"""团队级审批 HTTP 接口（需求 6）。

挂在 ``/api/team-approvals`` 下而**不**并进 ``/api/proposals``：
proposal 是**个人级**提案审批（owner 一个人拍板，FROZEN_CONTRACT §6），
团队审批的主体是「团队里另一个有资格的人」。两者的授权判据不同，混在一个
router 里会诱使后续代码把「谁能批」当成一个概念。

* ``POST   /api/team-approvals/teams``建组（建组人自动是 member）
* ``GET    /api/team-approvals/teams``                      —— 我所在的团队
* ``GET    /api/team-approvals/teams/{team_id}/members``     —— 成员名册
* ``POST   /api/team-approvals/teams/{team_id}/members``     —— 授予资格（仅审批人）
* ``POST   /api/team-approvals/teams/{team_id}/members/revoke`` —— 撤销资格
* ``POST   /api/team-approvals/requests``                    —— 提交申请（执行暂停）
* ``GET    /api/team-approvals/teams/{team_id}/requests``    —— 团队申请列表
* ``GET    /api/team-approvals/requests/{request_id}``       —— 单条申请
* ``POST   /api/team-approvals/requests/{request_id}/decision`` —— 拍板

写操作一律 ``csrf_protected``；拍板额外在服务层 ``require_owner()`` +
审批人资格校验 + 利益冲突回避，见 :mod:`find_yourself.services.team_approval`。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.errors import DomainError
from ...services.team_approval import TeamApprovalService
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/team-approvals", tags=["team-approval"])


class CreateTeamBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AddMemberBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    role: str = Field(default="member")


class RevokeMemberBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)


class SubmitRequestBody(BaseModel):
    team_id: str = Field(min_length=1, max_length=64)
    execution_id: str = Field(min_length=1, max_length=200)
    checkpoint: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    detail: dict[str, Any] = Field(default_factory=dict)
    required_role: str = Field(default="approver")
    supersedes_id: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)


class DecisionBody(BaseModel):
    decision: str = Field(min_length=1, max_length=64)
    note: str = ""
    resolution: dict[str, Any] | None = None


def _svc(svc: Services) -> TeamApprovalService:
    return TeamApprovalService(svc.session, svc.audit)


def _translate(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


# ----------------------------------------------------------------------
# 团队与成员
# ----------------------------------------------------------------------
@router.post("/teams", status_code=201)
async def create_team(
    body: CreateTeamBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        view = _svc(svc).create_team(actor, body.name)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.get("/teams")
async def list_teams(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    items = _svc(svc).list_teams(actor)
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.get("/teams/{team_id}/members")
async def list_members(
    team_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        items = _svc(svc).list_members(actor, team_id)
    except DomainError as exc:
        raise _translate(exc) from exc
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.post("/teams/{team_id}/members", status_code=201)
async def add_member(
    team_id: str,
    body: AddMemberBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """授予团队资格。**只有该团队的审批人**能调（服务层校验）。"""
    try:
        view = _svc(svc).add_member(actor, team_id, body.user_id, role=body.role)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.post("/teams/{team_id}/members/revoke")
async def revoke_member(
    team_id: str,
    body: RevokeMemberBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        view = _svc(svc).revoke_member(actor, team_id, body.user_id)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


# ----------------------------------------------------------------------
# 申请与拍板
# ----------------------------------------------------------------------
@router.post("/requests", status_code=201)
async def submit_request(
    body: SubmitRequestBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """提交申请。**执行同时被挂起**（复用 HITL 的暂停机制）。"""
    try:
        view = _svc(svc).submit(
            actor,
            body.team_id,
            execution_id=body.execution_id,
            checkpoint=body.checkpoint,
            title=body.title,
            detail=body.detail,
            required_role=body.required_role,
            supersedes_id=body.supersedes_id,
            timeout_seconds=body.timeout_seconds,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.get("/teams/{team_id}/requests")
async def list_requests(
    team_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    status: str | None = None,
) -> dict:
    try:
        items = _svc(svc).list_requests(actor, team_id, status=status)
    except DomainError as exc:
        raise _translate(exc) from exc
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.get("/requests/{request_id}")
async def get_request(
    request_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        return _svc(svc).get(actor, request_id)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.post("/requests/{request_id}/decision")
async def decide(
    request_id: str,
    body: DecisionBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """拍板。仅该团队的合格审批人可调，且不能审自己提交的申请。"""
    try:
        view = _svc(svc).decide(
            actor, request_id, body.decision,
            note=body.note, resolution=body.resolution,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view
