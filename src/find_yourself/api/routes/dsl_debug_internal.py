"""DSL 节点内精细单步调试 API（需求 §5.1 增强）。

在现有节点级单步（/step）基础上，新增节点内精细单步：

* F10 Step Over —— 执行到下一个同级别节点
* F11 Step Into —— 进入节点内部（如果是复合节点）
* Step Out —— 从节点内部跳出
* Breakpoint —— 断点管理

节点内执行状态机：
- idle: 未开始
- stepping: 正在单步执行
- paused: 暂停在某子步骤
- completed: 节点内部执行完成
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..deps import csrf_protected, get_actor
from ...services.actor import Actor
from ...services.dsl_canvas import (
    DslSuspended,
    DslValidationError,
    compile_dsl,
    dsl_digest,
    execute_node,
)
from . import dsl_canvas as _canvas_routes

router = APIRouter(prefix="/api/dsl/debug", tags=["dsl-debug-internal"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 节点内执行状态机
# --------------------------------------------------------------------------- #

class StepState(str, Enum):
    IDLE = "idle"
    STEPPING = "stepping"
    PAUSED = "paused"
    COMPLETED = "completed"


class StepMode(str, Enum):
    OVER = "over"    # F10: Step over (跳过内部子步骤)
    INTO = "into"    # F11: Step into (进入复合节点)
    OUT = "out"      # Step out (跳出复合节点)


@dataclass
class NodeStepContext:
    """节点内单步执行上下文"""
    run_id: str
    node_id: str
    state: StepState = StepState.IDLE
    step_mode: StepMode = StepMode.OVER
    # 子步骤状态（用于 transform/aggregate 等复合节点）
    sub_steps: list[dict] = field(default_factory=list)
    current_sub_step: int = 0
    # 变量状态
    variables: dict[str, Any] = field(default_factory=dict)
    # 断点
    breakpoints: set[int] = field(default_factory=set)
    # 暂停原因
    pause_reason: str | None = None
    # 开始时间
    started_at: str | None = None
    # 执行历史
    execution_history: list[dict] = field(default_factory=list)


# 全局存储（生产环境用 Redis）
_step_contexts: dict[str, NodeStepContext] = {}


def _get_step_context(run_id: str, node_id: str) -> NodeStepContext:
    key = f"{run_id}:{node_id}"
    if key not in _step_contexts:
        _step_contexts[key] = NodeStepContext(run_id=run_id, node_id=node_id)
    return _step_contexts[key]


def _delete_step_context(run_id: str, node_id: str) -> None:
    key = f"{run_id}:{node_id}"
    _step_contexts.pop(key, None)


def _parse_sub_steps(node: dict, payload: Any, upstreams: list) -> list[dict]:
    """解析节点的子步骤。

    不同节点类型有不同的子步骤分解：
    - transform: 预处理 → 执行变换 → 后处理
    - aggregate: 收集数据 → 分组/聚合 → 输出
    - conditional: 条件判断 → 分支执行
    - loop: 初始化 → 条件判断 → 循环体 → 更新
    """
    verb = node.get("verb", "")
    steps = []

    if verb == "transform":
        steps = [
            {"id": 0, "name": "预处理", "description": "数据验证与格式化"},
            {"id": 1, "name": "执行变换", "description": "应用转换规则"},
            {"id": 2, "name": "后处理", "description": "结果验证与格式化"},
        ]
    elif verb == "aggregate":
        steps = [
            {"id": 0, "name": "收集数据", "description": "从上游收集输入"},
            {"id": 1, "name": "分组聚合", "description": "应用聚合函数"},
            {"id": 2, "name": "输出结果", "description": "格式化输出"},
        ]
    elif verb == "conditional":
        steps = [
            {"id": 0, "name": "条件判断", "description": "评估条件表达式"},
            {"id": 1, "name": "分支执行", "description": "执行匹配的分支"},
        ]
    elif verb == "loop":
        steps = [
            {"id": 0, "name": "初始化", "description": "设置循环变量"},
            {"id": 1, "name": "条件判断", "description": "检查循环条件"},
            {"id": 2, "name": "循环体", "description": "执行循环内容"},
            {"id": 3, "name": "更新变量", "description": "更新循环变量"},
        ]
    else:
        # 简单节点只有一步
        steps = [{"id": 0, "name": "执行", "description": "执行节点"}]

    return steps


def _execute_sub_step(
    ctx: NodeStepContext,
    node: dict,
    payload: Any,
    upstreams: list,
    step_id: int,
) -> dict[str, Any]:
    """执行单个子步骤"""
    verb = node.get("verb", "")
    step_result = {
        "step_id": step_id,
        "step_name": ctx.sub_steps[step_id]["name"] if step_id < len(ctx.sub_steps) else f"Step {step_id}",
        "status": "pending",
        "output": None,
        "error": None,
    }

    try:
        if step_id == 0 and verb in ("transform", "aggregate", "conditional", "loop"):
            # 预处理/初始化步骤
            step_result["status"] = "succeeded"
            step_result["output"] = {"action": "prepare", "data": payload}
        elif verb == "transform" and step_id == 1:
            # 执行变换
            out = execute_node(node, payload, upstreams=upstreams)
            step_result["status"] = "succeeded"
            step_result["output"] = out
        elif step_id == len(ctx.sub_steps) - 1:
            # 最后一步：完整执行
            out = execute_node(node, payload, upstreams=upstreams)
            step_result["status"] = "succeeded"
            step_result["output"] = out
        else:
            step_result["status"] = "succeeded"
            step_result["output"] = {"partial": True, "step": step_id}
    except DslSuspended as susp:
        step_result["status"] = "suspended"
        step_result["error"] = str(susp)
    except Exception as exc:
        step_result["status"] = "failed"
        step_result["error"] = str(exc)

    return step_result


# --------------------------------------------------------------------------- #
# API 端点
# --------------------------------------------------------------------------- #

@router.post("/runs/{run_id}/node/{node_id}/step-into")
async def step_into(
    run_id: str,
    node_id: str,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """F11 Step Into —— 进入节点内部，开始节点内精细单步。

    返回：
    - node_type: 节点类型
    - sub_steps: 子步骤列表
    - current_step: 当前步骤
    - state: 执行状态
    """
    run = _canvas_routes._store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")

    doc = run["dsl"]
    try:
        plan = compile_dsl(doc)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if node_id not in plan.nodes:
        raise HTTPException(status_code=422, detail=f"节点不存在: {node_id}")

    node = plan.nodes[node_id]

    # 获取或创建执行上下文
    ctx = _get_step_context(run_id, node_id)

    # 解析子步骤
    logs = run.get("logs") or []
    incoming = [e for e in doc.get("edges", []) if e.get("to") == node_id]
    payload = None
    upstreams = []
    for edge in incoming:
        for log in reversed(logs):
            if log.get("node_id") == edge.get("from"):
                if log.get("status") == "succeeded":
                    upstreams.append(log.get("output"))
                    if payload is None:
                        payload = log.get("output")
                break

    ctx.sub_steps = _parse_sub_steps(node, payload, upstreams)
    ctx.state = StepState.PAUSED
    ctx.current_sub_step = 0
    ctx.step_mode = StepMode.INTO
    ctx.variables = {"payload": payload, "upstreams": upstreams}
    ctx.started_at = _now()

    return {
        "run_id": run_id,
        "node_id": node_id,
        "node_type": node.get("type"),
        "verb": node.get("verb"),
        "sub_steps": ctx.sub_steps,
        "current_step": ctx.current_sub_step,
        "state": ctx.state.value,
        "step_mode": ctx.step_mode.value,
        "variables": ctx.variables,
        "breakpoints": list(ctx.breakpoints),
    }


@router.post("/runs/{run_id}/node/{node_id}/step-over")
async def step_over(
    run_id: str,
    node_id: str,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """F10 Step Over —— 执行完当前节点的内部所有子步骤，跳到下一个节点。

    如果当前在节点内单步模式，直接执行完整个节点；
    如果不在节点内模式，创建草稿运行并执行完整节点。
    """
    ctx = _get_step_context(run_id, node_id)

    if ctx.state == StepState.PAUSED:
        # 节点内单步模式：执行完所有剩余子步骤
        run = _canvas_routes._store.get(run_id)
        doc = run["dsl"]
        plan = compile_dsl(doc)
        node = plan.nodes[node_id]

        logs = run.get("logs") or []
        incoming = [e for e in doc.get("edges", []) if e.get("to") == node_id]
        payload = None
        upstreams = []
        for edge in incoming:
            for log in reversed(logs):
                if log.get("node_id") == edge.get("from"):
                    if log.get("status") == "succeeded":
                        upstreams.append(log.get("output"))
                        if payload is None:
                            payload = log.get("output")
                    break

        # 执行完整节点
        try:
            out = execute_node(node, payload, upstreams=upstreams)
            ctx.state = StepState.COMPLETED
            ctx.execution_history.append({
                "step": "full_node",
                "status": "succeeded",
                "output": out,
                "timestamp": _now(),
            })

            # 更新运行日志
            log_entry = {
                "node_id": node_id,
                "status": "succeeded",
                "input": payload,
                "output": out,
                "started_at": ctx.started_at,
                "finished_at": _now(),
            }
            run["logs"] = run.get("logs") or []
            run["logs"].append(log_entry)
            _canvas_routes._store.save(run)

            return {
                "run_id": run_id,
                "node_id": node_id,
                "status": "completed",
                "output": out,
                "state": ctx.state.value,
                "history": ctx.execution_history,
            }
        except DslSuspended as susp:
            ctx.state = StepState.PAUSED
            ctx.pause_reason = "suspended"
            return {
                "run_id": run_id,
                "node_id": node_id,
                "status": "suspended",
                "suspended": susp.to_dict(),
                "state": ctx.state.value,
            }
        except Exception as exc:
            ctx.state = StepState.PAUSED
            ctx.pause_reason = str(exc)
            return {
                "run_id": run_id,
                "node_id": node_id,
                "status": "failed",
                "error": str(exc),
                "state": ctx.state.value,
            }
    else:
        # 非节点内模式：执行完整节点
        return {"error": "Use POST /api/dsl/runs/{run_id}/step for non-internal stepping"}


@router.post("/runs/{run_id}/node/{node_id}/step-next")
async def step_next(
    run_id: str,
    node_id: str,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """执行到下一个子步骤（单步前进）"""
    ctx = _get_step_context(run_id, node_id)

    if ctx.state not in (StepState.PAUSED, StepState.STEPPING):
        raise HTTPException(
            status_code=400,
            detail="Not in internal stepping mode. Call step-into first."
        )

    run = _canvas_routes._store.get(run_id)
    doc = run["dsl"]
    plan = compile_dsl(doc)
    node = plan.nodes[node_id]

    logs = run.get("logs") or []
    incoming = [e for e in doc.get("edges", []) if e.get("to") == node_id]
    payload = None
    upstreams = []
    for edge in incoming:
        for log in reversed(logs):
            if log.get("node_id") == edge.get("from"):
                if log.get("status") == "succeeded":
                    upstreams.append(log.get("output"))
                    if payload is None:
                        payload = log.get("output")
                break

    # 检查断点
    if ctx.current_sub_step in ctx.breakpoints:
        ctx.state = StepState.PAUSED
        ctx.pause_reason = "breakpoint"
        return {
            "run_id": run_id,
            "node_id": node_id,
            "status": "paused",
            "reason": "breakpoint",
            "current_step": ctx.current_sub_step,
            "state": ctx.state.value,
        }

    # 执行当前子步骤
    step_result = _execute_sub_step(ctx, node, payload, upstreams, ctx.current_sub_step)
    ctx.execution_history.append(step_result)

    # 检查是否是最后一步
    if ctx.current_sub_step >= len(ctx.sub_steps) - 1:
        ctx.state = StepState.COMPLETED
    else:
        ctx.current_sub_step += 1
        ctx.state = StepState.PAUSED

    return {
        "run_id": run_id,
        "node_id": node_id,
        "step_result": step_result,
        "current_step": ctx.current_sub_step,
        "state": ctx.state.value,
        "history": ctx.execution_history,
    }


@router.post("/runs/{run_id}/node/{node_id}/breakpoint/{step_id}")
async def set_breakpoint(
    run_id: str,
    node_id: str,
    step_id: int,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """设置/取消断点"""
    ctx = _get_step_context(run_id, node_id)

    if step_id in ctx.breakpoints:
        ctx.breakpoints.remove(step_id)
        action = "removed"
    else:
        ctx.breakpoints.add(step_id)
        action = "added"

    return {
        "run_id": run_id,
        "node_id": node_id,
        "step_id": step_id,
        "action": action,
        "breakpoints": list(ctx.breakpoints),
    }


@router.get("/runs/{run_id}/node/{node_id}/debug-state")
async def get_debug_state(
    run_id: str,
    node_id: str,
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """获取节点内调试状态"""
    ctx = _get_step_context(run_id, node_id)

    return {
        "run_id": run_id,
        "node_id": node_id,
        "state": ctx.state.value,
        "step_mode": ctx.step_mode.value,
        "sub_steps": ctx.sub_steps,
        "current_step": ctx.current_sub_step,
        "variables": ctx.variables,
        "breakpoints": list(ctx.breakpoints),
        "pause_reason": ctx.pause_reason,
        "execution_history": ctx.execution_history,
    }


@router.post("/runs/{run_id}/node/{node_id}/continue")
async def debug_continue(
    run_id: str,
    node_id: str,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """继续执行（从断点或暂停处）"""
    ctx = _get_step_context(run_id, node_id)

    if ctx.state not in (StepState.PAUSED, StepState.STEPPING):
        raise HTTPException(status_code=400, detail="Not in debug mode")

    run = _canvas_routes._store.get(run_id)
    doc = run["dsl"]
    plan = compile_dsl(doc)
    node = plan.nodes[node_id]

    logs = run.get("logs") or []
    incoming = [e for e in doc.get("edges", []) if e.get("to") == node_id]
    payload = None
    upstreams = []
    for edge in incoming:
        for log in reversed(logs):
            if log.get("node_id") == edge.get("from"):
                if log.get("status") == "succeeded":
                    upstreams.append(log.get("output"))
                    if payload is None:
                        payload = log.get("output")
                break

    # 执行到下一个断点或结束
    all_outputs = []
    while ctx.current_sub_step < len(ctx.sub_steps):
        if ctx.current_sub_step in ctx.breakpoints:
            ctx.state = StepState.PAUSED
            ctx.pause_reason = "breakpoint"
            return {
                "run_id": run_id,
                "node_id": node_id,
                "status": "paused",
                "reason": "breakpoint",
                "current_step": ctx.current_sub_step,
                "outputs": all_outputs,
            }

        step_result = _execute_sub_step(ctx, node, payload, upstreams, ctx.current_sub_step)
        ctx.execution_history.append(step_result)
        all_outputs.append(step_result)
        ctx.current_sub_step += 1

    ctx.state = StepState.COMPLETED

    return {
        "run_id": run_id,
        "node_id": node_id,
        "status": "completed",
        "state": ctx.state.value,
        "outputs": all_outputs,
    }


@router.post("/runs/{run_id}/node/{node_id}/reset")
async def reset_debug(
    run_id: str,
    node_id: str,
    actor: Actor = Depends(csrf_protected),
) -> dict[str, Any]:
    """重置节点内调试状态"""
    _delete_step_context(run_id, node_id)

    return {
        "run_id": run_id,
        "node_id": node_id,
        "action": "reset",
        "message": "Debug context cleared",
    }
