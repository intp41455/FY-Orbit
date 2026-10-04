"""多人协作 HTTP 接口（需求 15 第一切片：评论 / @人 / 通知 / 角色）。

挂在 ``/api/collaboration`` 下：

* ``POST   /api/collaboration/roles``            —— 授予协作角色（owner/admin）
* ``POST   /api/collaboration/roles/revoke``     —— 撤销协作角色
* ``GET    /api/collaboration/roles``            —— 某条 record 的角色名册
* ``POST   /api/collaboration/comments``         —— 发表评论（含 @人 → 通知）
* ``GET    /api/collaboration/comments``         —— 某条 record 的评论（分页/排序）
* ``PATCH  /api/collaboration/comments/{id}``    —— 编辑自己的评论
* ``DELETE /api/collaboration/comments/{id}``    —— 删除（作者或 owner/admin，联动失效通知）
* ``GET    /api/collaboration/notifications``    —— 我的通知
* ``GET    /api/collaboration/notifications/unread-count`` —— 我的未读数（不含正文）
* ``POST   /api/collaboration/notifications/{id}/read`` —— 标记已读

写操作一律 ``csrf_protected``；全部授权在服务层做（复用 ``grant.py``，见
:mod:`find_yourself.services.collaboration`）。路由本身**不**做授权判定。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...db.types import utcnow
from ...services.actor import Actor
from ...services.collaboration import CollaborationService
from ...services.errors import DomainError, ValidationFailed
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/collaboration", tags=["collaboration"])


class AssignRoleBody(BaseModel):
    record_kind: str = Field(min_length=1, max_length=16)
    record_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=200)
    role: str = Field(default="viewer")
    expires_at: datetime | None = None
    expires_in_days: float | None = Field(default=None, gt=0, le=30)


class RevokeRoleBody(BaseModel):
    record_kind: str = Field(min_length=1, max_length=16)
    record_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=200)


class CommentBody(BaseModel):
    record_kind: str = Field(min_length=1, max_length=16)
    record_id: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1)


class EditCommentBody(BaseModel):
    body: str = Field(min_length=1)


def _svc(svc: Services) -> CollaborationService:
    return CollaborationService(svc.session, svc.audit, grants=svc.grants)


def _translate(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


def _resolve_expiry(body: AssignRoleBody) -> datetime:
    if body.expires_at is not None:
        return body.expires_at
    if body.expires_in_days is not None:
        return utcnow() + timedelta(days=body.expires_in_days)
    raise ValidationFailed("role_expiry", "Provide expires_at or expires_in_days (<=30)")


# ----------------------------------------------------------------------
# 角色
# ----------------------------------------------------------------------
@router.post("/roles", status_code=201)
async def assign_role(
    body: AssignRoleBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        view = _svc(svc).assign_role(
            actor,
            record_kind=body.record_kind,
            record_id=body.record_id,
            user_id=body.user_id,
            role=body.role,
            expires_at=_resolve_expiry(body),
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.post("/roles/revoke")
async def revoke_role(
    body: RevokeRoleBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        view = _svc(svc).revoke_role(
            actor, record_kind=body.record_kind, record_id=body.record_id, user_id=body.user_id
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.get("/roles")
async def list_roles(
    record_kind: str,
    record_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        items = _svc(svc).list_roles(actor, record_kind=record_kind, record_id=record_id)
    except DomainError as exc:
        raise _translate(exc) from exc
    return {"count": len(items), "items": items}


# ----------------------------------------------------------------------
# 评论
# ----------------------------------------------------------------------
@router.post("/comments", status_code=201)
async def add_comment(
    body: CommentBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        result = _svc(svc).add_comment(
            actor, record_kind=body.record_kind, record_id=body.record_id, body=body.body
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return result


@router.get("/comments")
async def list_comments(
    record_kind: str,
    record_id: str,
    limit: int | None = None,
    offset: int = 0,
    order: str = "asc",
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """分页列出评论。``limit`` 默认有界（≤200）；隔离在服务层 WHERE 内完成。"""
    try:
        page = _svc(svc).list_comments_page(
            actor, record_kind=record_kind, record_id=record_id,
            limit=limit, offset=offset, order=order,
        )
    except DomainError as exc:
        raise _translate(exc) from exc
    return page


@router.patch("/comments/{comment_id}")
async def edit_comment(
    comment_id: str,
    body: EditCommentBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """编辑评论。**仅作者本人**可改（服务层校验）。"""
    try:
        result = _svc(svc).edit_comment(actor, comment_id, body.body)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return result


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """删除评论。作者本人，或 owner/admin（``delete_any``）。"""
    try:
        view = _svc(svc).delete_comment(actor, comment_id)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


# ----------------------------------------------------------------------
# 通知
# ----------------------------------------------------------------------
@router.get("/notifications")
async def list_notifications(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    items = _svc(svc).list_notifications(actor)
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.get("/notifications/unread-count")
async def unread_count(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """当前身份的未读通知数。返回一个整数，不含任何通知正文。"""
    count = _svc(svc).unread_count(actor)
    svc.session.commit()
    return {"unread": count}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        view = _svc(svc).mark_notification_read(actor, notification_id)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view
