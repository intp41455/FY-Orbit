"""W5 工作流工坊 HTTP 接口（一句话生成 + 导出脚本 + 图/DSL 互转）。

端点：

* ``GET  /api/workflow/status``         — ``{"model_configured": bool}``；
  前端据此如实显示「未配置模型」，而不是让用户点了才报 503。
* ``POST /api/workflow/generate``       — ``{prompt}`` → 合法 DSL 文档。
* ``POST /api/workflow/export``         — ``{dsl | dsl_text, prompt?}`` → 独立 Python 脚本。
* ``POST /api/workflow/graph/from-dsl`` — DSL → 图（含坐标）。非法输入报**行列级**
  错误（``error.details.line`` / ``column``），**不做半解析**。
* ``POST /api/workflow/graph/to-dsl``   — 图 → DSL（丢弃坐标），语义非法即报错。

行列级错误的来源分两类，都��**真实**定位，不编造：

* JSON 语法错误 —— 直接用 :class:`json.JSONDecodeError` 的 ``lineno/colno``；
* 语义错误（非法 verb、环、悬空边引用等）—— 服务端校验器只报语义消息，
  这里在原始 DSL 文本里定位出错节点 id / 字面量所在的行列。

诚实契约（总纲铁律 3）：

* 未配置 provider → ``503 model_not_configured``，**绝不**返回预置/占位 DSL；
* 模型两次都给出非法 DSL → ``422 workflow_generation_failed``，
  ``details`` 带真实校验错误原文，不伪造成功；
* 导出脚本不含任何密钥，平台地址是占位符 ``{{FY_BASE_URL}}``。

鉴权：所有端点都要身份（``get_actor``）；写操作再加 CSRF/Origin
（``csrf_protected``），与既有 owner 路由一致。
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..deps import Services, csrf_protected, get_actor, get_services, get_settings
from ...services.actor import Actor
from ...services.dsl_canvas import DslValidationError, compile_dsl
from ...services.workflow_gen import (
    FlowGraph,
    GraphEdge,
    GraphNode,
    WorkflowGenService,
    default_script_name,
    dsl_to_graph,
    export_script,
    graph_to_dsl,
)
from ...services.workflow_gen import _explain_validation_error as _explain

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

#: 语义错误里定位节点 id：``节点 tf1verb 必须是 ...``
_NODE_TOKEN_RE = re.compile(r"节点\s+([A-Za-z0-9_-]{1,64})")
#: 语义错误里定位枚举字面量：``... 必须是 ('map', 'filter', ...)``
_QUOTED_RE = re.compile(r"'([^']+)'")
#: JSON 里定位悬空边引用的节点 id。
_EDGE_REF_RE = re.compile(r"边\s+'?([A-Za-z0-9_-]{1,64})'?")


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 二选一：结构化文档或原始文本（文本模式才有行列级错误）。
    dsl: dict[str, Any] | None = None
    dsl_text: str | None = None
    prompt: str | None = Field(default=None, max_length=4000)
    filename: str | None = Field(default=None, max_length=80)


class FromDslRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dsl: dict[str, Any] | None = None
    dsl_text: str | None = None
    layout: dict[str, dict[str, float]] | None = None


class GraphNodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=32)
    verb: str | None = Field(default=None, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0


class GraphEdgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    condition: dict[str, Any] | None = None


class ToDslRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNodeIn] = Field(default_factory=list, max_length=200)
    edges: list[GraphEdgeIn] = Field(default_factory=list, max_length=400)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _service(svc: Services, settings) -> WorkflowGenService:
    return WorkflowGenService(settings=settings, budget=svc.budget)


class _DslHttpError(Exception):
    """内部信号：带着（可选）行列号的 DSL 非法原因，由端点边界转成响应。

    单独用异常而不是层层 ``return JSONResponse``，是为了让
    ``_parse_dsl_text`` / ``_require_dsl`` / ``_graph_from_payload`` 保持
    「要么给结果要么抛错」的单一出口。
    """

    def __init__(self, message: str, *, line: int | None = None,
                 column: int | None = None, code: str = "workflow_dsl_invalid"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.code = code

    def to_response(self) -> JSONResponse:
        details: dict[str, Any] = {}
        if self.line is not None:
            details["line"] = self.line
        if self.column is not None:
            details["column"] = self.column
        return JSONResponse(
            status_code=422,
            content={"error": {"code": self.code, "message": self.message,
                               "details": details}},
        )


def _parse_dsl_text(raw: str) -> dict[str, Any]:
    """解析 DSL 文本；JSON 语法错误带**真实**行列号。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _DslHttpError(
            f"DSL 文本不是合法 JSON: {exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）",
            line=exc.lineno,
            column=exc.colno,
        ) from exc
    if not isinstance(parsed, dict):
        raise _DslHttpError(
            f"DSL 文档必须是 JSON 对象，实际是 {type(parsed).__name__}",
            line=1, column=1)
    return parsed


def _require_dsl(dsl: dict[str, Any] | None, dsl_text: str | None) -> dict[str, Any]:
    """取出唯一可用的 DSL 来源；两个都没给或都给，明确报错（不猜）。"""
    if dsl is not None and dsl_text is not None:
        raise _DslHttpError("dsl 与 dsl_text 只能提供一个",
                            code="workflow_dsl_ambiguous")
    if dsl is not None:
        return dsl
    if dsl_text is not None:
        return _parse_dsl_text(dsl_text)
    raise _DslHttpError("请求体需提供 dsl 或 dsl_text",
                        code="workflow_dsl_missing")


def _locate(raw: str | None, message: str) -> tuple[int | None, int | None]:
    """在原始 DSL 文本里定位出错 token 的行列（1-based）。

    定位不到就返回 ``(None, None)`` —— **宁可没有位置，也不编造位置**。
    """
    if not raw:
        return None, None
    tokens: list[str] = []
    for pattern in (_NODE_TOKEN_RE, _EDGE_REF_RE):
        for match in pattern.finditer(message):
            tokens.append(match.group(1))
    tokens.extend(_QUOTED_RE.findall(message))
    lines = raw.splitlines()
    for token in tokens:
        if not token:
            continue
        for needle in (f'"{token}"', f"'{token}'", token):
            for index, line in enumerate(lines, start=1):
                col = line.find(needle)
                if col >= 0:
                    return index, col + 1
    return None, None


def _invalid_dsl(raw: str | None, doc: Any, exc: DslValidationError) -> _DslHttpError:
    """把平台的校验异常转成带**行列 + 实际非法取值**的 422。

    两件事分开做，顺序不能反：
    1. ``_locate`` 用**原始** message 找token 位置（富化后的 message 里混了
       JSON 片段，会把定位带偏）；
    2. message 本身用 :func:`_explain_validation_error` 附上模型实际写出的取值，
       否则用户只看到「必须是 ('map','filter','template')」，无从下手改。
    """
    line, column = _locate(raw, str(exc))
    return _DslHttpError(_explain(doc, str(exc)), line=line, column=column)


def _graph_from_payload(
    dsl: dict[str, Any] | None,
    dsl_text: str | None,
    layout: dict[str, dict[str, float]] | None,
) -> FlowGraph:
    """DSL → 图。**先整体校验再解析**；非法时报行列级错误，不半解析。"""
    raw = dsl_text
    doc = _require_dsl(dsl, raw)
    try:
        return dsl_to_graph(doc, layout)
    except DslValidationError as exc:
        raise _invalid_dsl(raw, doc, exc) from exc


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


@router.get("/status")
async def workflow_status(
    actor: Actor = Depends(get_actor),
    settings=Depends(get_settings),
    svc: Services = Depends(get_services),
) -> dict:
    """前端据此如实显示「未配置模型」，而不是点了才炸。"""
    return {"model_configured": _service(svc, settings).model_configured()}


@router.post("/generate")
async def generate_workflow(
    body: GenerateRequest,
    actor: Actor = Depends(csrf_protected),
    settings=Depends(get_settings),
    svc: Services = Depends(get_services),
) -> dict:
    """一句话 → 合法 DSL。无模型 key 时诚实 503；两次都非法则如实 422。"""
    return _service(svc, settings).generate(actor, prompt=body.prompt)


@router.post("/export", response_model=None)
async def export_workflow(body: ExportRequest) -> dict | JSONResponse:
    """合法 DSL → 独立 Python 脚本（不含密钥，地址为占位符）。"""
    doc: dict[str, Any] | None = None
    try:
        doc = _require_dsl(body.dsl, body.dsl_text)
        return export_script(
            doc,
            prompt=body.prompt,
            script_name=body.filename or default_script_name(body.prompt),
        )
    except _DslHttpError as exc:
        return exc.to_response()
    except DslValidationError as exc:
        # 用**解析出来的实际文档**（走 dsl_text 时 body.dsl 是 None）去富化错误。
        return _invalid_dsl(body.dsl_text, doc, exc).to_response()


@router.post("/graph/from-dsl", response_model=None)
async def graph_from_dsl(body: FromDslRequest) -> dict | JSONResponse:
    """DSL → 图。整体校验，非法时报行列级错误，**不半解析**。"""
    try:
        graph = _graph_from_payload(body.dsl, body.dsl_text, body.layout)
    except _DslHttpError as exc:
        return exc.to_response()
    return {"graph": graph.to_dict()}


@router.post("/graph/to-dsl", response_model=None)
async def graph_to_dsl_route(body: ToDslRequest) -> dict | JSONResponse:
    """图 → DSL。坐标被丢弃；语义非法即报错，绝不静默产出坏DSL。"""
    graph = FlowGraph(
        nodes=[
            GraphNode(id=n.id, type=n.type, verb=n.verb,
                      params=dict(n.params or {}), x=n.x, y=n.y)
            for n in body.nodes
        ],
        edges=[
            GraphEdge(from_id=e.from_, to_id=e.to,
                      condition=dict(e.condition) if e.condition else None)
            for e in body.edges
        ],
    )
    dsl = graph_to_dsl(graph)
    try:
        compile_dsl(dsl)
    except DslValidationError as exc:
        return _invalid_dsl(None, dsl, exc).to_response()
    return {"dsl": dsl}