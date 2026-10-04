"""W5 工作流三视图 · 一句话生成 + 导出独立脚本 + 图/DSL 双向序列化。

三层（小白一句话 / 进阶拖拽 / 开发者代码）是**同一个 DSL 源**的三种视图，本模块
只做两件事，正好卡在两侧之间：

1. :meth:`WorkflowGenService.generate` —— 自然语言 → 受限动词集 DSL 文档。
   *走受管网关* :class:`~find_yourself.runtime.gateway.ModelGateway`
   （预算 reserve/settle 由网关统一负责，本模块不重复扣费）；
   *结构化输出*：提示词把模型约束到受限动词集，模型只许输出 DSL JSON；
   *服务端复验*：模型说合法不算合法，产物必须过
   :func:`~find_yourself.services.dsl_canvas.validate_dsl` 与
   :func:`~find_yourself.services.dsl_canvas.compile_dsl`（含环检测）；
   *失败重试一次*：把真实的校验错误回灌进第二次提示词；仍非法则把校验错误
     **如实**抛给调用方（绝不伪造成功、绝不返回占位 DSL）。

2. :func:`export_script` —— DSL 文档 → 独立可运行 Python 脚本。
   脚本自带受限动词集执行器（纯 stdlib），平台地址留占位符
   ``{{FY_BASE_URL}}``；**绝不内嵌任何密钥**。

图↔DSL 序列化（:func:`graph_to_dsl` / :func:`dsl_to_graph`）也在这里，
前端 :mod:`web/src/components/workflow/FlowEditor.tsx` 用同一套语义：
*坐标属于视图，永远不进入 DSL*（claw-dialogue-extraction §1.3）。

诚实边界：本模块**不生产**任何 DSL 兜底内容。无provider 时抛
``ModelNotConfigured``（503）；模型输出非法且重试后仍非法时抛
:class:`WorkflowGenerationFailed`（422），错误里带真实校验信息。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..runtime.gateway import ModelGateway, ModelNotConfigured
from .actor import Actor
from .dsl_canvas import (
    CONDITION_OPS,
    FILTER_OPS,
    INPUT_KINDS,
    MAP_OPS,
    NODE_TYPES,
    OUTPUT_FORMATS,
    TRANSFORM_VERBS,
    DslValidationError,
    compile_dsl,
    validate_dsl,
)
from .errors import DomainError, ValidationFailed

#: 生成阶段的固定开销上限（token）。DSL 文档很小，够用且可预测。
GEN_MAX_TOKENS = 1200

#: 生成失败重试次数上限：任务书要求「非法则自动重试一次，仍非法则如实返回」。
GEN_MAX_ATTEMPTS = 2

#: prompt 长度上限（字符）。防止把整篇文档塞进提示词烧预算。
PROMPT_MAX_CHARS = 2000

#: 重试时回灌「模型上轮原文」的最大字符数（防止提示词无界膨胀）。
_REJECT_ECHO_MAX_CHARS = 1200

#: 导出脚本里的平台地址占位符——由用户自己替换，脚本内绝不写死真实地址。
BASE_URL_PLACEHOLDER = "{{FY_BASE_URL}}"

#: 导出脚本的 Python 版本下限（脚本用到 from __future__ 与内置泛型注解语法）。
MIN_PYTHON = (3, 9)


class WorkflowGenerationFailed(DomainError):
    """模型在两次尝试后仍未给出合法 DSL（校验错误如实上抛）。"""

    http_status = 422
    default_code = "workflow_generation_failed"


# --------------------------------------------------------------------------- #
# 图模型（视图层）：DSL 语义 + 坐标，与 dsl_canvas 的 DSL 文档一一对应
# --------------------------------------------------------------------------- #


@dataclass
class GraphNode:
    """一个 DSL 节点 + 它的视图坐标（坐标不进 DSL）。"""

    id: str
    type: str
    verb: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "verb": self.verb,
                "params": self.params, "x": self.x, "y": self.y}


@dataclass
class GraphEdge:
    """一条数据流边（from → to），condition 可选。"""

    from_id: str
    to_id: str
    condition: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_id, "to": self.to_id, "condition": self.condition}


@dataclass
class FlowGraph:
    """拖拽编辑器持有的完整状态：语义（DSL）+ 视图（坐标）。"""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


def graph_to_dsl(graph: FlowGraph) -> dict[str, Any]:
    """图 → DSL 文档。**坐标被丢弃**，只序列化语义（模型/视图分离）。"""
    doc: dict[str, Any] = {
        "version": "1",
        "nodes": [],
        "edges": [],
    }
    for node in graph.nodes:
        entry: dict[str, Any] = {"id": node.id, "type": node.type}
        if node.type == "transform":
            entry["verb"] = node.verb
        if node.params:
            entry["params"] = node.params
        doc["nodes"].append(entry)
    for edge in graph.edges:
        entry = {"from": edge.from_id, "to": edge.to_id}
        if edge.condition:
            entry["condition"] = edge.condition
        doc["edges"].append(entry)
    return doc


def dsl_to_graph(doc: dict[str, Any], layout: dict[str, dict[str, float]] | None = None) -> FlowGraph:
    """DSL 文档 → 图。**先整体校验再解析**（不半解析）。

    ``layout`` 是可选的坐标字典（``{node_id: {x, y}}``）；未提供的节点按
    网格自动排布，保证载入后立刻可见、不需要用户先手动拖一遍。
    """
    validate_dsl(doc)
    lay = layout or {}
    nodes: list[GraphNode] = []
    for index, raw in enumerate(doc["nodes"]):
        nid = raw["id"]
        pos = lay.get(nid) or _auto_position(index)
        nodes.append(GraphNode(
            id=nid,
            type=raw["type"],
            verb=raw.get("verb"),
            params=dict(raw.get("params") or {}),
            x=float(pos["x"]),
            y=float(pos["y"]),
        ))
    edges = [
        GraphEdge(from_id=e["from"], to_id=e["to"],
                  condition=dict(e["condition"]) if e.get("condition") else None)
        for e in doc["edges"]
    ]
    return FlowGraph(nodes=nodes, edges=edges)


def _auto_position(index: int) -> dict[str, float]:
    """未给坐标时的网格排布（每行 3 个）。"""
    col = index % 3
    row = index // 3
    return {"x": 40.0 + col * 190.0, "y": 40.0 + row * 110.0}


# --------------------------------------------------------------------------- #
# 提示词构造：把模型约束在受限动词集内
# --------------------------------------------------------------------------- #

_VERB_HELP = f"""受限动词集（只能使用以下类型与动词，绝不允许自创）：

- 节点 type：{list(NODE_TYPES)}
  - input：读取数据。params = {{"kind": "literal", "value": <任意 JSON>}}
            或 {{"kind": "text_lines", "value": "<多行文本>"}}
            （kind 只能是 {list(INPUT_KINDS)}）
  - transform：必须带 verb，verb 只能是 {list(TRANSFORM_VERBS)}
    - verb="map"：params = {{"op": "set", "field": "<字段名>", "value": "<模板>"}}
                   或 {{"op": "upper"}} / {{"op": "lower"}}
                   （map.op 只能是 {list(MAP_OPS)}）
    - verb="filter"：params = {{"field": "<字段名>", "op": "<比较符>", "value": <JSON>}}
                   （op 只能是 {list(FILTER_OPS)}）
    - verb="template"：params = {{"template": "含 {{field}} 插值的模板"}}
  - output：params = {{"format": "json"}} 或 {{"format": "text"}}
            （format 只能是 {list(OUTPUT_FORMATS)}）
- 边：{{"from": "<上游节点 id>", "to": "<下游节点 id>"}}；
  可选 condition = {{"field": "<字段名>", "op": "<比较符>", "value": <JSON>}}
  （op 只能是 {list(CONDITION_OPS)}）
- 节点 id 只允许字母、数字、下划线、连字符（^[A-Za-z0-9_-]+$），最长 64 字符。
- 图必须是有向无环的：不得存在环。"""

_JSON_SHAPE = """{
  "version": "1",
  "nodes": [
    {"id": "in1", "type": "input", "params": {"kind": "literal", "value": [{"text": "示例行"}]}},
    {"id": "tf1", "type": "transform", "verb": "template",
     "params": {"template": "处理：{text}"}},
    {"id": "out1", "type": "output", "params": {"format": "text"}}
  ],
  "edges": [
    {"from": "in1", "to": "tf1"},
    {"from": "tf1", "to": "out1"}
  ]
}"""


def build_generation_prompt(
    requirement: str,
    *,
    feedback: str | None = None,
    rejected: str | None = None,
) -> str:
    """构造一句话生成的提示词。

    ``feedback`` 非空时（第二次尝试），把上一轮的**真实校验错误**回灌进去，
    让模型知道具体哪里不合法；``rejected`` 同时回灌模型自己那轮被拒的原文，
    这样「你写的是什么」和「为什么被判非法」都在上下文里——这是唯一允许的
    「重试」含义。
    """
    lines = [
        "你是 Find Yourself 工作流编排器。把用户需求翻译成一份受限 DSL 文档。",
        "",
        "[用户需求]",
        requirement.strip(),
        "",
        "[DSL 规范]",
        _VERB_HELP,
        "",
        "[输出格式 —— 硬性要求]",
        "1. 只输出一个 JSON 对象，不要 Markdown 代码块、不要解释、不要注释。",
        "2. 顶层键只能是 version / nodes / edges，version 固定为字符串 \"1\"。",
        "3. 节点至少一个；每个节点必须有 id 与 type。",
        "4. 至少要有一个 input 节点作为数据源、一个 output 节点作为数据出口。",
        "5. 必须严格用上面列出的 type / verb / op / format 取值，不得自创。",
        "6. 图必须无环。",
        (
            "7. input 的 kind=\"literal\" 若要喂给 template 插值，value 必须是"
            "**对象数组**（如 [{\"text\": \"示例\"}]），模板里写 {text}；"
            "纯字符串数组只能被 map 的 upper/lower 处理。"
        ),
        "",
        "[输出示例（结构参考，不要照抄内容）]",
        _JSON_SHAPE,
    ]
    if feedback:
        lines += [
            "",
            "[上一轮输出的真实校验错误 —— 必须逐条修正后重新输出完整 JSON]",
            feedback,
        ]
    if rejected:
        lines += [
            "",
            "[上一轮你实际输出的内容（因此被判为非法，请勿重复）]",
            rejected[:_REJECT_ECHO_MAX_CHARS],
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 模型输出解析：只接受纯 JSON，容错剥一层 Markdown 代码块
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型回复里取出唯一的 JSON 对象。

    容错范围**仅限**剥 ```` ```json ```` 代码块与截取首个 ``{`` 到末个 ``}``
    之间的文本；除此之外任何解析失败都抛错（不做猜测式修补）。
    """
    raw = (text or "").strip()
    if not raw:
        raise DslValidationError("模型返回了空内容")
    stripped = _FENCE_RE.sub("", raw).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise DslValidationError(
                "模型输出不是合法 JSON（找不到完整的 {...} 结构）") from None
        try:
            parsed = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError as exc:
            raise DslValidationError(f"模型输出不是合法 JSON: {exc.msg}（第 {exc.lineno} 行）") from None
    if not isinstance(parsed, dict):
        raise DslValidationError(
            f"模型输出必须是 JSON 对象，实际是 {type(parsed).__name__}")
    return parsed


def _explain_validation_error(doc: Any, message: str) -> str:
    """给校验错误补上**模型实际写出的非法取值**。

    平台校验器（``services/dsl_canvas.py``）报的是「必须是 (map, filter, template)」
    这类**允许集**，不说模型到底写成了什么。重试时模型需要看到自己的错处才能
    自我修正，所以这里在不改动平台校验语义的前提下，从它给出的文档里把涉事
    节点/边的实际取值摘出来附在后面。摘不到就原样返回——不编造。
    """
    if not isinstance(doc, dict):
        return message
    m = re.search(r"节点\s+([A-Za-z0-9_-]{1,64})", message)
    if m:
        for node in doc.get("nodes") or []:
            if isinstance(node, dict) and node.get("id") == m.group(1):
                actual = {k: v for k, v in node.items() if k in ("type", "verb", "kind",
                                                                 "format", "op", "field")}
                params = node.get("params")
                if isinstance(params, dict):
                    actual["params_keys"] = sorted(params.keys())
                return f"{message}（该节点实际为: {json.dumps(actual, ensure_ascii=False)}）"
    m = re.search(r"边\s+'?([A-Za-z0-9_-]{1,64})'?", message)
    if m:
        for edge in doc.get("edges") or []:
            if isinstance(edge, dict) and edge.get("from") == m.group(1):
                return (f"{message}（该边实际为: "
                        f"{json.dumps(edge, ensure_ascii=False)}）")
    return message


# --------------------------------------------------------------------------- #
# 生成服务
# --------------------------------------------------------------------------- #


class WorkflowGenService:
    """一句话 → 受限 DSL；导出独立脚本。图↔DSL 序列化是模块级纯函数。"""

    def __init__(
        self,
        *,
        settings: Any = None,
        budget: Any = None,
        model_gateway: ModelGateway | None = None,
    ):
        self._settings = settings
        self._budget = budget
        self._model_gateway = model_gateway

    # -- gateway plumbing（与 butler 同范式） --------------------------------
    def _gateway(self) -> ModelGateway:
        if self._model_gateway is not None:
            return self._model_gateway
        if self._settings is None:
            raise ModelNotConfigured(_NOT_CONFIGURED_HINT)
        self._model_gateway = ModelGateway(self._settings, self._budget)
        return self._model_gateway

    def _model_name(self) -> str:
        return getattr(self._settings, "model_name", None) or "gpt-4o-mini"

    def model_configured(self) -> bool:
        """True 仅当真的有可调用的provider（驱动前端按钮的诚实态）。"""
        try:
            gateway = self._gateway()
        except ModelNotConfigured:
            return False
        return bool(gateway.configured or gateway.provider is not None)

    def build_prompt(self, requirement: str, *, feedback: str | None = None) -> str:
        """公开的提示词构造（测试与调试用）。"""
        return build_generation_prompt(requirement, feedback=feedback)

    # -- 生成 ---------------------------------------------------------------
    def generate(self, actor: Actor, *, prompt: str) -> dict[str, Any]:
        """一句话生成合法 DSL 文档。

        诚实失败契约：
        *未配置 provider* → ``ModelNotConfigured``（503，前端提示去配置）；
        *两次都非法* → :class:`WorkflowGenerationFailed`（422），``details``
          里带**真实的校验错误**，不做任何兜底伪造。
        """
        actor.require_authenticated()
        requirement = (prompt or "").strip()
        if not requirement:
            raise ValidationFailed("workflow_prompt_empty", "请先描述你的工作流需求")
        if len(requirement) > PROMPT_MAX_CHARS:
            raise ValidationFailed(
                "workflow_prompt_too_long",
                f"需求描述超过 {PROMPT_MAX_CHARS} 字符，请精简后重试")

        gateway = self._gateway()
        model = self._model_name()
        task_id = f"workflow-gen:{(actor.owner_id or actor.service_id or 'anonymous')}"

        feedback: str | None = None
        rejected: str | None = None
        last_error = ""
        attempts: list[dict[str, Any]] = []

        for attempt in range(1, GEN_MAX_ATTEMPTS + 1):
            text_prompt = build_generation_prompt(
                requirement, feedback=feedback, rejected=rejected)
            try:
                result = gateway.complete(
                    actor,
                    task_id=task_id,
                    model=model,
                    prompt=text_prompt,
                    target_domain="work",
                    max_tokens=GEN_MAX_TOKENS,
                )
            except ModelNotConfigured:
                raise ModelNotConfigured(_NOT_CONFIGURED_HINT) from None
            except DomainError:
                raise
            except Exception as exc:  # 网络/传输层：如实报类型，不编造
                raise DomainError(
                    "model_unavailable",
                    f"Model call failed: {type(exc).__name__}", 503,
                ) from exc

            raw_text = result.text
            # doc 预置为 None：模型输出若连 JSON 都不是，就没有文档可「解释」，
            # _explain_validation_error(None, ...) 会原样返回 message（不编造）。
            doc: dict[str, Any] | None = None
            try:
                doc = extract_json_object(raw_text)
                # 服务端复验：结构（validate_dsl）+ 拓扑/环（compile_dsl）。
                # 「模型说合法」不算合法。
                validate_dsl(doc)
                compile_dsl(doc)
            except DslValidationError as exc:
                # 附上模型实际写出的非法取值，重试时模型才能自我修正。
                last_error = _explain_validation_error(doc, str(exc))
                attempts.append({"attempt": attempt, "ok": False, "error": last_error})
                # 回灌真实错误 + 模型自己那轮被拒的原文，进入下一次尝试。
                feedback = last_error
                rejected = raw_text
                continue

            attempts.append({"attempt": attempt, "ok": True, "error": ""})
            return {
                "dsl": doc,
                "model": getattr(result, "model", "") or model,
                "provider_id": getattr(result, "provider_id", "") or "",
                "attempts": attempts,
                "usage": result.usage,
                "settled_usd": str(result.settled_amount),
                # 降级可见（FROZEN_CONTRACT §7：不得静默换模型）
                "degraded_from": getattr(result, "degraded_from", "") or "",
                "degraded_reason": getattr(result, "degraded_reason", "") or "",
            }

        # 两次都非法：把真实校验错误如实上抛，不返回任何占位 DSL。
        raise WorkflowGenerationFailed(
            "workflow_generation_failed",
            f"模型在{GEN_MAX_ATTEMPTS} 次尝试后仍未给出合法 DSL：{last_error}",
            422,
        )

    # -- 导出 ---------------------------------------------------------------
    def export(self, actor: Actor, *, dsl: dict[str, Any], prompt: str | None = None) -> dict[str, Any]:
        """校验并导出独立 Python 脚本。非法 DSL 直接 422，不生成半成品。"""
        actor.require_authenticated()
        return export_script(dsl, prompt=prompt)


_NOT_CONFIGURED_HINT = (
    "No model provider is configured for workflow generation. "
    "Set FY_MODEL_API_KEY and FY_MODEL_BASE_URL (OpenAI-compatible endpoint, "
    "e.g. DeepSeek or a local Ollama) to enable 「一句话生成」."
)


# --------------------------------------------------------------------------- #
# 导出：独立 Python 脚本
# --------------------------------------------------------------------------- #

#: 脚本里的执行器模板。用 str.format 的命名占位符，避免与 CSS/JSON 花括号打架，
#: 因此模板里所有字面花括号都写成双花括号。
_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find Yourself 工作流导出脚本（由「工作流工坊 · W5」生成）。

来源需求：{source_prompt}
生成时间：{generated_at}
导出DSL version：{dsl_version}（节点 {node_count} 个 / 边 {edge_count} 条）

本脚本是**自包含**的：只用 Python 标准库，内置了与平台
``services/dsl_canvas.py`` 语义一致的受限动词集解释器，在干净环境里
``python {script_name}`` 直接可跑，不需要安装任何依赖，也**不包含任何密钥**。

平台地址占位符
--------------
脚本里的 ``BASE_URL = "{base_url_placeholder}"`` 是占位符，仅在你把
``--via-platform`` 打开、改为调用平台运行接口时才需要替换成你自己的平台地址
（例如 ``http://127.0.0.1:8000``）。默认的本地顺序执行模式完全不需要它。

用法
----
    python {script_name}                  # 本地顺序执行（默认，无需网络）
    python {script_name} --payload '[{{"name": "x"}}]'   # 覆盖 input 的运行数据
    python {script_name} --via-platform                # 调用平台 run 接口（需替换 BASE_URL）
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

#: 平台地址占位符 —— 由使用者替换为本机平台地址；本地模式不使用。
BASE_URL = "{base_url_placeholder}"

DSL = {dsl_literal}

VALID_NODE_TYPES = {node_types!r}
VALID_VERBS = {verbs!r}
VALID_INPUT_KINDS = {input_kinds!r}
VALID_OUTPUT_FORMATS = {output_formats!r}
VALID_MAP_OPS = {map_ops!r}
VALID_FILTER_OPS = {filter_ops!r}
VALID_CONDITION_OPS = {condition_ops!r}


# --------------------------------------------------------------------------- #
# 受限动词集解释器（与平台 services/dsl_canvas.py 语义一致）
# --------------------------------------------------------------------------- #

import re

_TEMPLATE_RE = re.compile(r"\\{{([A-Za-z0-9_.\\[\\]]+)\\}}")


class DslError(Exception):
    """DSL 结构 / 参数 / 执行期错误。"""


def validate(doc):
    """结构 + 参数校验（对应平台的 validate_dsl）。"""
    if not isinstance(doc, dict):
        raise DslError("DSL 文档必须是 JSON 对象")
    if doc.get("version") != "1":
        raise DslError('version 必须为 "1"')
    nodes = doc.get("nodes")
    edges = doc.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise DslError("nodes 必须是非空数组")
    if not isinstance(edges, list):
        raise DslError("edges 必须是数组")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise DslError("每个节点必须有非空字符串 id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{{1,64}}", node["id"]):
            raise DslError("节点 id 不合法: %r" % node["id"])
        if node["id"] in ids:
            raise DslError("节点 id 重复: %s" % node["id"])
        ids.add(node["id"])
        if node.get("type") not in VALID_NODE_TYPES:
            raise DslError("节点 %s type 必须是 %r" % (node["id"], VALID_NODE_TYPES))
        params = node.get("params", {{}})
        if not isinstance(params, dict):
            raise DslError("节点 %s params 必须是对象" % node["id"])
        if node["type"] == "transform":
            verb = node.get("verb")
            if verb not in VALID_VERBS:
                raise DslError("transform 节点 %s verb 必须是 %r" % (node["id"], VALID_VERBS))
            _validate_transform(node["id"], verb, params)
    for edge in edges:
        if not isinstance(edge, dict):
            raise DslError("边必须是对象")
        src, dst = edge.get("from"), edge.get("to")
        if src not in ids or dst not in ids:
            raise DslError("边 %r->%r 引用了未定义节点" % (src, dst))
        cond = edge.get("condition")
        if cond is not None:
            if not isinstance(cond, dict) or cond.get("op") not in VALID_CONDITION_OPS \\
                    or not isinstance(cond.get("field"), str) or not cond["field"]:
                raise DslError("边 %s->%s condition 需要 field/op/value" % (src, dst))
    return doc


def _validate_transform(node_id, verb, params):
    if verb == "map":
        op = params.get("op")
        if op not in VALID_MAP_OPS:
            raise DslError("节点 %s map.op 必须是 %r" % (node_id, VALID_MAP_OPS))
        if op == "set" and not isinstance(params.get("field"), str):
            raise DslError("节点 %s map.set 需要 field" % node_id)
    elif verb == "filter":
        if params.get("op") not in VALID_FILTER_OPS or not isinstance(params.get("field"), str):
            raise DslError("节点 %s filter 需要 field + op%r" % (node_id, VALID_FILTER_OPS))
    elif verb == "template":
        if not isinstance(params.get("template"), str) or not params["template"]:
            raise DslError("节点 %s template 需要 template 字符串" % node_id)


def topological_order(doc):
    """Kahn 拓扑排序 + 环检测（对应平台的 compile_dsl）。"""
    nodes = {{n["id"]: n for n in doc["nodes"]}}
    indegree = {{nid: 0 for nid in nodes}}
    adjacency = {{nid: [] for nid in nodes}}
    for edge in doc["edges"]:
        adjacency[edge["from"]].append(edge)
        indegree[edge["to"]] += 1
    ready = sorted(nid for nid, d in indegree.items() if d == 0)
    order = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for edge in adjacency[nid]:
            indegree[edge["to"]] -= 1
            if indegree[edge["to"]] == 0:
                ready.append(edge["to"])
        ready.sort()
    if len(order) != len(nodes):
        cyclic = sorted(nid for nid, d in indegree.items() if d > 0)
        raise DslError("DSL 存在环，涉及节点: %s" % cyclic)
    return order, nodes, adjacency, indegree


def lookup(item, path):
    cur = item
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def compare(left, op, right):
    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "gt":
            return left is not None and right is not None and left > right
        if op == "lt":
            return left is not None and right is not None and left < right
        if op == "contains":
            return left is not None and right in left
    except TypeError:
        return False
    return False


def interpolate(template, item):
    def repl(match):
        val = lookup(item, match.group(1))
        return "" if val is None else str(val)
    return _TEMPLATE_RE.sub(repl, template)


def execute_node(node, payload):
    ntype = node["type"]
    params = node.get("params", {{}}) or {{}}
    if ntype == "input":
        kind = params.get("kind", "literal")
        if kind == "text_lines":
            text = params.get("value", "")
            if not isinstance(text, str):
                raise DslError("input 节点 %s text_lines.value 必须是字符串" % node["id"])
            return [line for line in text.splitlines() if line.strip()]
        if kind == "literal":
            return params.get("value")
        raise DslError("input 节点 %s kind 必须是 %r" % (node["id"], VALID_INPUT_KINDS))
    if ntype == "transform":
        verb = node["verb"]
        if payload is None:
            raise DslError("transform 节点 %s 无上游输入" % node["id"])
        if verb == "map":
            items = payload if isinstance(payload, list) else [payload]
            op = params["op"]
            out = []
            for item in items:
                if op == "set":
                    if not isinstance(item, dict):
                        item = {{"value": item}}
                    item = dict(item)
                    item[params["field"]] = interpolate(str(params.get("value", "")), item)
                elif op == "upper":
                    item = item.upper() if isinstance(item, str) else item
                elif op == "lower":
                    item = item.lower() if isinstance(item, str) else item
                out.append(item)
            return out
        if verb == "filter":
            items = payload if isinstance(payload, list) else [payload]
            return [i for i in items
                    if compare(lookup(i, params["field"]), params["op"], params.get("value"))]
        if verb == "template":
            tpl = params["template"]
            if isinstance(payload, list):
                return [interpolate(tpl, i) for i in payload]
            return interpolate(tpl, payload)
        raise DslError("未知的 transform verb: %s" % verb)
    if ntype == "output":
        fmt = params.get("format", "json")
        if fmt == "text":
            if isinstance(payload, list):
                return "\\n".join(str(x) for x in payload)
            return "" if payload is None else str(payload)
        if fmt == "json":
            return payload
        raise DslError("output 节点 %s format 必须是 %r" % (node["id"], VALID_OUTPUT_FORMATS))
    raise DslError("未知节点类型: %s" % ntype)


def run(doc, payload_override=None):
    """顺序执行一份 DSL 文档，返回 (输出, 逐步日志)。

    调度语义与平台一致：入度 0 的先跑；节点成功后按出边条件决定后继是否入队，
    全部入边条件都不满足的后继记为 skipped。
    """
    validate(doc)
    order, nodes, adjacency, _ = topological_order(doc)
    outputs = {{}}
    triggered = {{nid: False for nid in nodes}}
    indegree_map = {{nid: 0 for nid in nodes}}
    for edge in doc["edges"]:
        indegree_map[edge["to"]] += 1
    pending = dict(indegree_map)
    queue = [nid for nid in order if pending[nid] == 0]
    logs = []

    # payload 覆盖：只改写 input 字面量，语义与平台一致（仍是同一条数据流）。
    if payload_override is not None:
        _apply_payload(doc, outputs, payload_override)

    while queue:
        queue.sort(key=lambda nid: order.index(nid))
        nid = queue.pop(0)
        node = nodes[nid]
        incoming = [e["from"] for e in doc["edges"] if e["to"] == nid]
        up = None
        for src in incoming:
            if outputs.get(src) is not None:
                up = outputs[src]
                break
        try:
            out = execute_node(node, up)
            outputs[nid] = out
            logs.append({{"node_id": nid, "status": "succeeded", "output": out}})
        except Exception as exc:
            logs.append({{"node_id": nid, "status": "failed", "error": str(exc)}})
            for edge in adjacency[nid]:
                dst = edge["to"]
                pending[dst] -= 1
            continue
        for edge in adjacency[nid]:
            cond = edge.get("condition")
            passed = True if not cond else compare(
                lookup(out, cond["field"]), cond["op"], cond.get("value"))
            if passed:
                triggered[edge["to"]] = True
                if pending[edge["to"]] > 0:
                    pending[edge["to"]] -= 1
                    if pending[edge["to"]] == 0:
                        queue.append(edge["to"])
            else:
                pending[edge["to"]] -= 1
    for nid in order:
        if not any(l["node_id"] == nid for l in logs):
            logs.append({{"node_id": nid, "status": "skipped"}})

    outs = [outputs[n["id"]] for n in doc["nodes"]
            if n["type"] == "output" and n["id"] in outputs]
    result = outs[-1] if outs else outputs.get(order[-1])
    return result, logs


def _apply_payload(doc, outputs, payload_override):
    """把 --payload 写进第一个 input 字面量节点（无 input 节点则忽略）。"""
    for node in doc["nodes"]:
        if node.get("type") == "input" and (node.get("params") or {{}}).get("kind", "literal") == "literal":
            node.setdefault("params", {{}})["value"] = payload_override
            return


def run_via_platform(base_url, doc):
    """调用平台运行接口（可选路径，需要 CSRF 会话 cookie，故默认关闭）。"""
    url = base_url.rstrip("/") + "/api/dsl-canvas/runs"
    body = json.dumps({{"dsl": doc}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={{"Content-Type": "application/json"}})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit("调用平台失败: %s" % type(exc).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description="运行导出的 Find Yourself 工作流")
    parser.add_argument("--payload", default=None,
                        help="JSON 文本，覆盖 input 字面量节点的数据")
    parser.add_argument("--via-platform", action="store_true",
                        help="改为调用平台 run 接口（需先把 BASE_URL 占位符替换为真实地址）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果与逐步日志")
    args = parser.parse_args(argv)

    if args.via_platform:
        if BASE_URL == "{base_url_placeholder}":
            print("错误：--via-platform 需要先把脚本里的 BASE_URL 占位符 "
                  "替换为真实平台地址。", file=sys.stderr)
            return 2
        print(json.dumps(run_via_platform(BASE_URL, DSL), ensure_ascii=False, indent=2))
        return 0

    payload = None
    if args.payload is not None:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print("--payload 不是合法 JSON: %s" % exc.msg, file=sys.stderr)
            return 2

    try:
        result, logs = run(DSL, payload_override=payload)
    except DslError as exc:
        print("DSL 执行失败: %s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({{"output": result, "logs": logs}}, ensure_ascii=False, indent=2))
    else:
        print("=== 输出 ===")
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        print("=== 逐步日志 ===")
        for entry in logs:
            line = "  [%s] %s" % (entry["status"], entry["node_id"])
            if entry.get("error"):
                line += " -> %s" % entry["error"]
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def export_script(
    dsl: dict[str, Any],
    *,
    prompt: str | None = None,
    script_name: str = "fy_workflow.py",
) -> dict[str, Any]:
    """把一份合法 DSL 导出成独立可运行的 Python 脚本。

    先过 :func:`validate_dsl` + :func:`compile_dsl`（含环检测）：非法 DSL
    一律抛 :class:`DslValidationError`，**绝不生成半成品脚本**。

    脚本内**不含任何密钥**；平台地址一律是占位符 ``{{FY_BASE_URL}}``。
    """
    validate_dsl(dsl)
    compile_dsl(dsl)  # 环检测：导出的脚本必须能跑

    source_prompt = (prompt or "").strip()
    if not source_prompt:
        # 没有来源 prompt 时如实标注，不编造。
        source_prompt = "（未记录：本次导出未提供来源需求）"

    # 用 JSON 字面量嵌入 DSL：中文按 \uXXXX 转义，保证脚本在任何编码环境可读。
    dsl_literal = json.dumps(dsl, ensure_ascii=True, indent=2, sort_keys=False)
    generated_at = datetime.now(timezone.utc).isoformat()

    script = _SCRIPT_TEMPLATE.format(
        source_prompt=source_prompt,
        generated_at=generated_at,
        dsl_version=str(dsl.get("version")),
        node_count=len(dsl.get("nodes") or []),
        edge_count=len(dsl.get("edges") or []),
        script_name=script_name,
        base_url_placeholder=BASE_URL_PLACEHOLDER,
        dsl_literal=dsl_literal,
        node_types=list(NODE_TYPES),
        verbs=list(TRANSFORM_VERBS),
        input_kinds=list(INPUT_KINDS),
        output_formats=list(OUTPUT_FORMATS),
        map_ops=list(MAP_OPS),
        filter_ops=list(FILTER_OPS),
        condition_ops=list(CONDITION_OPS),
    )
    return {
        "filename": script_name,
        "language": "python",
        "requires_python": ">=3.9",
        "base_url_placeholder": BASE_URL_PLACEHOLDER,
        "source_prompt": source_prompt,
        "generated_at": generated_at,
        "script": script,
    }


def default_script_name(prompt: str | None = None) -> str:
    """由需求派生一个安全的脚本文件名（无需求时用默认名）。"""
    if not prompt:
        return "fy_workflow.py"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", prompt.strip())[:40].strip("_")
    return f"fy_workflow_{slug}.py" if slug else "fy_workflow.py"


__all__ = [
    "BASE_URL_PLACEHOLDER",
    "FlowGraph",
    "GEN_MAX_ATTEMPTS",
    "GEN_MAX_TOKENS",
    "GraphEdge",
    "GraphNode",
    "PROMPT_MAX_CHARS",
    "WorkflowGenService",
    "WorkflowGenerationFailed",
    "build_generation_prompt",
    "default_script_name",
    "dsl_to_graph",
    "export_script",
    "extract_json_object",
    "graph_to_dsl",
]