"""基座质保 HTTP 接口（P1 · A-基座质保-10/11/12/13/14）。

* ``GET  /api/quality/logs/levels``                  — 五级分级目录（可配置）
* ``GET  /api/quality/logs``                        — 五维筛选（级别/actor/界面/项目/序号）
* ``GET  /api/quality/logs/savepoints``             — 保存点清单（有真实时间戳）
* ``POST /api/quality/logs/export``                 — 一键导出排查包（先脱敏、可预览、进留痕）
* ``GET  /api/quality/logs/exports``                — 我导出过的排查包
* ``GET  /api/quality/logs/exports/{export_id}``    — 排查包详情（owner 隔离）
* ``GET  /api/quality/performance-budget``          — 预算门禁总览（五项 + 档位 + 本地自测）
* ``GET  /api/quality/performance-budget/tiers``    — 硬件档位
* ``GET  /api/quality/performance-budget/limits``   — 某档位下的有效阈值
* ``GET  /api/quality/performance-budget/self-test``— 本地自测计划（与 CI 同一份阈值）
* ``POST /api/quality/performance-budget/evaluate`` — 按档位判定样本，超限即给出失败与指标
* ``GET  /api/quality/base-contract``               — 接入契约（能力 / 声明 / 豁免规则）
* ``GET  /api/quality/base-contract/audit``         — 基座接入校验（CI 门禁判定）
* ``GET  /api/quality/base-contract/exemptions``    — 豁免清单
* ``POST /api/quality/base-contract/exemptions``    — 登记豁免（须写明理由）
* ``POST /api/quality/base-contract/exemptions/approve`` — 审批豁免（owner）
* ``POST /api/quality/base-contract/exemptions/revoke``  — 撤回豁免（owner）

范式同 ``api/routes/kanban.py``：读用 ``get_actor``，写/落盘用 ``csrf_protected``；
body 不接受 ``owner_id``/``role``（BUG-03）；错误统一走 DomainError 信封。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ...services.quality import budget as budget_svc
from ...services.quality.base_contract import BaseContractService, base_contract
from ...services.quality.logs import (
    MAX_PAGE_LIMIT,
    LogService,
    quality_dir,
)
from ...services.quality.self_test import SELF_TEST_COMMAND
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/quality", tags=["quality"])


def _logs(svc: Services) -> LogService:
    return LogService(svc.session, audit=svc.audit)


def _base(svc: Services) -> BaseContractService:
    return BaseContractService(svc.session, audit=svc.audit)


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #
class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str | None = Field(default=None, max_length=20)
    surface: str | None = Field(default=None, max_length=120)
    project: str | None = Field(default=None, max_length=120)
    since_seq: int | None = Field(default=None, ge=0)
    until_seq: int | None = Field(default=None, ge=0)
    preview: bool = True


class EvaluateBody(BaseModel):
    """性能样本。``samples`` 必须**逐项齐全**——缺项判失败，不静默跳过。"""

    model_config = ConfigDict(extra="forbid")

    tier: str = Field(default="standard", max_length=20)
    samples: dict[str, float] = Field(default_factory=dict)


class ExemptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=2000)
    scope: str = Field(default="pure_display", max_length=40)


class ExemptionDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=400)
    note: str | None = Field(default=None, max_length=2000)


# --------------------------------------------------------------------------- #
# A-基座质保-10 · 日志分级与可导出
# --------------------------------------------------------------------------- #
@router.get("/logs/levels")
async def quality_log_levels(actor: Actor = Depends(get_actor),
                             svc: Services = Depends(get_services)) -> dict[str, Any]:
    """五级分级目录：级别、token、是否可配置、与审计链的同源声明。"""
    return _logs(svc).levels()


@router.get("/logs/savepoints")
async def quality_log_savepoints(actor: Actor = Depends(get_actor),
                                 svc: Services = Depends(get_services),
                                 limit: int = Query(default=500, ge=1, le=2000)) -> dict[str, Any]:
    """保存点清单（带 ``created_at`` 真实时间戳；审计帧没有时间戳，见 levels 的说明）。"""
    items = _logs(svc).savepoints(actor, limit=limit)
    return {"items": items, "total": len(items), "time_axis": "wallclock",
            "source": "work_stashes（写前快照）"}


@router.get("/logs")
async def quality_logs_query(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    level: str | None = Query(default=None),
    actor_filter: str | None = Query(default=None, alias="actor"),
    surface: str | None = Query(default=None),
    project: str | None = Query(default=None),
    since_seq: int | None = Query(default=None, ge=0),
    until_seq: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """五维筛选（级别 / actor / 界面 / 项目 / 序号窗口）。"""
    return _logs(svc).query(
        actor, level=level, actor_filter=actor_filter, surface=surface, project=project,
        since_seq=since_seq, until_seq=until_seq, limit=limit, offset=offset,
    )


@router.get("/logs/exports")
async def quality_exports(actor: Actor = Depends(get_actor),
                          svc: Services = Depends(get_services)) -> dict[str, Any]:
    """本 owner 导出过的排查包。"""
    return _logs(svc).list_exports(actor)


@router.post("/logs/export")
async def quality_export(body: ExportBody,
                         actor: Actor = Depends(csrf_protected),
                         svc: Services = Depends(get_services)) -> dict[str, Any]:
    """一键导出排查包：日志 + 留痕 + 保存点清单；**导出前脱敏**、可预览、进留痕。"""
    out = _logs(svc).export_package(
        actor, level=body.level, surface=body.surface, project=body.project,
        since_seq=body.since_seq, until_seq=body.until_seq, preview=body.preview,
    )
    svc.session.commit()
    return out


@router.get("/logs/exports/{export_id}")
async def quality_export_detail(export_id: str, actor: Actor = Depends(get_actor),
                                svc: Services = Depends(get_services)) -> dict[str, Any]:
    """读回一个排查包（他人导出表现为 404，不泄露存在性）。"""
    return _logs(svc).export_detail(actor, export_id)


# --------------------------------------------------------------------------- #
# A-基座质保-13 · 质量机制性能预算门禁（W3）
# --------------------------------------------------------------------------- #
@router.get("/performance-budget/tiers")
async def quality_budget_tiers(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """硬件档位（低配 / 标准 / 高配）——阈值是基线的确定性派生，不是第二份配置。"""
    actor.require_authenticated()
    return {**budget_svc.hardware_tiers(),
            "baseline_version": budget_svc.load_baseline().get("version"),
            "baseline_file": str(budget_svc.BASELINE_FILE)}


@router.get("/performance-budget/limits")
async def quality_budget_limits(actor: Actor = Depends(get_actor),
                                tier: str = "standard") -> dict[str, Any]:
    """某档位下的**有效**阈值（基线 × 档位倍率）。"""
    actor.require_authenticated()
    return budget_svc.effective_limits(tier)


@router.get("/performance-budget/self-test")
async def quality_budget_self_test(actor: Actor = Depends(get_actor),
                                   tier: str = "standard") -> dict[str, Any]:
    """本地性能自测计划：每项怎么量 + 与 CI 完全同一份阈值的提交方式。"""
    actor.require_authenticated()
    return {**budget_svc.self_test_plan(tier),
            "local_script": SELF_TEST_COMMAND,
            "ci_note": "CI 与本地自测都调用 evaluate（同一份 performance_baseline.json），"
                       "不允许在任何地方再写一份阈值。"}


@router.get("/performance-budget")
async def quality_budget(actor: Actor = Depends(get_actor),
                         tier: str = "standard") -> dict[str, Any]:
    """预算门禁总览：五项指标 + 来源基线版本 + 档位 + 本地自测入口。"""
    actor.require_authenticated()
    baseline = budget_svc.load_baseline()
    limits = budget_svc.effective_limits(tier)
    return {
        "baseline_version": baseline.get("version"),
        "baseline_file": str(budget_svc.BASELINE_FILE),
        "unit": limits["unit"],
        "tier": limits["tier"],
        "tier_label": limits["tier_label"],
        "multiplier": limits["multiplier"],
        "metrics": [
            {"metric": name, **limits["limits"][name],
             "why": baseline["metrics"][name].get("why", "")}
            for name in budget_svc.METRICS
        ],
        "tiers": budget_svc.hardware_tiers()["tiers"],
        "self_test": {"plan_endpoint": "/api/quality/performance-budget/self-test",
                      "evaluate_endpoint": "/api/quality/performance-budget/evaluate",
                      "local_script": SELF_TEST_COMMAND},
        "source": "performance_baseline.json（唯一真源：CI 与本地自测共用）",
    }


@router.post("/performance-budget/evaluate")
async def quality_budget_evaluate(body: EvaluateBody,
                                  actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """按档位逐项判定。**超限即失败**：``ok=False`` + 逐项指标/阈值/超出量 + CI 退出码。"""
    actor.require_authenticated()
    result = budget_svc.evaluate(body.samples, body.tier)
    result["ci"] = {
        "exit_code": 0 if result["ok"] else 1,
        "reason": ("五项均在预算内，CI 放行" if result["ok"]
                   else f"性能预算超限：{result['summary']}"),
    }
    return result


# --------------------------------------------------------------------------- #
# A-基座质保-11 · 基座接入强制校验（W1）
# --------------------------------------------------------------------------- #
@router.get("/base-contract/exemptions")
async def quality_exemptions(actor: Actor = Depends(get_actor),
                             svc: Services = Depends(get_services)) -> dict[str, Any]:
    """豁免清单（含 approved / pending 分组与「可编辑不得豁免」规则）。"""
    return _base(svc).exemptions(actor)


@router.get("/base-contract/audit")
async def quality_base_audit(actor: Actor = Depends(get_actor),
                             svc: Services = Depends(get_services),
                             root: str | None = Query(default=None),
                             include_components: bool = Query(default=True)) -> dict[str, Any]:
    """基座接入校验：哪条界面没接入、缺哪个能力、怎么接入（CI 门禁判定）。"""
    dirs = ["web/src/pages"]
    if include_components:
        dirs.append("web/src/components")
    return _base(svc).audit_tree(actor, root=root, dirs=dirs)


@router.get("/base-contract")
async def quality_base_contract(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """接入契约：四项基座能力 + 声明形式 + 豁免规则（前端与 CI 共用一份）。"""
    actor.require_authenticated()
    return {
        **base_contract(),
        "audit_endpoint": "/api/quality/base-contract/audit",
        "exemptions_endpoint": "/api/quality/base-contract/exemptions",
    }


@router.post("/base-contract/exemptions")
async def quality_exemption_request(body: ExemptionBody,
                                    actor: Actor = Depends(csrf_protected),
                                    svc: Services = Depends(get_services)) -> dict[str, Any]:
    """登记豁免（仅限非编辑态纯展示页；理由必填；审批前不生效）。"""
    out = _base(svc).request_exemption(actor, path=body.path, reason=body.reason,
                                       scope=body.scope)
    svc.session.commit()
    return out


@router.post("/base-contract/exemptions/approve")
async def quality_exemption_approve(body: ExemptionDecisionBody,
                                    actor: Actor = Depends(csrf_protected),
                                    svc: Services = Depends(get_services)) -> dict[str, Any]:
    """审批豁免（owner 专属；通过后才真正放行）。"""
    out = _base(svc).approve_exemption(actor, path=body.path, note=body.note)
    svc.session.commit()
    return out


@router.post("/base-contract/exemptions/revoke")
async def quality_exemption_revoke(body: ExemptionDecisionBody,
                                   actor: Actor = Depends(csrf_protected),
                                   svc: Services = Depends(get_services)) -> dict[str, Any]:
    """撤回豁免（撤回后该文件立即重新进入门禁）。"""
    out = _base(svc).revoke_exemption(actor, path=body.path, note=body.note)
    svc.session.commit()
    return out


@router.get("/storage")
async def quality_storage(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """基座本地存储位置（导出包与豁免清单都落这里）；前端「占用空间」从这里取口径。"""
    actor.require_authenticated()
    root = quality_dir()
    exports = root / "exports"
    return {
        "quality_dir": str(root),
        "exists": root.is_dir(),
        "scope": "logs 导出包 + base 豁免清单（不落业务数据）",
        "endpoints": {
            "levels": "/api/quality/logs/levels",
            "query": "/api/quality/logs",
            "export": "/api/quality/logs/export",
            "base_audit": "/api/quality/base-contract/audit",
            "budget": "/api/quality/performance-budget",
        },
    }


__all__ = ["router"]
