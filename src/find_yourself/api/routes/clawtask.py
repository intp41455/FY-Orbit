"""任务可移植 HTTP 接口（P16 · A-任务可移植-03/04/05）。

* ``GET  /api/clawtask/schema``                       — ``.clawtask`` 冻结 schema
* ``POST /api/clawtask/docs/validate``                — 结构校验（返回全部问题）
* ``POST /api/clawtask/docs/serialize``               — 文档 → 单文件文本（默认可移植）
* ``POST /api/clawtask/docs/parse``                   — 文本 → 文档（摘要不符拒绝）
* ``POST /api/clawtask/tasks/{task_id}/export``       — 从真实任务行导出 ``.clawtask``
* ``GET  /api/clawtask/market``                       — 任务市场检索（本地目录 + 有界分页）
* ``POST /api/clawtask/market/publish``               — 上架任务模板
* ``GET  /api/clawtask/market/{item_id}``             — 条目详情
* ``POST /api/clawtask/market/{item_id}/import``      — 导入条目（校验摘要）
* ``DELETE /api/clawtask/market/{item_id}``           — 下架（仅上架者本人）
* ``GET  /api/clawtask/hibernations``                 — 我的冬眠包
* ``POST /api/clawtask/hibernations``                 — 封存（doc 或 task_id）
* ``GET  /api/clawtask/hibernations/{id}``            — 冬眠包 manifest
* ``POST /api/clawtask/hibernations/{id}/wake``       — 一键唤醒（先校验完整性）
* ``DELETE /api/clawtask/hibernations/{id}``          — 丢弃冬眠包

⚠️ **市场不含账号与支付**（``payments_supported`` 恒为 ``false``）——需求原文的
「挂市场卖 5 块」涉及交易，属合规敏感面，待主控确认后单独立项。

范式同 ``api/routes/kanban.py``：读用 ``get_actor``，改用 ``csrf_protected``；
body 不接受 ``owner_id``/``role``（BUG-03）；错误走 DomainError 信封。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ...services.clawtask.format import (
    clawtask_schema,
    from_task,
    parse,
    serialize,
    validate_clawtask,
    verify_integrity,
)
from ...services.clawtask.hibernate import HibernationStore, hibernation_policy
from ...services.clawtask.market import ClawTaskMarket
from ...services.errors import NotFound
from ...services.templates.scaffold import ScaffoldTemplateService
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/clawtask", tags=["clawtask"])


def _market(svc: Services) -> ClawTaskMarket:
    return ClawTaskMarket(audit=svc.audit)


def _hibernation(svc: Services) -> HibernationStore:
    return HibernationStore(audit=svc.audit)


def _templates(svc: Services) -> ScaffoldTemplateService:
    return ScaffoldTemplateService(audit=svc.audit, session=svc.session)


class DocBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: dict[str, Any]


class SerializeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: dict[str, Any]
    portable: bool = True


class ParseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=4_000_000)
    verify: bool = True


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: dict[str, Any]
    version: str = Field(default="1.0.0", max_length=40)


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(default="development-pipeline", max_length=200)
    tier: str = Field(default="novice")
    next_action: str = Field(default="", max_length=2000)
    target_models: list[str] | None = None


class HibernateBody(BaseModel):
    """封存请求：给 ``doc`` 就直接封，给 ``task_id`` 就从真实任务行现构。"""

    model_config = ConfigDict(extra="forbid")

    doc: dict[str, Any] | None = None
    task_id: str | None = Field(default=None, max_length=200)
    template_id: str = Field(default="development-pipeline", max_length=200)
    tier: str = Field(default="novice")
    reason: str = Field(min_length=1, max_length=200)
    artifacts: list[str] = Field(default_factory=list)
    budget_percent: float | None = Field(default=None, ge=0, le=200)


# ---------------------------------------------------------------------------
# 契约与文档工具
# ---------------------------------------------------------------------------
@router.get("/schema")
async def clawtask_schema_endpoint(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    actor.require_authenticated()
    return clawtask_schema()


@router.post("/docs/validate")
async def clawtask_validate(body: DocBody,
                            actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """校验文档，返回**全部**问题（合法时 ``ok=true`` 且 ``problems=[]``）。

    对**草稿**（尚未封存）不报 ``integrity_missing``——那是序列化时才会由
    :func:`seal` 补齐的；只有文档真的带了 ``integrity`` 段才校验它。
    """
    actor.require_authenticated()
    problems = validate_clawtask(body.doc, check_integrity=False)
    if "integrity" in body.doc:
        problems += verify_integrity(body.doc)
    return {"ok": not problems, "problems": problems}


@router.post("/docs/serialize")
async def clawtask_serialize(body: SerializeBody,
                             actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """文档 → 单文件文本。``portable=True``（默认）剥离本机指针，便于分享。"""
    actor.require_authenticated()
    return serialize(body.doc, portable=body.portable)


@router.post("/docs/parse")
async def clawtask_parse(body: ParseBody,
                         actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """文本 → 文档。``verify=True``（默认）时摘要不符直接 422。"""
    actor.require_authenticated()
    return {"doc": parse(body.text, verify=body.verify)}


@router.post("/tasks/{task_id}/export")
async def clawtask_export(
    task_id: str,
    body: ExportBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """从真实任务行导出 ``.clawtask``（只搬运真实字段，不填料）。"""
    from ...db.models import Task

    task = svc.session.get(Task, task_id)
    if task is None or task.owner_id != actor.owner_id:
        raise NotFound("task_not_found", f"任务不存在：{task_id}")
    template = _templates(svc).get_template(actor, body.template_id, tier=body.tier)
    doc = from_task(
        actor, task, system_template=template,
        target_models=body.target_models, next_action=body.next_action,
    )
    exported = serialize(doc, portable=True)
    svc.session.commit()
    return {"doc": doc, "export": exported}


# ---------------------------------------------------------------------------
# 任务市场（本地目录式；无账号/支付）
# ---------------------------------------------------------------------------
@router.get("/market")
async def market_list(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    query: str = "",
    template_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _market(svc).list_items(actor, query=query, template_id=template_id,
                                   limit=limit, offset=offset)


@router.post("/market/publish")
async def market_publish(
    body: PublishBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """上架任务模板（``kind`` 必须是 ``task_template``）。"""
    out = _market(svc).publish(actor, body.doc, version=body.version)
    svc.session.commit()
    return out


@router.get("/market/{item_id}")
async def market_item(item_id: str, actor: Actor = Depends(get_actor),
                      svc: Services = Depends(get_services)) -> dict[str, Any]:
    return _market(svc).get_item(actor, item_id)


@router.post("/market/{item_id}/import")
async def market_import(item_id: str, actor: Actor = Depends(csrf_protected),
                        svc: Services = Depends(get_services)) -> dict[str, Any]:
    """导入市场条目 → 任务模板文档（可直接交给运行时跑）。"""
    out = _market(svc).import_item(actor, item_id)
    svc.session.commit()
    return out


@router.delete("/market/{item_id}")
async def market_unpublish(item_id: str, actor: Actor = Depends(csrf_protected),
                           svc: Services = Depends(get_services)) -> dict[str, Any]:
    out = _market(svc).unpublish(actor, item_id)
    svc.session.commit()
    return out


# ---------------------------------------------------------------------------
# 冬眠机制
# ---------------------------------------------------------------------------
@router.get("/hibernation-policy")
async def hibernation_policy_endpoint(
    actor: Actor = Depends(get_actor),
    budget_percent: float | None = None,
    model_failed: bool = False,
    user_requested: bool = False,
) -> dict[str, Any]:
    """临界判定策略（80% 预警 / 95% 强制建议）；只给建议，不静默停机。"""
    actor.require_authenticated()
    return hibernation_policy(budget_percent=budget_percent, model_failed=model_failed,
                              user_requested=user_requested)


@router.get("/hibernations")
async def hibernations_list(actor: Actor = Depends(get_actor),
                            svc: Services = Depends(get_services)) -> dict[str, Any]:
    return _hibernation(svc).list_hibernations(actor)


@router.post("/hibernations")
async def hibernations_create(
    body: HibernateBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """封存：``doc`` 直封，或从 ``task_id`` 现构后封。"""
    doc = body.doc
    if doc is None:
        if not body.task_id:
            raise NotFound("hibernate_source_required", "需要提供 doc 或 task_id")
        from ...db.models import Task

        task = svc.session.get(Task, body.task_id)
        if task is None or task.owner_id != actor.owner_id:
            raise NotFound("task_not_found", f"任务不存在：{body.task_id}")
        template = _templates(svc).get_template(actor, body.template_id, tier=body.tier)
        doc = from_task(actor, task, system_template=template,
                        local_only={"artifacts_dir": str(_hibernation(svc).directory)})
    out = _hibernation(svc).hibernate(actor, doc, reason=body.reason,
                                     artifacts=body.artifacts,
                                     budget_percent=body.budget_percent)
    svc.session.commit()
    return out


@router.get("/hibernations/{hibernation_id}")
async def hibernations_inspect(hibernation_id: str, actor: Actor = Depends(get_actor),
                               svc: Services = Depends(get_services)) -> dict[str, Any]:
    return _hibernation(svc).inspect(actor, hibernation_id)


@router.post("/hibernations/{hibernation_id}/wake")
async def hibernations_wake(hibernation_id: str, actor: Actor = Depends(csrf_protected),
                            svc: Services = Depends(get_services)) -> dict[str, Any]:
    """一键唤醒（先把整包完整性校验过一遍，任何一份被改动就拒绝）。"""
    out = _hibernation(svc).wake(actor, hibernation_id)
    svc.session.commit()
    return out


@router.delete("/hibernations/{hibernation_id}")
async def hibernations_discard(hibernation_id: str, actor: Actor = Depends(csrf_protected),
                               svc: Services = Depends(get_services)) -> dict[str, Any]:
    out = _hibernation(svc).discard(actor, hibernation_id)
    svc.session.commit()
    return out


__all__ = ["router"]
