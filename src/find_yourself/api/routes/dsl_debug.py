"""DSL 节点级调试 API（A-代码SDK-03 / A-画布搭建器-04，与包5 前端联动）。

契约（已定案，供前端逐字消费；前端实现是包5 的
``web/src/components/workflow`` 域）：

* ``POST /api/dsl/runs/{run_id}/step``              —— 单步执行一节点
* ``POST /api/dsl/runs/{run_id}/node/{node_id}/run`` —— 单节点运行（自动取上游变量）
* ``GET  /api/dsl/runs/{run_id}/state``              —— 变量监视

外加两个同域支撑端点（使上面三条真正可用）：

* ``POST /api/dsl/runs``        —— 创建**草稿运行**（只登记 DSL 不执行）。
  画布的 ``POST /api/dsl-canvas/runs`` 是提交即整图跑完，没有「逐步」可言；
  调试语义需要一个未执行/半执行的 run 作载体。响应与画布 run 字段同构。
* ``GET  /api/dsl/modes``       —— 三重模式入口骨架（小白/技术/企业，
  数据来自 :func:`find_yourself.services.dsl_sdk.mode_overview`）。

语义与画布执行器逐字对齐：
* 上游变量合并规则 = ``run_dsl``：payload 取第一个非 None 的成功上游输出，
  ``upstreams`` 保留全部（供 merge 动词）；
* 边条件按「源节点输出」评估（与 ``run_dsl`` 的动态入队一致）；入边存在而
  全部不通过 → 该节点记 ``skipped``（不执行）；
* 走到 ``confirm`` / ``approval`` 且尚无裁决 → **202 + 挂起载荷**（控制流
  信号，不是失败），日志如实记 ``suspended``。

运行存档与画布共用同一个进程内 :class:`DslRunStore`（``runs/dsl_canvas.py``
的 ``_store``），因此调试端点可以直接调试画布跑过的 run。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ...services.actor import Actor
from ...services.dsl_canvas import (
    DslSuspended,
    DslValidationError,
    compile_dsl,
    dsl_digest,
    execute_node,
)
from ...services.dsl_sdk import mode_overview
from ..deps import csrf_protected, get_actor
from . import dsl_canvas as _canvas_routes
from .dsl_canvas import _extract_doc

router = APIRouter(prefix="/api/dsl", tags=["dsl-debug"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_run(run_id: str) -> dict[str, Any]:
    """取共享存档里的运行记录（画布与调试端点读写同一份）。"""
    payload = _canvas_routes._store.get(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    return payload


def _plan_or_422(doc: dict[str, Any]):
    try:
        return compile_dsl(doc)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _latest_log(logs: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    for log in reversed(logs):
        if log.get("node_id") == node_id:
            return log
    return None


def _upstreams(doc: dict[str, Any], logs: list[dict[str, Any]],
               node_id: str) -> tuple[Any, list[Any], list[dict[str, Any]]]:
    """按 ``run_dsl`` 同款规则合并上游变量。

    返回 ``(payload, upstreams, incoming_edges)``：payload 取第一个非 None
    的成功上游输出；upstreams 保留全部成功上游输出（按边声明序）。
    """
    incoming = [e for e in doc.get("edges", []) if e.get("to") == node_id]
    payload: Any = None
    upstreams: list[Any] = []
    for edge in incoming:
        up = _latest_log(logs, edge.get("from", ""))
        if up is not None and up.get("status") == "succeeded":
            upstreams.append(up.get("output"))
            if payload is None and up.get("output") is not None:
                payload = up.get("output")
    return payload, upstreams, incoming


def _edge_passed(up_log: dict[str, Any] | None,
                 condition: dict[str, Any] | None) -> bool:
    """评估一条入边是否放行（与 ``run_dsl`` 的动态入队判定一致）。

    上游成功才评估条件；上游 failed/skipped/无日志 → 边不放行（与调度器
    「失败分支终止」语义一致）。无条件边恒放行。
    """
    if up_log is None or up_log.get("status") != "succeeded":
        return False
    if not condition:
        return True
    from ...services.dsl_canvas import _compare, _lookup  # 同一比较语义
    out = up_log.get("output")
    return _compare(_lookup(out, condition.get("field", "")),
                    condition.get("op", ""), condition.get("value"))


def _append_log(run: dict[str, Any], node_id: str, node: dict[str, Any],
                status: str, *, input_: Any = None, output: Any = None,
                error: str | None = None) -> dict[str, Any]:
    log = {
        "node_id": node_id, "node_type": node.get("type"),
        "verb": node.get("verb"), "status": status,
        "input": input_, "output": output, "error": error,
        "started_at": _now(), "finished_at": _now(),
    }
    run["logs"] = run.get("logs") or []
    run["logs"].append(log)
    return log


def _finalize_if_complete(run: dict[str, Any], plan) -> None:
    """全部节点都有日志且无失败/挂起 → 收敛成 succeeded 并计算最终输出。"""
    if run.get("status") in ("failed", "suspended"):
        return
    logged = {log.get("node_id") for log in run.get("logs", [])}
    if logged < set(plan.nodes):
        if run.get("status") == "draft":
            run["status"] = "running"
        return
    outputs = {log.get("node_id"): log.get("output")
               for log in run.get("logs", []) if log.get("status") == "succeeded"}
    outs = [outputs[n["id"]] for n in run["dsl"]["nodes"]
            if n["type"] == "output" and n["id"] in outputs]
    run["status"] = "succeeded"
    run["output"] = outs[-1] if outs else outputs.get(plan.order[-1])


def _progress(run: dict[str, Any], plan) -> dict[str, int]:
    executed = len({log.get("node_id") for log in run.get("logs", [])})
    return {"executed": executed, "total": len(plan.nodes)}


def _next_node(run: dict[str, Any], plan) -> str | None:
    logged = {log.get("node_id") for log in run.get("logs", [])}
    for nid in plan.order:
        if nid not in logged:
            return nid
    return None


def _node_response(run: dict[str, Any], plan, node_id: str,
                   status: str, *, input_: Any = None, output: Any = None,
                   error: str | None = None) -> dict[str, Any]:
    return {
        "run_id": run["run_id"], "node_id": node_id, "status": status,
        "input": input_, "output": output, "error": error,
        "run_status": run.get("status"),
        "progress": _progress(run, plan),
        "next_node_id": _next_node(run, plan),
    }


def _execute_single_node(run: dict[str, Any], node_id: str) -> Any:
    """执行一个节点并写回存档（step 与单节点运行共用）。

    挂起返回 ``JSONResponse(202)``；其余返回响应字典。语义见模块 docstring。
    """
    doc = run["dsl"]
    plan = _plan_or_422(doc)
    if node_id not in plan.nodes:
        raise HTTPException(status_code=422, detail=f"节点不存在: {node_id}")

    logs = run.get("logs") or []
    payload, upstreams, incoming = _upstreams(doc, logs, node_id)
    node = plan.nodes[node_id]

    # 入边全部「已决」（上游都有日志）且全部不放行 → skipped（与调度器语义
    # 一致：条件不满足/上游失败的分支终止）。上游尚未执行不算「不放行」——
    # 显式指定节点时按 run_dsl 同款照常执行（transform 无上游输入会如实失败）。
    all_upstreams_decided = all(
        _latest_log(logs, e.get("from", "")) is not None for e in incoming)
    if incoming and all_upstreams_decided and not any(
            _edge_passed(_latest_log(logs, e.get("from", "")), e.get("condition"))
            for e in incoming):
        log = _append_log(run, node_id, node, "skipped", input_=payload)
        _finalize_if_complete(run, plan)
        return _node_response(run, plan, node_id, "skipped",
                              input_=log["input"])

    log_entry = _append_log(run, node_id, node, "succeeded", input_=payload)
    if run.get("status") == "draft":
        run["status"] = "running"
    try:
        out = execute_node(
            node, payload, upstreams=upstreams,
            execution_id=run.get("execution_id"),
            dsl_digest=dsl_digest(doc))
        log_entry["output"] = out
        log_entry["finished_at"] = _now()
        _finalize_if_complete(run, plan)
        return _node_response(run, plan, node_id, "succeeded",
                              input_=payload, output=out)
    except DslSuspended as susp:
        log_entry["status"] = "suspended"
        log_entry["finished_at"] = _now()
        run["status"] = "suspended"
        run["error"] = None
        # 202 + 挂起载荷：与画布 runs 端点同款（前端据此建 HITL interrupt）。
        return JSONResponse(status_code=202, content={
            "run_id": run["run_id"], "node_id": node_id,
            "status": "suspended", "suspended": susp.to_dict(),
            "progress": _progress(run, plan),
            "next_node_id": _next_node(run, plan),
        })
    except Exception as exc:
        log_entry["status"] = "failed"
        log_entry["error"] = str(exc)
        log_entry["finished_at"] = _now()
        run["status"] = "failed"
        run["error"] = f"节点 {node_id} 执行失败: {exc}"
        return _node_response(run, plan, node_id, "failed",
                              input_=payload, error=str(exc))


# ---------------------------------------------------------------------------
# 草稿运行创建（调试语义的载体；画布 runs 是整图即跑，没有逐步可言）
# ---------------------------------------------------------------------------


@router.post("/runs")
async def create_draft_run(request: Request,
                           actor: Actor = Depends(csrf_protected)) -> dict:
    """创建草稿运行：只登记 DSL、**不执行**，供逐步调试。"""
    from ...services.dsl_canvas import RunResult
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    execution_id = payload.get("execution_id") if isinstance(payload, dict) else None
    if execution_id is not None and not isinstance(execution_id, str):
        raise HTTPException(status_code=422, detail="execution_id 必须是字符串")
    plan = _plan_or_422(doc)  # 只编译不执行：环/类型错误在这里就 422
    result = RunResult(run_id=f"dsl-debug-{uuid.uuid4().hex[:12]}", status="draft",
                       doc=doc, execution_id=execution_id)
    stored = _canvas_routes._store.save(result)
    return {"run_id": stored["run_id"], "status": stored["status"],
            "node_count": len(plan.nodes), "edge_count": len(doc.get("edges", [])),
            "topological_order": plan.order}


# ---------------------------------------------------------------------------
# 定案契约的三条端点
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/step")
async def step_run(run_id: str, request: Request,
                   actor: Actor = Depends(csrf_protected)):
    """单步执行一节点：缺省取拓扑序下一个未执行节点，也可用
    ``{"node_id": "..."}`` 指定。全部执行过时返回 ``status="completed"``。"""
    run = _load_run(run_id)
    plan = _plan_or_422(run["dsl"])
    node_id: str | None = None
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and body.get("node_id") is not None:
        node_id = body["node_id"]
        if not isinstance(node_id, str) or node_id not in plan.nodes:
            raise HTTPException(status_code=422,
                                detail=f"节点不存在: {node_id!r}")
    else:
        node_id = _next_node(run, plan)
        if node_id is None:
            return {"run_id": run_id, "status": "completed",
                    "run_status": run.get("status"),
                    "progress": _progress(run, plan), "next_node_id": None}
    return _execute_single_node(run, node_id)


@router.post("/runs/{run_id}/node/{node_id}/run")
async def run_node(run_id: str, node_id: str,
                   actor: Actor = Depends(csrf_protected)):
    """单节点运行：**自动取上游变量**（按 ``run_dsl`` 同款合并规则），
    可重复执行（追加日志，便于调试观察）。"""
    run = _load_run(run_id)
    return _execute_single_node(run, node_id)


@router.get("/runs/{run_id}/state")
async def get_state(run_id: str, actor: Actor = Depends(get_actor)) -> dict:
    """变量监视：每个节点的最新状态与输出（变量 = 节点输出）。"""
    run = _load_run(run_id)
    plan = _plan_or_422(run["dsl"])
    variables: dict[str, dict[str, Any]] = {}
    for nid in plan.order:
        log = _latest_log(run.get("logs") or [], nid)
        variables[nid] = {
            "status": log.get("status") if log else "pending",
            "output": log.get("output") if log else None,
            "error": log.get("error") if log else None,
        }
    return {
        "run_id": run_id, "run_status": run.get("status"),
        "progress": _progress(run, plan),
        "variables": variables,
        "final_output": run.get("output"), "error": run.get("error"),
        "execution_id": run.get("execution_id"),
    }


# ---------------------------------------------------------------------------
# 三重模式入口骨架（A-三重模式-04）
# ---------------------------------------------------------------------------


@router.get("/modes")
async def get_modes(actor: Actor = Depends(get_actor)) -> dict:
    """三重模式（小白/技术/企业）入口概览；数据来自 dsl_sdk 注册框架。"""
    return {"modes": mode_overview()}
