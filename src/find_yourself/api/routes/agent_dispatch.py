"""子 Agent 派发验证 HTTP 接口（工单 P1-20）。

* ``POST /api/agent-dispatch``            — 派发子任务（Task 四件套 + 工具清单）
* ``GET  /api/agent-dispatch``            — 列出全部派发
* ``GET  /api/agent-dispatch/{id}``       — 取回父/子两级 trace（派发时间/工具调用/子自报/验收结论）

请求体::

    {
      "task": {"capability": "...", "payload": {...},
               "acceptance": {"type": "output_contains", "contains": [...]},
               "env_contract": {...}},   # 可选，轻量环境标注
      "tools": [{"name": "echo", "arguments": {...}}, ...]
    }

子 Agent 是确定性工作器（真实调用 P1-05 工具注册中心），提交即同步返回
完整父/子 trace；验收由独立验收器按 acceptance 真实复核，不信任自报。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.agent_dispatch import (
    DispatchValidationError,
    agent_dispatch,
)
from ..deps import csrf_protected, get_actor

router = APIRouter(prefix="/api/agent-dispatch", tags=["agent-dispatch"])


class TaskSpec(BaseModel):
    capability: str = Field(min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)
    acceptance: dict
    env_contract: dict = Field(default_factory=dict)


class ToolCallSpec(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class DispatchBody(BaseModel):
    task: TaskSpec
    tools: list[ToolCallSpec] = Field(min_length=1)


@router.post("")
def dispatch_task(body: DispatchBody,
                  actor: Actor = Depends(csrf_protected)) -> dict:
    try:
        parent = agent_dispatch.dispatch(
            body.task.model_dump(), [t.model_dump() for t in body.tools])
    except DispatchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return parent


@router.get("")
def list_dispatches(actor: Actor = Depends(get_actor)) -> dict:
    return {"count": len(agent_dispatch.list_ids()),
            "parent_task_ids": agent_dispatch.list_ids()}


@router.get("/{parent_task_id}")
def get_dispatch(parent_task_id: str, actor: Actor = Depends(get_actor)) -> dict:
    parent = agent_dispatch.get(parent_task_id)
    if parent is None:
        raise HTTPException(status_code=404,
                            detail=f"派发不存在: {parent_task_id}")
    return parent


def reset_store_for_tests(archive_dir: str | None = None) -> None:
    """测试专用：重建进程内单例并可选重设归档目录。"""
    from ...services.agent_dispatch import AgentDispatchService

    global agent_dispatch
    agent_dispatch = AgentDispatchService(archive_dir=archive_dir)
