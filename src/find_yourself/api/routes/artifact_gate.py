"""产物版本门禁 HTTP 接口（需求 7）。

挂在 ``/api/artifact-gates`` 下。写操作一律 ``csrf_protected``；判定/放行
额外在服务层 ``require_owner()``——**执行体不能给自己放行**。

* ``POST /api/artifact-gates/versions``                   —— 登记新版本（内容冻结）
* ``GET  /api/artifact-gates/versions``                   —— 我可见的版本列表
* ``GET  /api/artifact-gates/versions/{id}``              —— 单个版本
* ``POST /api/artifact-gates/versions/{id}/submit``       —— 送检
* ``POST /api/artifact-gates/versions/{id}/checks``       —— 记录一条检查证据
* ``POST /api/artifact-gates/versions/{id}/independent-test`` —— 真跑一条命令并记录
* ``POST /api/artifact-gates/versions/{id}/decision``     —— 推进状态机
* ``GET  /api/artifact-gates/versions/{id}/checks``       —— 该版的全部证据
* ``GET  /api/artifact-gates/versions/{id}/gate``         —— 能不能用/能不能对外
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.artifact_gate import ArtifactGateService
from ...services.errors import DomainError
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/artifact-gates", tags=["artifact-gate"])


class RegisterBody(BaseModel):
    artifact_kind: str = Field(min_length=1, max_length=32)
    artifact_id: str = Field(min_length=1, max_length=200)
    workspace_dir: str = Field(min_length=1, max_length=1000)
    target_files: list[str] | None = None
    note: str = Field(default="", max_length=2000)


class SubmitBody(BaseModel):
    note: str = Field(default="", max_length=2000)


class CheckBody(BaseModel):
    check_name: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=16)
    observed_digest: str | None = Field(default=None, max_length=64)
    evidence: dict[str, Any] = Field(default_factory=dict)
    detail: str = Field(default="", max_length=2000)


class IndependentTestBody(BaseModel):
    command: list[str] | str
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    env: dict[str, str] | None = None


class DecisionBody(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=2000)


def _svc(svc: Services) -> ArtifactGateService:
    return ArtifactGateService(svc.session, svc.audit)


def _translate(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/versions", status_code=201)
async def register_version(
    body: RegisterBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """登记新版本。版号由服务端算，内容摘要由真实字节算出。"""
    try:
        view = _svc(svc).register(
            actor,
            artifact_kind=body.artifact_kind,
            artifact_id=body.artifact_id,
            workspace_dir=body.workspace_dir,
            target_files=body.target_files,
            note=body.note,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.get("/versions")
async def list_versions(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    artifact_kind: str | None = None,
    artifact_id: str | None = None,
    state: str | None = None,
) -> dict:
    try:
        items = _svc(svc).list_versions(
            actor, artifact_kind=artifact_kind, artifact_id=artifact_id, state=state
        )
    except DomainError as exc:
        raise _translate(exc) from exc
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.get("/versions/{version_id}")
async def get_version(
    version_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        return _svc(svc).get(actor, version_id)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.post("/versions/{version_id}/submit")
async def submit(
    version_id: str,
    body: SubmitBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        view = _svc(svc).submit(actor, version_id, note=body.note)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.post("/versions/{version_id}/checks", status_code=201)
async def record_check(
    version_id: str,
    body: CheckBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """记录一条检查证据。**只写证据，不改状态**——离放行还差一次 decision。"""
    try:
        view = _svc(svc).record_check(
            actor,
            version_id,
            body.check_name,
            status=body.status,
            observed_digest=body.observed_digest,
            evidence=body.evidence,
            detail=body.detail,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.post("/versions/{version_id}/independent-test")
async def run_independent_test(
    version_id: str,
    body: IndependentTestBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """真跑一条命令并把结果记成 ``independent_test`` 证据。"""
    try:
        view = _svc(svc).run_independent_test(
            actor,
            version_id,
            body.command,
            timeout_seconds=body.timeout_seconds,
            env=body.env,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.post("/versions/{version_id}/decision")
async def decide(
    version_id: str,
    body: DecisionBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """推进状态机。非法动作 422，且不改变任何状态。"""
    try:
        view = _svc(svc).decide(actor, version_id, body.action, note=body.note)
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.get("/versions/{version_id}/checks")
async def list_checks(
    version_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        items = _svc(svc).list_checks(actor, version_id)
    except DomainError as exc:
        raise _translate(exc) from exc
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.get("/versions/{version_id}/gate")
async def gate(
    version_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """门禁判定：能不能用/ 能不能对外，以及**为什么**。"""
    try:
        return _svc(svc).evaluate(actor, version_id)
    except DomainError as exc:
        raise _translate(exc) from exc
