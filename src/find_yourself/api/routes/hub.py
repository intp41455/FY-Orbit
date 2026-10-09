"""W6 超级中台适配器中心 API（连接 CRUD / 探活 / 能力清单 / 路由试算 / manifest 导入）。

安全与诚实约定
--------------
* 所有端点都要身份（``get_actor``）+ owner（中台连接是**个人**接入配置）；
  写操作还要 CSRF/Origin（``csrf_protected``）。
* owner 一律取 ``actor.owner_id``，请求体不接受 owner_id（FROZEN_CONTRACT §2）。
* 越权访问他人连接 = 403 ``hub_connection_forbidden``（任务书 §1.2 明文要求），
  错误体不含对方任何字段。
* 响应里的凭证**永远**是掩码（``public_connection`` 是唯一序列化入口）。
* 探活/调用失败如实返回 ``ok=false`` + 真实原因，绝不伪造成功。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.errors import ValidationFailed
from ...services.hub.connections import HubService, public_connection
from ...services.hub.manifest import EXAMPLE_MANIFEST, MANIFEST_SCHEMA
from ...services.hub.orchestrator import DEFAULT_MAX_WORKERS, Orchestrator
from ...services.hub.presets import public_presets
from ...services.hub.router import CapabilityRouter
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/hub", tags=["hub"])


def hub_service(svc: Services = Depends(get_services)) -> HubService:
    return HubService(svc.session)


def _owner(actor: Actor) -> Actor:
    """中台连接是个人配置：服务身份不能代管（防御性，业务层另有 owner 校验）。"""
    actor.require_owner()
    return actor


class CapabilityPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default="", max_length=300)
    # 中文别名/同义词：路由时走子串匹配，让中文任务描述能命中英文标签的能力。
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CredentialFieldPayload(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=60)
    secret: bool = True
    required: bool = False


class CreateConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=32)
    preset_id: str = Field(default="", max_length=48)
    icon: str = Field(default="", max_length=16)
    description: str = Field(default="", max_length=300)
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    secret_fields: list[str] = Field(default_factory=list, max_length=20)
    credential_fields: list[CredentialFieldPayload] = Field(default_factory=list, max_length=20)
    capabilities: list[CapabilityPayload] = Field(default_factory=list, max_length=50)
    manifest: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    preference: int = Field(default=0, ge=0, le=10)


class UpdateConnectionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    icon: str | None = Field(default=None, max_length=16)
    description: str | None = Field(default=None, max_length=300)
    config: dict[str, Any] | None = None
    credentials: dict[str, Any] | None = None
    capabilities: list[CapabilityPayload] | None = Field(default=None, max_length=50)
    preference: int | None = Field(default=None, ge=0, le=10)
    state: str | None = Field(default=None, max_length=24)


class InvokeRequest(BaseModel):
    action: str = Field(default="invoke", max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)


class RouteRequest(BaseModel):
    hint: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    kind: str | None = Field(default=None, max_length=32)
    include_unhealthy: bool = False


class ManifestImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    filename: str = Field(default="", max_length=200)
    name: str | None = Field(default=None, max_length=120)
    credentials: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 编排请求体
# --------------------------------------------------------------------------- #

class OrchestrateRequest(BaseModel):
    """多 Agent 协同编排（自动派单）。

    ``mode`` 三选一：
    ``sequential``  顺序执行，前一步输出拼进后一步上下文（写作/审校类）
    ``parallel``    并行执行同一任务的不同侧面，互不知情（多角度调研类）

    ``steps`` 是步骤描述列表；``aspects`` 是并行侧面列表。二者按 ``mode`` 取用。
    """

    task: str = Field(min_length=1, max_length=4_000)
    mode: str = Field(default="sequential", pattern=r"^(sequential|parallel)$")
    steps: list[str] = Field(default_factory=list, max_length=20)
    aspects: list[str] = Field(default_factory=list, max_length=20)
    kind: str | None = Field(default=None, max_length=32)
    max_workers: int = Field(default=DEFAULT_MAX_WORKERS, ge=1, le=8)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    summarize: bool = False
    summary_instruction: str = Field(default="把这些结果整合成一份结论。", max_length=500)


class PipelineRequest(BaseModel):
    """显式指定每步用哪个连接的编排。

    ``stages`` 每项可含 ``connection_id`` / ``capability`` / ``tool`` /
    ``instruction`` / ``params_extra``。``tool`` 只有「必须指名工具」的适配器
    （如 ``mcp_server``）需要——它只认 ``params["name"]``。
    """

    stages: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    summarize: bool = False
    summary_instruction: str = Field(default="把这些结果整合成一份结论。", max_length=500)


def _orchestrator(actor: Actor, hub: HubService, svc: Services, max_workers: int) -> Orchestrator:
    """构造**生产接线唯一正确入口**的编排器。

    必须走 :meth:`Orchestrator.bind` 而不是直接 ``Orchestrator(invoke=hub.invoke,...)``：
    真实签名是 ``invoke(actor, conn_id, ...)``，actor 在第一位，直接注入会让
    ``conn_id`` 错位到 ``actor`` 上，运行时必抛 ``TypeError``。
    """
    return Orchestrator.bind(
        hub, actor=actor, session=svc.session, owner_id=actor.owner_id,
        max_workers=max_workers,
    )


# --------------------------------------------------------------------------- #
# 预置 / manifest
# --------------------------------------------------------------------------- #

@router.get("/presets")
async def list_presets(actor: Actor = Depends(get_actor)) -> dict:
    _owner(actor)
    presets = public_presets()
    return {"presets": presets, "count": len(presets)}


@router.get("/manifest/example")
async def manifest_example(actor: Actor = Depends(get_actor)) -> dict:
    """可复制的 manifest 模板（供「下载模板」按钮使用）。"""
    _owner(actor)
    return {"schema": MANIFEST_SCHEMA, "example": EXAMPLE_MANIFEST}


@router.post("/manifest/import")
async def import_manifest(
    body: ManifestImportRequest,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    """注册一个声明式适配器（零代码）。缺必填凭证会停在 needs_credentials。"""
    _owner(actor)
    created = hub.import_manifest_connection(
        actor, text=body.text, filename=body.filename,
        credentials=body.credentials, name=body.name,
    )
    hub.s.commit()
    return {"connection": created}


# --------------------------------------------------------------------------- #
# 连接 CRUD
# --------------------------------------------------------------------------- #

@router.get("/connections")
async def list_connections(
    kind: str | None = Query(default=None, max_length=32),
    group: str | None = Query(default=None, max_length=16),
    actor: Actor = Depends(get_actor),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    rows = hub.list_connections(actor, kind=kind, group=group)
    # 两个键指向同一份数据：既有按 `connections` 读的调用方继续工作，按 `items`
    # 读的也不必再判空。改名会静默打断前者，所以宁可冗余。
    return {"connections": rows, "items": rows, "count": len(rows)}


@router.post("/connections")
async def create_connection(
    body: CreateConnectionRequest,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    payload = body.model_dump(exclude_none=True)
    payload["credential_fields"] = [f for f in body.credential_fields]
    conn = hub.create_connection(actor, payload)
    hub.s.commit()
    return {"connection": conn}


@router.get("/connections/{conn_id}")
async def get_connection(
    conn_id: str,
    actor: Actor = Depends(get_actor),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    return {"connection": hub.get_connection(actor, conn_id)}


@router.patch("/connections/{conn_id}")
async def update_connection(
    conn_id: str,
    body: UpdateConnectionRequest,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    conn = hub.update_connection(actor, conn_id, body.model_dump(exclude_none=True))
    hub.s.commit()
    return {"connection": conn}


@router.delete("/connections/{conn_id}")
async def delete_connection(
    conn_id: str,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    result = hub.delete_connection(actor, conn_id)
    hub.s.commit()
    return result


# --------------------------------------------------------------------------- #
# 探活 / 调用
# --------------------------------------------------------------------------- #

@router.post("/connections/{conn_id}/health-check")
async def health_check(
    conn_id: str,
    timeout_seconds: float = Query(default=3.0, ge=0.5, le=30.0),
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    result = hub.health_check(actor, conn_id, timeout_seconds=timeout_seconds)
    hub.s.commit()
    return result


@router.post("/connections/health-check-all")
async def health_check_all(
    timeout_seconds: float = Query(default=3.0, ge=0.5, le=30.0),
    limit: int = Query(default=50, ge=1, le=200),
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    """并发探活（asyncio.gather，每条最多 3s）；单条失败不影响整批。"""
    _owner(actor)
    results = await hub.health_check_all(actor, timeout_seconds=timeout_seconds, limit=limit)
    hub.s.commit()
    healthy = sum(1 for r in results if r["report"]["ok"])
    return {"results": results, "count": len(results), "healthy": healthy}


@router.post("/connections/{conn_id}/invoke")
async def invoke_connection(
    conn_id: str,
    body: InvokeRequest,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
) -> dict:
    """真实调用一次适配器。失败返回 200 + ok=false（调用本身成功了，目标没成功）。"""
    _owner(actor)
    result = hub.invoke(
        actor, conn_id, action=body.action, params=body.params,
        timeout_seconds=body.timeout_seconds,
    )
    hub.s.commit()
    return result


# --------------------------------------------------------------------------- #
# 能力清单 / 路由
# --------------------------------------------------------------------------- #

@router.get("/capabilities")
async def list_capabilities(
    kind: str | None = Query(default=None, max_length=32),
    actor: Actor = Depends(get_actor),
    hub: HubService = Depends(hub_service),
) -> dict:
    _owner(actor)
    rows = hub.capabilities(actor, kind=kind)
    return {"capabilities": rows, "count": len(rows)}


@router.post("/connections/{conn_id}/capabilities")
async def register_capability(
    conn_id: str,
    body: CapabilityPayload,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    _owner(actor)
    caps = CapabilityRouter(svc.session, actor.owner_id).register_capability(
        conn_id, body.model_dump()
    )
    svc.session.commit()
    return {"connection_id": conn_id, "capabilities": caps}


@router.delete("/connections/{conn_id}/capabilities/{capability_name}")
async def unregister_capability(
    conn_id: str,
    capability_name: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    _owner(actor)
    caps = CapabilityRouter(svc.session, actor.owner_id).unregister_capability(
        conn_id, capability_name
    )
    svc.session.commit()
    return {"connection_id": conn_id, "capabilities": caps}


@router.post("/route")
async def route(
    body: RouteRequest,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """路由试算：标签匹配 + 健康优先 + 用户偏好权重（v1 确定性，非 LLM 路由）。"""
    _owner(actor)
    ranked = CapabilityRouter(svc.session, actor.owner_id).route(
        body.hint, top_k=body.top_k, kind=body.kind,
        include_unhealthy=body.include_unhealthy,
    )
    return {"hint": body.hint, "candidates": ranked, "count": len(ranked)}


# --------------------------------------------------------------------------- #
# 多 Agent 协同编排
# --------------------------------------------------------------------------- #

@router.post("/orchestrate")
async def orchestrate(
    body: OrchestrateRequest,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
    svc: Services = Depends(get_services),
) -> dict:
    """自动派单的多 Agent 协同编排。

    诚实约定：某步失败**如实**返回在 ``steps`` 里并置 ``ok=false``，不吞掉、不
    伪造成功；匹配不上能力就不派单（``step.error`` 说明原因）。
    """
    _owner(actor)
    orch = _orchestrator(actor, hub, svc, body.max_workers)
    items = body.aspects if body.mode == "parallel" else body.steps
    if not items:
        raise ValidationFailed(
            "hub_orchestrate_empty",
            f"mode={body.mode} 需要提供对应的 "
            f"{'aspects' if body.mode == 'parallel' else 'steps'} 列表",
        )
    if body.mode == "parallel":
        result = orch.run_parallel(
            body.task, items, kind=body.kind, max_workers=body.max_workers,
            timeout_seconds=body.timeout_seconds,
        )
    else:
        result = orch.run_sequential(
            body.task, items, kind=body.kind or "openai_chat",
            timeout_seconds=body.timeout_seconds,
        )
    if body.summarize:
        result = orch.summarize(
            result, instruction=body.summary_instruction, timeout_seconds=body.timeout_seconds,
        )
    hub.s.commit()
    return {"orchestration": result.to_public()}


@router.post("/orchestrate/pipeline")
async def orchestrate_pipeline(
    body: PipelineRequest,
    actor: Actor = Depends(csrf_protected),
    hub: HubService = Depends(hub_service),
    svc: Services = Depends(get_services),
) -> dict:
    """显式指定每步用哪个连接的编排（需要精确控制时用这个）。"""
    _owner(actor)
    orch = _orchestrator(actor, hub, svc, DEFAULT_MAX_WORKERS)
    result = orch.run_pipeline(body.stages, timeout_seconds=body.timeout_seconds)
    if body.summarize:
        result = orch.summarize(result, instruction=body.summary_instruction,
                                timeout_seconds=body.timeout_seconds)
    hub.s.commit()
    return {"orchestration": result.to_public()}


@router.get("/orchestrate/capabilities")
async def orchestrate_capabilities(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """编排前置体检：当前有几条连接、几项能力可选。

    编排要靠路由打分派单，一个 Agent 都没有时必然每步都失败。让前端先自查，
    比让用户跑完一轮拿到满屏 ``hub_no_agent`` 报错更诚实。
    """
    _owner(actor)
    # candidates() 返回的是「能力项」列表（一条连接可能有多项能力），
    # 所以连接数要去重；kind 同理。
    items = CapabilityRouter(svc.session, actor.owner_id).candidates(
        include_unhealthy=True,
    )
    conn_ids = {str(i.get("connection_id") or "") for i in items if i.get("connection_id")}
    kinds = sorted({str(i.get("kind") or "") for i in items if i.get("kind")})
    return {
        "agent_count": len(conn_ids),
        "capability_count": len(items),
        "kinds": kinds,
        "can_orchestrate": len(conn_ids) > 0,
        "max_workers": DEFAULT_MAX_WORKERS,
    }


__all__ = ["public_connection", "router"]
