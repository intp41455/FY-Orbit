"""包6 · A-云盘RAG-02/03 · RAG 方案模板 / 调试器 / A/B 对比 API + 图谱路由装配。

路由前缀统一在 ``/api/knowledge``（与图谱端点同族）；本文件同时承担
``api/routes/knowledge.py`` 中 ``graph_router`` 的装配点——routes/__init__ 的
约定式自动发现挂载本模块的 ``router``（无前缀组合器），图谱端点随之生效。

鉴权与 owner 隔离同 ``knowledge.py``：身份走 ``get_actor``，写操作加 CSRF
（``csrf_protected``）；owner 一律取 ``actor.owner_id``，不接受请求体指定。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.knowledge.rag_presets import RagPresetService
from ..deps import Services, csrf_protected, get_actor, get_services

# 图谱端点装配（定义在 knowledge.py，prefix=/api/knowledge）
from .knowledge import graph_router  # noqa: F401 — include 进组合路由即挂载

rag_router = APIRouter(prefix="/api/knowledge", tags=["rag-presets"])


def _preset_service(services: Services) -> RagPresetService:
    return RagPresetService(services.session, services.audit)


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #


class SavePresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    params: dict[str, Any] = Field(default_factory=dict)
    preset_id: str | None = Field(default=None, max_length=80)


class RagDebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    preset_id: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] | None = Field(default=None)
    top_k: int | None = Field(default=None, ge=1, le=50)
    document_ids: list[str] | None = Field(default=None, max_length=50)


class RagCompareRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    preset_ids: list[str] = Field(min_length=2, max_length=4)
    top_k: int | None = Field(default=None, ge=1, le=50)
    document_ids: list[str] | None = Field(default=None, max_length=50)


class SaveComparisonRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any]
    preset_ids: list[str] | None = Field(default=None, max_length=4)


# --------------------------------------------------------------------------- #
# 方案模板
# --------------------------------------------------------------------------- #


@rag_router.get("/rag/presets")
async def list_rag_presets(
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> dict:
    """内置 4 预设 + 用户保存方案（含预留参数位说明）。"""
    actor.require_authenticated()
    return _preset_service(services).list_presets(actor, owner_id=actor.owner_id)


@rag_router.post("/rag/presets")
async def save_rag_preset(
    body: SavePresetRequest,
    actor: Actor = Depends(csrf_protected),
    services: Services = Depends(get_services),
) -> dict:
    svc = _preset_service(services)
    result = svc.save_preset(
        actor,
        owner_id=actor.owner_id,
        name=body.name,
        params=body.params,
        description=body.description,
        preset_id=body.preset_id,
    )
    services.session.commit()
    return result


@rag_router.delete("/rag/presets/{preset_id}")
async def delete_rag_preset(
    preset_id: str,
    actor: Actor = Depends(csrf_protected),
    services: Services = Depends(get_services),
) -> dict:
    svc = _preset_service(services)
    result = svc.delete_preset(actor, owner_id=actor.owner_id, preset_id=preset_id)
    services.session.commit()
    return result


# --------------------------------------------------------------------------- #
# 调试器 / A/B 对比
# --------------------------------------------------------------------------- #


@rag_router.post("/rag/debug")
async def debug_rag(
    body: RagDebugRequest,
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> dict:
    """单方案调试：召回片段 / 分数 / 命中词 / 双路诊断 / 耗时（只读，不加 CSRF）。"""
    actor.require_authenticated()
    return _preset_service(services).run_debug(
        actor,
        owner_id=actor.owner_id,
        query=body.query,
        preset_id=body.preset_id,
        params=body.params,
        top_k=body.top_k,
        document_ids=body.document_ids,
    )


@rag_router.post("/rag/compare")
async def compare_rag(
    body: RagCompareRequest,
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> dict:
    """多方案同题对比（2..4 臂）：片段/分数/耗时 + 重合度矩阵。"""
    actor.require_authenticated()
    return _preset_service(services).compare(
        actor,
        owner_id=actor.owner_id,
        query=body.query,
        preset_ids=body.preset_ids,
        top_k=body.top_k,
        document_ids=body.document_ids,
    )


@rag_router.post("/rag/comparisons")
async def save_rag_comparison(
    body: SaveComparisonRequest,
    actor: Actor = Depends(csrf_protected),
    services: Services = Depends(get_services),
) -> dict:
    """保存一次 A/B 对比（payload 截断存档：每臂前 5 条、content 300 字）。"""
    svc = _preset_service(services)
    result = svc.save_comparison(
        actor,
        owner_id=actor.owner_id,
        query=body.query,
        payload=body.payload,
        preset_ids=body.preset_ids,
    )
    services.session.commit()
    return result


@rag_router.get("/rag/comparisons")
async def list_rag_comparisons(
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> dict:
    actor.require_authenticated()
    return _preset_service(services).list_comparisons(actor, owner_id=actor.owner_id)


@rag_router.get("/rag/comparisons/{run_id}")
async def get_rag_comparison(
    run_id: str,
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> dict:
    actor.require_authenticated()
    return _preset_service(services).get_comparison(
        actor, owner_id=actor.owner_id, run_id=run_id
    )


# --------------------------------------------------------------------------- #
# 组合路由：图谱端点（knowledge.graph_router）+ RAG 方案端点
# routes/__init__ 的约定式自动发现按模块级 ``router`` 挂载本组合。
# --------------------------------------------------------------------------- #

router = APIRouter()
router.include_router(graph_router)
router.include_router(rag_router)
