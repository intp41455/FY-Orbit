"""开箱模板 HTTP 接口（P13 · A-开箱模板-02/03/05/07/08/09）。

* ``GET  /api/templates/schema``                     — 冻结 schema 契约（P12/P16 消费）
* ``GET  /api/templates/layers``                     — 三层供给（新手 / 进阶 / 技术）
* ``GET  /api/templates/quality-tiers``              — 出厂质量档位（W7）
* ``GET  /api/templates``                            — 模板列表（含「一眼可见」构成摘要）
* ``GET  /api/templates/{template_id}``              — 模板详情（必备项默认值 + 说明）
* ``POST /api/templates/{template_id}/instantiate``  — 实例化（无空必填项，缺项明确列出）
* ``POST /api/templates/{template_id}/controller-check`` — 总控提示词体检（警告但不阻断）
* ``POST /api/templates/{template_id}/restore-factory``  — 一键恢复出厂
* ``POST /api/templates/{template_id}/expand-to-code``   — 展开为代码（复用既有通道）
* ``POST /api/templates/import-code``                — 代码编辑后另存为新模板
* ``POST /api/templates/manual-quality``             — 手册质量验收（W8）

范式与 ``api/routes/kanban.py`` 一致：读用 ``get_actor``，改用 ``csrf_protected``；
body 一律不接受 ``owner_id``/``role``（BUG-03）；错误统一走 DomainError 信封。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ...services.templates.scaffold import (
    QUALITY_TIERS,
    ScaffoldTemplateService,
)
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _svc(svc: Services) -> ScaffoldTemplateService:
    return ScaffoldTemplateService(audit=svc.audit, session=svc.session)


class InstantiateBody(BaseModel):
    """实例化请求体。

    ``overrides`` 只允许八类必备项 + ``controller_prompt`` / ``member_prompts``
    （服务层再校验一次，未知键 422）——请求体不能借此提升权限或塞入任意字段。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    tier: str = Field(default="novice")
    overrides: dict[str, Any] = Field(default_factory=dict)


class ControllerCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_prompt: str = Field(min_length=1, max_length=20000)


class ImportCodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=400000)
    base_template_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=200)


class ManualQualityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1, max_length=2_000_000)


# ---------------------------------------------------------------------------
# 读：契约与目录（静态路径必须先于 /{template_id} 声明）
# ---------------------------------------------------------------------------
@router.get("/schema")
async def templates_schema(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """冻结的模板 schema（跨包锁四：P12 / P16 按此消费）。"""
    actor.require_authenticated()
    from ...services.templates.scaffold import template_schema

    return template_schema()


@router.get("/layers")
async def templates_layers(actor: Actor = Depends(get_actor),
                           svc: Services = Depends(get_services)) -> dict[str, Any]:
    """三层模板供给（需求 A-开箱模板-04）。"""
    return _svc(svc).layers()


@router.get("/quality-tiers")
async def templates_quality_tiers(actor: Actor = Depends(get_actor),
                                  svc: Services = Depends(get_services)) -> dict[str, Any]:
    """出厂质量档位（需求 A-开箱模板-08 / W7）。"""
    return _svc(svc).quality_tiers()


@router.get("")
async def templates_list(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    scenario: str | None = None,
    layer: str | None = None,
    tier: str = "novice",
) -> dict[str, Any]:
    """模板列表：每条带「一眼可见」的构成摘要（需求 -07①）。"""
    return _svc(svc).list_templates(actor, scenario=scenario, layer=layer, tier=tier)


@router.post("/manual-quality")
async def templates_manual_quality(
    body: ManualQualityBody,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """手册质量验收（需求 -09 / W8）：故障目录 ≥20 类、有可照做示例、零基础路径。"""
    return _svc(svc).manual_quality_report(actor, body.markdown)


@router.get("/manual-quality")
async def templates_manual_quality_from_path(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    path: str | None = None,
) -> dict[str, Any]:
    """从文件读手册并验收（未给 path 时读 env ``FY_MANUAL_PATH``；都没有则 404）。"""
    return _svc(svc).manual_quality_report(actor, None, path=path)


@router.post("/import-code")
async def templates_import_code(
    body: ImportCodeBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """代码编辑后另存为新模板（需求 -05②）。"""
    out = _svc(svc).import_code(
        actor, body.code, base_template_id=body.base_template_id, name=body.name,
    )
    svc.session.commit()
    return out


@router.get("/{template_id}")
async def templates_detail(
    template_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    tier: str = "novice",
    layer: str | None = None,
) -> dict[str, Any]:
    """模板详情：七项配置可见 + 八类必备项默认值与说明 + 结构问题清单。"""
    return _svc(svc).get_template(actor, template_id, tier=tier, layer=layer)


# ---------------------------------------------------------------------------
# 写：实例化 / 提示词体检 / 恢复出厂 / 展开为代码
# ---------------------------------------------------------------------------
@router.post("/{template_id}/instantiate")
async def templates_instantiate(
    template_id: str,
    body: InstantiateBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """按模板创建可运行系统规格（需求 -02 / -03）。"""
    out = _svc(svc).instantiate(
        actor, template_id, tier=body.tier, overrides=body.overrides, name=body.name,
    )
    svc.session.commit()
    return out


@router.post("/{template_id}/controller-check")
async def templates_controller_check(
    template_id: str,
    body: ControllerCheckBody,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """总控提示词体检：改坏禁行规则时给明确警告，但**不阻断保存**（需求 -02③）。"""
    return _svc(svc).check_controller_prompt_draft(actor, template_id, body.draft_prompt)


@router.post("/{template_id}/restore-factory")
async def templates_restore_factory(
    template_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
    tier: str = "novice",
) -> dict[str, Any]:
    """一键恢复出厂总控提示词与必备项默认值（需求 -02④ / -03③）。"""
    out = _svc(svc).restore_factory(actor, template_id, tier=tier)
    svc.session.commit()
    return out


@router.post("/{template_id}/expand-to-code")
async def templates_expand_to_code(
    template_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    tier: str = "novice",
) -> dict[str, Any]:
    """一键展开为代码进入技术模式（需求 -05①，复用既有画布↔代码通道）。"""
    out = _svc(svc).expand_to_code(actor, template_id, tier=tier)
    svc.session.commit()
    return out


@router.get("/{template_id}/example-run")
async def templates_example_run(
    template_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    tier: str = "novice",
) -> dict[str, Any]:
    """「一键试跑」的规格面（需求 -07②）：返回内置示例任务，无需用户写提示词。

    ⚠️ 真正的执行由运行时承接（同一单循环内核）。本端点只交付**可执行的试跑
    规格 + 真实存在的示例任务**，不假装已经跑过；``executed`` 恒为 ``False``，
    以免前端把「拿到规格」当成「已经出结果」。
    """
    doc = _svc(svc).get_template(actor, template_id, tier=tier)
    example = doc.get("example_task") or {}
    return {
        "template_id": template_id,
        "quality_tier": tier,
        "example_task": example,
        "steps": (doc.get("overview", {}).get("estimate") or {}).get("steps"),
        "executed": False,
        "note": "试跑由运行时承接；本响应只提供示例任务与步数估算，未执行任何模型调用。",
    }


__all__ = ["router", "QUALITY_TIERS"]
