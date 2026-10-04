"""受限 DSL 画布 HTTP 接口（工单 P1-18）。

* ``GET  /api/dsl-canvas/schema``        — DSL JSON Schema + 受限动词集元数据
* ``POST /api/dsl-canvas/validate``     — 只校验（编译）不执行
* ``POST /api/dsl-canvas/runs``         — 提交 DSL 文本 → 立即执行 → 返回 run_id 与结果
* ``GET  /api/dsl-canvas/runs/{run_id}`` — 取回运行结果与逐步日志

执行记录只落内存与归档目录（env ``FY_DSL_ARCHIVE_DIR``），不写数据库迁移。
执行器是确定性的（无外部 LLM 调用），因此提交即同步返回完整结果。
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import csrf_protected, get_actor
from ...services.actor import Actor
from ...services.dsl_canvas import (
    DSL_JSON_SCHEMA,
    NODE_TYPES,
    TRANSFORM_VERBS,
    DslRunStore,
    DslValidationError,
    compile_dsl,
    run_dsl,
)

router = APIRouter(prefix="/api/dsl-canvas", tags=["dsl-canvas"])

# 进程内运行存档；归档目录可通过 env 打开（evidence 归档用）。
_store = DslRunStore(archive_dir=os.environ.get("FY_DSL_ARCHIVE_DIR") or None)


@router.get("/schema")
async def get_schema() -> dict:
    return {
        "schema": DSL_JSON_SCHEMA,
        "node_types": list(NODE_TYPES),
        "transform_verbs": list(TRANSFORM_VERBS),
    }


def _extract_doc(payload: Any) -> dict:
    """兼容三种提交形态：裸 DSL 文档 / ``{"dsl": {...}}`` / ``{"dsl": "<json 文本>"}``。"""
    if isinstance(payload, dict) and isinstance(payload.get("dsl"), (dict, str)) \
            and "nodes" not in payload:
        doc = payload["dsl"]
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422,
                                    detail=f"DSL 文本不是合法 JSON: {exc}") from exc
        return doc
    if isinstance(payload, dict) and "version" in payload:
        return payload
    raise HTTPException(status_code=422,
                        detail="请求体需为 DSL 文档、{\"dsl\": 文档} 或 {\"dsl\": \"<json 文本>\"}")


@router.post("/validate")
async def validate(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    try:
        plan = compile_dsl(doc)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"valid": True, "topological_order": plan.order}


@router.post("/runs")
async def submit_run(request: Request, actor: Actor = Depends(csrf_protected)) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    try:
        result = run_dsl(doc)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _store.save(result)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, actor: Actor = Depends(get_actor)) -> dict:
    payload = _store.get(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    return payload


@router.get("/runs")
async def list_runs(actor: Actor = Depends(get_actor)) -> dict:
    return {"items": [{"run_id": rid} for rid in _store.list_ids()]}


def reset_store_for_tests(archive_dir: str | None = None) -> None:
    """测试专用：清空并可选重设归档目录。"""
    global _store
    _store = DslRunStore(archive_dir=archive_dir)
