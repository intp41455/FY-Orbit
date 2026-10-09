"""受限 DSL 画布 HTTP 接口（工单 P1-18 / 需求 5）。

* ``GET  /api/dsl-canvas/schema``        — DSL JSON Schema + 受限动词集元数据
* ``POST /api/dsl-canvas/validate``     — 只校验（编译）不执行
* ``POST /api/dsl-canvas/export-code``  — 画布 DSL → 受限 Python 代码
* ``POST /api/dsl-canvas/import-code``  — 受限 Python 代码 → 画布 DSL（往返无损）
* ``POST /api/dsl-canvas/runs``         — 提交 DSL 文本 → 立即执行 → 返回 run_id 与结果
  （走到 ``confirm`` 且尚无人工裁决时返回 ``status="suspended"`` + 挂起载荷）
* ``GET  /api/dsl-canvas/runs/{run_id}`` — 取回运行结果与逐步日志

执行记录只落内存与归档目录（env ``FY_DSL_ARCHIVE_DIR``），不写数据库迁移。
执行器是确定性的（无外部 LLM 调用），因此提交即同步返回完整结果。
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ...services.actor import Actor
from ...services.dsl_canvas import (
    AGGREGATE_OPS,
    DSL_JSON_SCHEMA,
    MERGE_OPS,
    NODE_TYPES,
    OUTPUT_FORMATS,
    TRANSFORM_VERBS,
    DslRunStore,
    DslSuspended,
    DslValidationError,
    compile_dsl,
    run_dsl,
    verb_catalog,
)
from ...services.dsl_code_export import export_dsl_code, parse_dsl_code
from ...services.dsl_ir import validate_ir  # P1 · 收集式 IR 校验（只读调用，禁改 dsl_ir）
from ..deps import csrf_protected, get_actor

router = APIRouter(prefix="/api/dsl-canvas", tags=["dsl-canvas"])

# 进程内运行存档；归档目录可通过 env 打开（evidence 归档用）。
_store = DslRunStore(archive_dir=os.environ.get("FY_DSL_ARCHIVE_DIR") or None)


@router.get("/schema")
async def get_schema() -> dict:
    return {
        "schema": DSL_JSON_SCHEMA,
        "node_types": list(NODE_TYPES),
        "transform_verbs": list(TRANSFORM_VERBS),
        # 需求 5①：动词集的完整元数据（名字/ 分类 / 参数契约 / 可执行性）。
        # 前端节点面板据此渲染表单，不再硬编码枚举。
        "verb_catalog": verb_catalog(),
        "aggregate_ops": list(AGGREGATE_OPS),
        "merge_ops": list(MERGE_OPS),
        "output_formats": list(OUTPUT_FORMATS),
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


@router.post("/validate-ir")
async def validate_ir_route(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    """P1 · 收集式 IR 校验（ADR-02）。

    与 ``POST /validate`` 的区别：**不 fail-fast**——一次返回全部
    :class:`~find_yourself.services.dsl_ir.Diagnostic`（node_id + field_path +
    code + message），供前端画布按节点打红点、按字段高亮。合法时
    ``diagnostics`` 为空数组。
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    diagnostics = validate_ir(doc)
    return {"valid": not diagnostics,
            "diagnostics": [d.to_dict() for d in diagnostics]}


@router.post("/runs")
async def submit_run(request: Request, actor: Actor = Depends(csrf_protected)):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    # execution_id 由调用方显式传入（不要用 run_id：重启即断链）。
    execution_id = payload.get("execution_id") if isinstance(payload, dict) else None
    if execution_id is not None and not isinstance(execution_id, str):
        raise HTTPException(status_code=422, detail="execution_id 必须是字符串")
    try:
        result = run_dsl(doc, execution_id=execution_id)
    except DslSuspended as susp:
        # 挂起**不是失败**（见 DslSuspended）。返回 202 + 挂起载荷，让上层建
        # HITL interrupt；人工裁决后带execution_id 重开一轮即可续跑。
        # 用 JSONResponse 显式返回：直接 return (dict, 202) 会被 FastAPI 当成
        # 「响应体是个二元组」而序列化成数组。
        return JSONResponse(status_code=202, content={
            "status": "suspended", "suspended": susp.to_dict()})
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _store.save(result)


@router.post("/export-code")
async def export_code(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    """画布 DSL → 受限 Python 代码。非法 DSL（含白名单外动词）422，绝不半成品。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    filename = (payload.get("filename") if isinstance(payload, dict) else None) or None
    try:
        if isinstance(filename, str) and filename:
            return export_dsl_code(doc, filename=filename)
        return export_dsl_code(doc)
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import-code")
async def import_code(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    """受限 Python 代码 → DSL 文档（与export-code 往返无损）。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("code"), str):
        raise HTTPException(status_code=422, detail="请求体需为 {\"code\": \"<受限 DSL 代码>\"}")
    try:
        doc = parse_dsl_code(payload["code"])
    except DslValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"dsl": doc, "topological_order": compile_dsl(doc).order}


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
