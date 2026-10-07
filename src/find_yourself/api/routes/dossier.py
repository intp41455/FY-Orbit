"""任务档案库与企业模式 HTTP 接口（P15 · A-三重模式-03 / A-上下文持久化-02/03）。

* ``GET  /api/dossier/tasks/{task_id}/archive``      — 档案全景（读模型 + 来源标注）
* ``GET  /api/dossier/tasks/{task_id}/briefing``     — 「翻档案」一秒上手（有字数上限）
* ``GET  /api/dossier/tasks/{task_id}/retrospective``— 任务复盘报告
* ``POST /api/dossier/tasks/{task_id}/distill``      — 经验沉淀成可复用模板
* ``GET  /api/dossier/knowledge``                    — 本 owner 沉淀过的知识模板
* ``GET  /api/dossier/knowledge/{knowledge_id}``     — 知识模板详情
* ``GET  /api/dossier/enterprise``                   — 企业模式适配器目录 + 治理声明
* ``POST /api/dossier/enterprise/{target}/adapt``     — 外部框架定义 → 内部单循环规格
* ``POST /api/dossier/enterprise/{target}/mappings``  — 增补企业自有字段映射
* ``GET  /api/dossier/enterprise/{target}/onboarding`` — 企业接入文档

范式同 ``api/routes/kanban.py``：读用 ``get_actor``，落盘/改状态用 ``csrf_protected``；
body 不接受 ``owner_id``/``role``（BUG-03）；错误走 DomainError 信封。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ...services.dossier.archive import DEFAULT_BRIEFING_LIMIT, TaskArchive
from ...services.dossier.enterprise import EnterpriseAdapter
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/dossier", tags=["dossier"])


def _archive(svc: Services) -> TaskArchive:
    return TaskArchive(svc.session, audit=svc.audit)


def _enterprise(svc: Services) -> EnterpriseAdapter:
    return EnterpriseAdapter(audit=svc.audit)


class DistillBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list)


class MappingItem(BaseModel):
    """一条映射。``from`` 是 Python 关键字，用别名暴露（``populate_by_name`` 允许两种写法）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_path: str = Field(alias="from", min_length=1, max_length=200)
    to: str = Field(min_length=1, max_length=200)
    via: str = Field(default="identity", max_length=40)


class MappingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[MappingItem] = Field(min_length=1)


class AdaptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external: dict[str, Any]
    overrides: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 任务档案库（读）
# --------------------------------------------------------------------------- #
@router.get("/tasks/{task_id}/archive")
async def dossier_archive(task_id: str, actor: Actor = Depends(get_actor),
                          svc: Services = Depends(get_services)) -> dict[str, Any]:
    """档案全景：目标 / 里程碑 / 分工 / 决策 / 产出版本 / 依赖 + 每个字段的来源。"""
    return _archive(svc).archive(actor, task_id)


@router.get("/tasks/{task_id}/briefing")
async def dossier_briefing(
    task_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    limit: int = Query(default=DEFAULT_BRIEFING_LIMIT, ge=200, le=20000),
) -> dict[str, Any]:
    """给新加入 Agent 的可粘贴简报；超限明确标注截断（不假装是全量）。"""
    return _archive(svc).briefing(actor, task_id, limit=limit)


@router.get("/tasks/{task_id}/retrospective")
async def dossier_retro(task_id: str, actor: Actor = Depends(get_actor),
                        svc: Services = Depends(get_services)) -> dict[str, Any]:
    """任务复盘：时间线 + 决策 + 指标 + 经验条目（每条可追到来源）。"""
    return _archive(svc).retrospective(actor, task_id)


@router.post("/tasks/{task_id}/distill")
async def dossier_distill(
    task_id: str,
    body: DistillBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """把复盘沉淀成可复用知识模板（落盘），下次类似任务直接套。"""
    out = _archive(svc).distill(actor, task_id, name=body.name, tags=body.tags)
    svc.session.commit()
    return out


# --------------------------------------------------------------------------- #
# 知识沉淀
# --------------------------------------------------------------------------- #
@router.get("/knowledge")
async def dossier_knowledge_list(actor: Actor = Depends(get_actor),
                                 svc: Services = Depends(get_services)) -> dict[str, Any]:
    return _archive(svc).list_knowledge(actor)


@router.get("/knowledge/{knowledge_id}")
async def dossier_knowledge_detail(knowledge_id: str, actor: Actor = Depends(get_actor),
                                   svc: Services = Depends(get_services)) -> dict[str, Any]:
    return _archive(svc).knowledge_detail(actor, knowledge_id)


# --------------------------------------------------------------------------- #
# 企业模式（A-三重模式-03）
# --------------------------------------------------------------------------- #
@router.get("/enterprise")
async def enterprise_catalog(actor: Actor = Depends(get_actor),
                             svc: Services = Depends(get_services)) -> dict[str, Any]:
    """适配器目录 + **权限复用声明**（不另起多租户）。"""
    return _enterprise(svc).catalog(actor)


@router.post("/enterprise/{target}/adapt")
async def enterprise_adapt(
    target: str,
    body: AdaptBody,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """外部框架定义 → 内部单循环规格。未映射字段全部保留并报告，不静默丢弃。"""
    out = _enterprise(svc).adapt(actor, target, body.external, overrides=body.overrides)
    svc.session.commit()
    return out


@router.post("/enterprise/{target}/mappings")
async def enterprise_register_mappings(
    target: str,
    body: MappingsBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """增补企业自有字段映射（声明式，不改内核）。"""
    mappings = [{"from": m.from_path, "to": m.to, "via": m.via} for m in body.mappings]
    out = _enterprise(svc).register_mapping(actor, target, mappings)
    svc.session.commit()
    return out


@router.get("/enterprise/{target}/onboarding")
async def enterprise_onboarding(target: str, actor: Actor = Depends(get_actor),
                                svc: Services = Depends(get_services)) -> dict[str, Any]:
    """企业接入文档（验收：有企业接入文档）。"""
    return _enterprise(svc).onboarding_doc(actor, target)


__all__ = ["router"]
