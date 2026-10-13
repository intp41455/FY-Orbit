"""受限 DSL 画布 HTTP 接口（工单 P1-18 / 需求 5）。

* ``GET  /api/dsl-canvas/schema``        — DSL JSON Schema + 受限动词集元数据
* ``POST /api/dsl-canvas/validate``     — 只校验（编译）不执行
* ``POST /api/dsl-canvas/export-code``  — 画布 DSL → 受限 Python 代码
* ``POST /api/dsl-canvas/import-code``  — 受限 Python 代码 → 画布 DSL（往返无损）
* ``POST /api/dsl-canvas/runs``         — 提交 DSL 文本 → 立即执行 → 返回 run_id 与结果
  （走到 ``confirm`` / ``approval`` 且尚无人工裁决时返回 ``status="suspended"``
  + 挂起载荷；``execution_id`` 相同的重开一轮会回查 HITL 已落定的裁决续跑）
* ``GET  /api/dsl-canvas/runs/{run_id}`` — 取回运行结果与逐步日志
* ``POST /api/dsl-canvas/preview``     — 画布内联调参的即时重算：**执行但不落库**
  （工单 G4-d。与 ``/runs`` 的唯一区别就是没有 ``_store.save()``；命中
  ``approval`` / ``confirm`` 时返回 202 + 挂起载荷）

执行记录只落内存与归档目录（env ``FY_DSL_ARCHIVE_DIR``），不写数据库迁移。
执行器是确定性的（无外部 LLM 调用），因此提交即同步返回完整结果。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...services import dsl_run_events
from ...services.actor import Actor
from ...services.dsl_canvas import (
    AGGREGATE_OPS,
    DSL_JSON_SCHEMA,
    MERGE_OPS,
    NODE_PARAMS_SCHEMAS,
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
from ...services.errors import ValidationFailed
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/dsl-canvas", tags=["dsl-canvas"])

# 进程内运行存档；归档目录可通过 env 打开（evidence 归档用）。
_store = DslRunStore(archive_dir=os.environ.get("FY_DSL_ARCHIVE_DIR") or None)


def _actor_identity(actor: Actor) -> str:
    """run 的归属标识（owner 与 service 身份不互通）。"""
    return actor.owner_id or actor.service_id or ""


# 人工裁决原值 → DSL approval signal 的合法取值。
# 只有「批准」「驳回」两值能直通；``team_approval`` 的第三值 ``return_for_change``
# （以及 cancelled / expired / timeout）**一律不映射**——signal 的类型里没有
# 第三档，猜一个就等于替治理层做决定，故让它落回 None 继续挂起。
_APPROVAL_DECISIONS = {"approve": "approve", "reject": "reject"}


def _approval_signal(svc: Services, actor: Actor, execution_id: str | None):
    """把「上一轮人怎么裁决的」接成 ``run_dsl`` 的 :data:`ApprovalSignal`。

    断点在此：``approval`` 节点在服务层早就支持挂起/放行/驳回，但 HTTP 面此前
    从不传 ``approval_signal``，于是永远读到「尚无裁决」→恒挂起，人工裁决形同
    摆设。此处按 :class:`~find_yourself.services.dsl_canvas.VerbContext` 的
    checkpoint 格式（``{execution_id}#{node_id}``）回查HITL，让重放找到裁决。

    checkpoint 拼法与 ``_exec_approval`` 里抛 ``DslSuspended`` 时用的完全一致
    （含 ``execution_id`` 为 None 时退回 ``-`` 的写法），否则读回来永远查无此行。

    读不到（尚未裁决 / 不可见 / 已超时 / 第三值）一律 ``None`` → 继续挂起。
    **绝不把「读不到」猜成 approve。**
    """
    if not execution_id:
        # 无 execution_id 时挂起行的execution_id 落的是调用方给的 None/空，
        # 此时无法定位唯一一行，宁可不读也不误放行。
        return None

    def read(node_id: str, run_execution_id: str | None) -> str | None:
        # 以执行体回传的 execution_id 为准（与抛 DslSuspended 时用的是同一个
        # 值），闭包里的只作兜底——两者按构造恒等，但拼checkpoint 必须用
        # 「抛挂起那一刻」的那个值，差一点就永远查无此行。
        exec_id = run_execution_id or execution_id
        decision = svc.hitl.decision_for(
            actor, exec_id, f"{exec_id}#{node_id}")
        return _APPROVAL_DECISIONS.get(decision or "")

    return read


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
        # G4-j：节点级参数契约（``NODE_PARAMS_SCHEMAS`` 单一真源，逐字下发）。
        # 前端据此驱动节点参数枚举（input.kind / output.format / mode / method
        # / language / strategy / trigger kind ...），不再手抄一份镜像。
        "node_params_schema": NODE_PARAMS_SCHEMAS,
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
                raise ValidationFailed(
                    "validation_failed", f"DSL 文本不是合法 JSON: {exc}") from exc
        return doc
    if isinstance(payload, dict) and "version" in payload:
        return payload
    raise ValidationFailed(
        "validation_failed",
        "请求体需为 DSL 文档、{\"dsl\": 文档} 或 {\"dsl\": \"<json 文本>\"}")


@router.post("/validate")
async def validate(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValidationFailed("validation_failed", "请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    try:
        plan = compile_dsl(doc)
    except DslValidationError as exc:
        raise ValidationFailed("validation_failed", str(exc)) from exc
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
        raise ValidationFailed("validation_failed", "请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    diagnostics = validate_ir(doc)
    return {"valid": not diagnostics,
            "diagnostics": [d.to_dict() for d in diagnostics]}


@router.post("/runs")
async def submit_run(request: Request, actor: Actor = Depends(csrf_protected),
                     svc: Services = Depends(get_services)):
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValidationFailed("validation_failed", "请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    # execution_id 由调用方显式传入（不要用 run_id：重启即断链）。
    execution_id = payload.get("execution_id") if isinstance(payload, dict) else None
    if execution_id is not None and not isinstance(execution_id, str):
        raise ValidationFailed("validation_failed", "execution_id 必须是字符串")
    if isinstance(payload, dict) and payload.get("stream"):
        return _submit_run_stream(doc, execution_id, _actor_identity(actor))
    try:
        result = run_dsl(
            doc,
            execution_id=execution_id,
            approval_signal=_approval_signal(svc, actor, execution_id),
        )
    except DslSuspended as susp:
        # 挂起**不是失败**（见 DslSuspended）。返回 202 + 挂起载荷，让上层建
        # HITL interrupt；人工裁决后带execution_id 重开一轮即可续跑。
        # 用 JSONResponse 显式返回：直接 return (dict, 202) 会被 FastAPI 当成
        # 「响应体是个二元组」而序列化成数组。
        return JSONResponse(status_code=202, content={
            "status": "suspended", "suspended": susp.to_dict()})
    except DslValidationError as exc:
        raise ValidationFailed("validation_failed", str(exc)) from exc
    return _store.save(result, owner=_actor_identity(actor))


@router.post("/preview")
async def preview_run(request: Request, actor: Actor = Depends(csrf_protected)):
    """画布内联调参即时重算：**执行但不落库**（G4-d）。

    与 ``POST /runs`` 的唯一区别是**没有 ``_store.save()``**。落库不是「顺手的
    副作用」而是 ``/runs`` 的契约：``DslRunStore.save`` 同时写进程内环形缓冲
    （``max_in_memory=200``）与归档目录（``FY_DSL_ARCHIVE_DIR``）。若属性面板
    每敲一键就过一次它，用户调 200 次参数就会把全部真实 run 记录挤出缓冲。

    **刻意不做 overrides 入参。** 参数改动直接由调用方改完 ``doc`` 再提交，
    端点只负责跑。这样预览与真实运行读的是**同一份文档**，不存在第二套覆盖
    通道；多一条 overrides 就会多一处「doc 改了但 overrides 还是旧值」的漂移。

    命中 ``approval`` / ``confirm`` 时预览**必然**挂起（预览没有上一轮人工
    裁决可回查，也绝不代治理层做决定），返 202 + 挂起载荷，让属性面板提示
    「此图需人工裁决后才能跑完」而不是500。
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValidationFailed("validation_failed", "请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    try:
        result = run_dsl(doc)
    except DslSuspended as susp:
        return JSONResponse(status_code=202, content={
            "status": "suspended", "suspended": susp.to_dict(),
            "preview_only": True})
    except DslValidationError as exc:
        raise ValidationFailed("validation_failed", str(exc)) from exc
    return {**result.to_dict(), "preview_only": True}


def _submit_run_stream(doc: dict, execution_id: str | None, owner: str) -> JSONResponse:
    """``stream:true`` → 立即 202 + 后台线程执行 + SSE 事件扇出（G2）。

    同步分支的 ``DslValidationError`` 是在 ``run_dsl`` 的 try 块**之外**抛出的
    （``compile_dsl`` 在函数首行），若不在这里预校验就会返回一个注定失败的
    202 孤儿 run：前端拿到 run_id 却等不到任何事件。故先同步编译一次，非法立刻
    422。
    """
    try:
        compile_dsl(doc)
    except DslValidationError as exc:
        raise ValidationFailed("validation_failed", str(exc)) from exc

    rid = f"dsl-{uuid.uuid4().hex[:12]}"
    # 必须在 202 返回**之前**、事件循环线程内登记属主：后台线程可能瞬间跑完，
    # 但只要结果还没落 store，此刻的订阅就只能靠这张进程内登记表通过属主校验。
    on_event = dsl_run_events.begin_stream_run(rid, owner)

    def forward(event_type: str, data: dict) -> None:
        # ``run_dsl`` 内部发的 run_finished 早于 ``_store.save``，前端收到后会
        # 立刻 getRun 而 404。故吞掉它，改由 worker 在落库后自行补发。
        if event_type == "run_finished":
            return
        on_event(event_type, data)

    def worker() -> None:
        try:
            result = run_dsl(doc, run_id=rid, on_event=forward,
                             execution_id=execution_id)
        except DslSuspended:
            # 与同步分支 202 suspended 一致：挂起不落 store。
            on_event("run_finished", {"run_id": rid, "status": "suspended",
                                      "output": None, "error": None})
            return
        except Exception as exc:  # noqa: BLE001 - 后台失败必须回传给前端
            on_event("run_finished", {"run_id": rid, "status": "failed",
                                      "output": None, "error": str(exc)})
            return
        _store.save(result, owner=owner)
        # 落库之后才发收尾事件：前端拿到它即可直接 getRun。
        on_event("run_finished", {"run_id": rid, "status": result.status,
                                  "output": result.output, "error": result.error})

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={"status": "running", "run_id": rid})


@router.post("/export-code")
async def export_code(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    """画布 DSL → 受限 Python 代码。非法 DSL（含白名单外动词）422，绝不半成品。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValidationFailed("validation_failed", "请求体不是合法 JSON") from exc
    doc = _extract_doc(payload)
    filename = (payload.get("filename") if isinstance(payload, dict) else None) or None
    try:
        if isinstance(filename, str) and filename:
            return export_dsl_code(doc, filename=filename)
        return export_dsl_code(doc)
    except DslValidationError as exc:
        raise ValidationFailed("validation_failed", str(exc)) from exc


@router.post("/import-code")
async def import_code(request: Request, actor: Actor = Depends(get_actor)) -> dict:
    """受限 Python 代码 → DSL 文档（与export-code 往返无损）。"""
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValidationFailed("validation_failed", "请求体不是合法 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("code"), str):
        raise ValidationFailed(
            "validation_failed", "请求体需为 {\"code\": \"<受限 DSL 代码>\"}")
    try:
        doc = parse_dsl_code(payload["code"])
    except DslValidationError as exc:
        raise ValidationFailed("validation_failed", str(exc)) from exc
    return {"dsl": doc, "topological_order": compile_dsl(doc).order}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, actor: Actor = Depends(get_actor)) -> dict:
    actor.require_authenticated()
    # 属主外一律 404：不泄漏「存在但不属于你」（同 DslFlowStore._own_flow）。
    payload = _store.get_owned(run_id, _actor_identity(actor))
    if payload is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    return payload


@router.get("/runs/{run_id}/events")
async def stream_run_events_route(
        run_id: str, request: Request,
        actor: Actor = Depends(get_actor)) -> StreamingResponse:
    """订阅一次 DSL run 的实时事件（G2 SSE）。

    属主判定有两条通路：进程内登记表（``stream:true`` 提交后、结果尚未落 store
    的 in-flight 窗口）或 store 里的归属（同步提交的历史 run）。两者都不通过
    即 404，绝不泄漏「存在但不属于你」。

    事件总线的访问一律经 ``services/dsl_run_events`` 桥接——路由层不直连
    ``runtime``（分层红线）。
    """
    actor.require_authenticated()
    owner = _actor_identity(actor)
    if dsl_run_events.owner_of(run_id) != owner \
            and _store.get_owned(run_id, owner) is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")

    last_id = int(request.headers.get("last-event-id") or 0)

    return StreamingResponse(
        dsl_run_events.stream_run_events(run_id, last_event_id=last_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs")
async def list_runs(actor: Actor = Depends(get_actor)) -> dict:
    actor.require_authenticated()
    owner = _actor_identity(actor)
    return {"items": [{"run_id": rid} for rid in _store.list_ids(owner)]}


def reset_store_for_tests(archive_dir: str | None = None) -> None:
    """测试专用：清空并可选重设归档目录。"""
    global _store
    _store = DslRunStore(archive_dir=archive_dir)
