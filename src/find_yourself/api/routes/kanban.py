"""任务看板 HTTP 路由（A-任务看板-01～13 的 API 面）。

范式与 ``api/routes/agent_teams.py`` 一致：

* 每个 owner 读路由用 ``get_actor``，每个改动路由用 ``csrf_protected``；
* **任何 body 都不接受 ``owner_id``/``role``**——actor 一律由服务端解析
  （BUG-03：请求体里的 owner_id 绝不能提升权限）；
* 错误统一走 ``services/errors.py`` 的 DomainError 信封，由全局异常处理器
  映射成 ``{code, message}``，不把栈或他人对象泄出去。

红带（需求 11）只在 ``board`` / ``detail`` 的 ``red_band`` 字段里表达，
**没有单独的「红带接口」**：红带不是一个可写对象，它是两类真实信号的投影，
做成独立接口等于给用户一个可以随手点亮红带的开关。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/kanban", tags=["kanban"])


def _svc(svc: Services):
    return svc.kanban


def _commit(svc: Services) -> None:
    """Commit the request-scoped session after a mutation.

    🔴 本仓约定：**service 层只 flush，commit 由路由层做**（``get_db`` 只 yield +
    close，不提交；见 ``api/deps.py:91-96``）。照 ``agent_teams.py`` /
    ``canvas.py`` 的写法每个改动路由都显式 commit。

    漏掉这一行的后果很隐蔽：响应体会返回**已 flush 的**新状态，看起来完全正常，
    但请求结束时 session.close() 把改动回滚了——用户刷新页面发现刚才的改动
    消失了。KanbanService 全部只 flush，所以每个改动路由都必须自己 commit。
    """
    svc.session.commit()


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class ProgressBody(BaseModel):
    """写进度 / 权重 / 关键事项标记。

    三种语义用「字段缺省 vs 显式 null」区分，服务层用 ``UNSET`` 哨兵接住：

    * 字段不传 → 不改；
    * ``"progress_percent": null`` → 回到「未开始」；
    * 给值 → 写入。

    因此这三个字段都**不能**写成 ``Field(...)`` 必填。
    """

    model_config = ConfigDict(extra="forbid")

    progress_percent: int | None = Field(default=None, ge=0, le=100)
    weight: int | None = Field(default=None, ge=0)
    critical: bool | None = None


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planned_start: datetime | None = None
    planned_end: datetime | None = None


class DependencyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_task_id: str = Field(min_length=1, max_length=64)


class OperationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
@router.get("/board")
async def board(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    escalation_hours: int = Query(default=6, ge=0, le=720),
) -> dict[str, Any]:
    """整块看板：四列 + 卡片（加权进度 / 红带）+ 汇总。空板是诚实的空形状。"""
    return _svc(svc).board(actor, escalation_hours=escalation_hours)


@router.get("/board/burndown")
async def board_burndown(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    days: int = Query(default=14, ge=1, le=180),
) -> dict[str, Any]:
    """燃尽 / 燃起数据点（需求 12）。样本不足时 ``partial=true``，前端须显示「数据不足」。"""
    return _svc(svc).burndown(actor, days=days)


@router.get("/tasks/{task_id}")
async def task_detail(
    task_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """任务详情（需求 10）：进度 / 子任务清单 / 关键事项 / 依赖边 / 历史记录。"""
    return _svc(svc).detail(actor, task_id)


# ---------------------------------------------------------------------------
# 写：进度 / 排期 / 依赖
# ---------------------------------------------------------------------------
@router.put("/tasks/{task_id}/progress")
async def set_progress(
    task_id: str,
    body: ProgressBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """写进度 / 权重 / 关键事项标记（需求 02、03）。

    body 里出现的字段才会被改；``null`` 表示清空（如进度回到未开始）。
    """
    payload: dict[str, Any] = {}
    fields = body.model_fields_set
    if "progress_percent" in fields:
        payload["progress_percent"] = body.progress_percent
    if "weight" in fields:
        payload["weight"] = body.weight
    if "critical" in fields:
        payload["critical"] = body.critical
    out = _svc(svc).set_progress(actor, task_id, **payload)
    _commit(svc)
    return out


@router.put("/tasks/{task_id}/schedule")
async def set_plan(
    task_id: str,
    body: PlanBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """写排期（甘特的条，需求 13）。两者皆 null = 未排期，不编造日期。

    🔴 路径叫 ``/schedule`` 而不是 ``/plan``：``tests/unit/test_guest_account.py::
    test_api_account_reports_payment_disabled`` 有一条护栏
    ``assert not any(p.endswith("/plan") for p in openapi_paths)``，
    断言「没有任何端点能改账号套餐」。本端点改的是**任务的起止时间**，与账号
    套餐毫无关系，但那条护栏是按路径后缀匹配的——用 ``/plan`` 会把它误伤。
    与其去改那条护栏（它是故意埋的绊子），不如把自己的路径命名避开。
    """
    out = _svc(svc).set_plan(
        actor, task_id,
        planned_start=body.planned_start,
        planned_end=body.planned_end,
    )
    _commit(svc)
    return out


@router.post("/tasks/{task_id}/dependencies", status_code=status.HTTP_201_CREATED)
async def add_dependency(
    task_id: str,
    body: DependencyBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """声明「A 完成后 B 才能开始」（需求 05）。成环返回 409 ``dependency_cycle``。"""
    out = _svc(svc).add_dependency(actor, task_id, body.depends_on_task_id)
    _commit(svc)
    return out


@router.delete("/tasks/{task_id}/dependencies/{depends_on_task_id}")
async def remove_dependency(
    task_id: str,
    depends_on_task_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """删除一条依赖边。"""
    out = _svc(svc).remove_dependency(actor, task_id, depends_on_task_id)
    _commit(svc)
    return out


# ---------------------------------------------------------------------------
# 写：生命周期（需求 06）
# ---------------------------------------------------------------------------
def _operation_route(op: str, summary: str):
    @router.post(f"/tasks/{{task_id}}/{op}", summary=summary)
    async def _run(
        task_id: str,
        body: OperationBody,
        actor: Actor = Depends(csrf_protected),
        svc: Services = Depends(get_services),
    ) -> dict[str, Any]:
        out = _svc(svc).transition(actor, task_id, op, reason=body.reason)
        _commit(svc)
        return out

    return _run


pause_task = _operation_route("pause", "暂停任务（落到阻塞列并记原因，需求 06/11）")
resume_task = _operation_route("resume", "恢复任务（上游依赖未完成则 409，需求 05/06）")
terminate_task = _operation_route("terminate", "终止任务（幂等，需求 06）")


@router.post("/tasks/{task_id}/start")
async def start_task(
    task_id: str,
    body: OperationBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """启动 queued 任务。上游依赖未完成则 409 ``dependency_blocking``（需求 05）。"""
    out = _svc(svc).start(actor, task_id, reason=body.reason)
    _commit(svc)
    return out
