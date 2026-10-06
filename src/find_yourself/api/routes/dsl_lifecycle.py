"""DSL 画布流程生命周期 HTTP 接口（A-画布搭建器-05/06，补齐包5）。

双形态 + 版本化 + 批量评估：

* ``POST /api/dsl-lifecycle/flows``                       —— 创建流程（chatflow/workflow）
* ``GET  /api/dsl-lifecycle/flows``                       —— 我的流程列表
* ``GET  /api/dsl-lifecycle/flows/{id}``                  —— 取流程（含草稿/已发布文档）
* ``PUT  /api/dsl-lifecycle/flows/{id}/draft``            —— 保存草稿（可显式切换 flow_type）
* ``POST /api/dsl-lifecycle/flows/{id}/publish``          —— 发布（装配校验 + 版本快照）
* ``GET  /api/dsl-lifecycle/flows/{id}/versions``         —— 发布历史
* ``GET  /api/dsl-lifecycle/flows/{id}/versions/{v}``     —— 取历史版本文档
* ``POST /api/dsl-lifecycle/flows/{id}/rollback``         —— 回滚（历史版本取回草稿）
* ``POST /api/dsl-lifecycle/flows/{id}/evaluate-batch``   —— CSV 批量评估（接 UnifiedEvaluator）
* ``POST /api/dsl-lifecycle/validate-assembly``           —— 装配完整性 + 触发器/入口语义校验

节点级调试 API（``POST /api/dsl/runs/{run_id}/step`` 等）由包4 施工，不在本文件。
批量评估对 :class:`~find_yourself.runtime.evaluation.UnifiedEvaluator` 的调用是
该评测器的**产品接线点**（此前无人 import）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import Services, csrf_protected, get_actor, get_services, get_settings
from ...config import Settings
from ...services.actor import Actor
from ...services.dsl_canvas import (
    FLOW_TYPES,
    DslFlowStore,
    DslValidationError,
    evaluate_flow_batch,
    validate_assembly,
)
from ...services.errors import DomainError

router = APIRouter(prefix="/api/dsl-lifecycle", tags=["dsl-lifecycle"])


def _translate(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


def _store(svc: Services) -> DslFlowStore:
    """流程生命周期 store（svc.session 注入；不扩共享 Services 容器，避免并行包冲突）。"""
    return DslFlowStore(svc.session)


class FlowCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    flow_type: str = "workflow"
    doc: dict[str, Any] | None = None


class DraftBody(BaseModel):
    doc: dict[str, Any]
    #: 显式传入时切换形态（chatflow ⇄ workflow）。
    flow_type: str | None = None


class PublishBody(BaseModel):
    note: str = Field(default="", max_length=200)


class RollbackBody(BaseModel):
    version: int = Field(ge=1)


class BatchEvalBody(BaseModel):
    #: CSV 文本（表头须含 input；可选 case_id/expected/category）。
    csv: str = Field(min_length=1)
    runs_per_case: int = Field(default=1, ge=1, le=5)
    #: 解析器开关：按需注入真实副作用（llm 走 provider 网关、knowledge 走
    #: 现有检索、tool 走工具注册表）。默认全关——无解析器的节点诚实失败。
    with_llm: bool = False
    with_knowledge: bool = False
    with_tools: bool = False


class AssemblyBody(BaseModel):
    doc: dict[str, Any]
    flow_type: str | None = None


def _build_runner_kwargs(svc: Services, actor: Actor, settings: Settings,
                         body: BatchEvalBody) -> dict[str, Any]:
    """按需把**现有公开服务**适配成 run_dsl 的解析器（不在 DSL 层重写它们）。"""
    kwargs: dict[str, Any] = {}
    if body.with_knowledge:
        from ...services.knowledge.search import KnowledgeSearchService

        search = KnowledgeSearchService(svc.session)

        def knowledge_resolver(query: str, params: dict[str, Any]) -> Any:
            return search.search(
                actor, owner_id=actor.owner_id or "", query=query,
                top_k=int(params.get("top_k") or 5),
                mode=params.get("mode"),
                document_ids=params.get("document_ids"))

        kwargs["knowledge_resolver"] = knowledge_resolver
    if body.with_tools:
        from ...services.tool_registry import tool_registry

        kwargs["tool_resolver"] = \
            lambda name, args, _params: tool_registry.invoke(name, args)
    if body.with_llm:
        from ...runtime.gateway import ModelGateway

        gateway = ModelGateway(settings, svc.budget)

        def llm_resolver(model: str, prompt: str, params: dict[str, Any]) -> Any:
            return gateway.complete(
                actor, task_id=f"dsl-eval-{actor.owner_id}",
                model=model, prompt=prompt,
                max_tokens=int(params.get("max_tokens") or 512),
                timeout_seconds=float(params.get("timeout_seconds") or 30.0))

        kwargs["llm_resolver"] = llm_resolver
    return kwargs


@router.post("/flows", status_code=201)
async def create_flow(body: FlowCreateBody,
                      actor: Actor = Depends(csrf_protected),
                      svc: Services = Depends(get_services)) -> dict:
    if body.flow_type not in FLOW_TYPES:
        raise HTTPException(status_code=422,
                            detail=f"flow_type 必须是 {FLOW_TYPES}")
    try:
        return _store(svc).create_flow(actor, name=body.name,
                                         flow_type=body.flow_type,
                                         doc=body.doc)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.get("/flows")
async def list_flows(actor: Actor = Depends(get_actor),
                     svc: Services = Depends(get_services)) -> dict:
    try:
        return {"items": _store(svc).list_flows(actor)}
    except DomainError as exc:
        raise _translate(exc) from exc


@router.get("/flows/{flow_id}")
async def get_flow(flow_id: str,
                   actor: Actor = Depends(get_actor),
                   svc: Services = Depends(get_services)) -> dict:
    try:
        return _store(svc).get_flow(actor, flow_id)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.put("/flows/{flow_id}/draft")
async def save_draft(flow_id: str, body: DraftBody,
                     actor: Actor = Depends(csrf_protected),
                     svc: Services = Depends(get_services)) -> dict:
    try:
        return _store(svc).save_draft(actor, flow_id, body.doc,
                                        flow_type=body.flow_type)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DomainError as exc:
        raise _translate(exc) from exc


@router.post("/flows/{flow_id}/publish")
async def publish_flow(flow_id: str, body: PublishBody,
                       actor: Actor = Depends(csrf_protected),
                       svc: Services = Depends(get_services)) -> dict:
    try:
        return _store(svc).publish(actor, flow_id, note=body.note)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DomainError as exc:
        raise _translate(exc) from exc


@router.get("/flows/{flow_id}/versions")
async def list_versions(flow_id: str,
                        actor: Actor = Depends(get_actor),
                        svc: Services = Depends(get_services)) -> dict:
    try:
        return {"items": _store(svc).list_versions(actor, flow_id)}
    except DomainError as exc:
        raise _translate(exc) from exc


@router.get("/flows/{flow_id}/versions/{version}")
async def get_version(flow_id: str, version: int,
                      actor: Actor = Depends(get_actor),
                      svc: Services = Depends(get_services)) -> dict:
    try:
        return _store(svc).get_version(actor, flow_id, version)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.post("/flows/{flow_id}/rollback")
async def rollback_flow(flow_id: str, body: RollbackBody,
                        actor: Actor = Depends(csrf_protected),
                        svc: Services = Depends(get_services)) -> dict:
    try:
        return _store(svc).rollback(actor, flow_id, body.version)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.post("/flows/{flow_id}/evaluate-batch")
async def evaluate_batch(flow_id: str, body: BatchEvalBody,
                         actor: Actor = Depends(csrf_protected),
                         svc: Services = Depends(get_services),
                         settings: Settings = Depends(get_settings)) -> dict:
    """CSV 批量评估：逐行喂**已发布**画布并汇总（成功/失败/时延）。

    优先用已发布文档；未发布但草稿存在时明确报错（评估对象必须是发布版，
    避免拿草稿当产品行为）。
    """
    flow = _store(svc).get_flow(actor, flow_id)
    doc = flow.get("published_doc")
    if not doc:
        raise HTTPException(
            status_code=409,
            detail="该流程尚未发布：批量评估只针对已发布版本（先 publish）")
    try:
        return evaluate_flow_batch(
            doc, body.csv, runs_per_case=body.runs_per_case,
            runner_kwargs=_build_runner_kwargs(svc, actor, settings, body))
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DomainError as exc:
        raise _translate(exc) from exc


@router.post("/validate-assembly")
async def validate_assembly_route(body: AssemblyBody,
                                  actor: Actor = Depends(get_actor)) -> dict:
    """装配完整性 + 触发器/入口语义校验（前端装配面板的权威判定）。"""
    try:
        return validate_assembly(body.doc, flow_type=body.flow_type)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
