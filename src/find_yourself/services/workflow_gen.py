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
    AGGREGATE_OPS,
    CODE_LANGUAGES,
    CODE_TIMEOUT_CAP_SECONDS,
    CONDITION_OPS,
    DEFAULT_RECURSION_LIMIT,
    FILTER_OPS,
    HTTP_METHODS,
    INPUT_KINDS,
    ITERATION_DEFAULT_MAX_ITEMS,
    ITERATION_MAX_ITEMS_CAP,
    LOOP_DEFAULT_MAX_ITERATIONS,
    LOOP_MAX_ITERATIONS_CAP,
    MAP_OPS,
    MAX_EDGES_PER_GRAPH,
    MAX_NODES_PER_GRAPH,
    MERGE_OPS,
    NODE_TYPES,
    OUTPUT_FORMATS,
    PARALLEL_MAX_BRANCHES,
    SUBFLOW_MAX_DEPTH,
    TRANSFORM_VERBS,
    TRIGGER_KINDS,
    VARIABLE_AGGREGATOR_STRATEGIES,
    VERB_REGISTRY,
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

#: 每个动词给模型的一句参数示例。
#: 由注册表派生：新增动词只需改 dsl_canvas.VERB_REGISTRY，本表与提示词自动跟上。
#: 花括号说明：这些串通过 :data:`_VERB_HELP_LINES` **代入** f-string，代入值不再
#: 二次格式化，所以这里必须写**单层**花括号（与 _VERB_HELP 内联字面量的双层写法
#: 相反——那处是被 f-string 格式化的）。
_VERB_EXAMPLES: dict[str, str] = {
    "map": 'params = {"op": "set", "field": "<字段名>", "value": "<模板>"}'
           ' 或 {"op": "upper"} / {"op": "lower"}'
           f'（map.op 只能是 {list(MAP_OPS)}）',
    "filter": 'params = {"field": "<字段名>", "op": "<比较符>", "value": <JSON>}'
              f'（op 只能是 {list(FILTER_OPS)}）',
    "template": 'params = {"template": "含 {field} 插值的模板"}',
    "branch": 'params = {"field": "<字段名>", "op": "<比较符>", "value": <JSON>, '
              '"then_label": "<命中标签>", "else_label": "<未命中标签>"}'
              '（产出 {"branch": 标签, "value": 载荷}，供下游边 condition 路由）',
    "aggregate": 'params = {"op": "<聚合算子>", "field": "<字段名>", "sep": "<连接符>"}'
                 f'（op 只能是 {list(AGGREGATE_OPS)}；sum/min/max/avg 必须给 field，'
                 'join 用 sep 默认 ","）',
    "merge": 'params = {"mode": "<汇聚策略>"}'
             f'（mode 只能是 {list(MERGE_OPS)}；把多路入边收敛成一路）',
    "agent": 'params = {"agent": "<已注册 Agent 名>"}'
             '（需要平台注入 Agent 解析器；未接入时该节点会失败）',
    "confirm": 'params = {"prompt": "<给人看的确认话术>", "role": "<角色>"}'
               '（人工确认动词位，HITL 尚未接入，执行必定失败——除非任务确实需要人工）',
    "artifact": 'params = {"name": "<产物名>", "kind": "<产物类型>"}'
                '（产出 {"artifact": 名字, "kind": 类型, "content": 载荷}）',
}

#: 提示词里的动词段落。**只列出注册表里真实存在的动词**，不多不少。
_VERB_HELP_LINES = "\n".join(
    f'    - verb="{name}"：{_VERB_EXAMPLES.get(name, spec.summary)}'
    for name, spec in VERB_REGISTRY.items()
)

#: 生成阶段的动词说明。**由注册表派生**，保证提示词与平台校验永远同一套语义
#: （新增动词只需改 dsl_canvas.VERB_REGISTRY，提示词自动跟上）。
_VERB_HELP = f"""受限动词集（只能使用以下类型与动词，绝不允许自创）：

- 节点 type：{list(NODE_TYPES)}
  - input：读取数据。params = {{"kind": "literal", "value": <任意 JSON>}}
            或 {{"kind": "text_lines", "value": "<多行文本>"}}
            （kind 只能是 {list(INPUT_KINDS)}）
  - transform：必须带 verb，verb 只能是 {list(TRANSFORM_VERBS)}
{_VERB_HELP_LINES}
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

    def _ensure_gen_task(self, actor: Actor, task_id: str) -> None:
        """幂等自建一句话生成的预算归集任务行（无 session/无 budget 时跳过）。

        gateway 的 budget.reserve 只认已存在的 Task 行；归集行按 owner 稳定
        复用（id 即 task_id），goal 写明用途，绝不伪装成用户业务任务。
        """
        budget = self._budget
        session = getattr(budget, "s", None) if budget is not None else None
        if session is None:
            return
        from datetime import timedelta

        from ..db.models import Task

        if session.get(Task, task_id) is not None:
            return
        session.add(Task(
            id=task_id,
            owner_id=actor.owner_id or "anonymous",
            goal="一句话生成工作流的模型费用归集（系统自建，非用户业务任务）",
            domain="work",
            status="queued",
            deadline=datetime.now(timezone.utc) + timedelta(days=365),
            idempotency_key=f"gen-task-{task_id}",
        ))
        session.flush()

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
        # P0-7：gateway 调 budget.reserve(task_id) 要求 Task 行存在；
        # 一句话生成的归集任务行此前从未创建，线上直接 Task not found。
        # 这里幂等自建（同 owner 复用同一行），费用归集有处可落。
        self._ensure_gen_task(actor, task_id)

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

#: 受限解释器源码（导出脚本内联用）。
#:
#: 整段逐字搬运自平台 ``services/dsl_canvas.py``（同一套节点/动词语义），
#: 只做四处与运行环境有关的手术：
#:   1. ``code`` 子进程引导脚本的定界符 ``r'''`` -> ``r"""``（避免嵌套截断）；
#:   2. ``compile_dsl`` 摘掉 ADR-02 类型化 IR 闸门（``dsl_ir`` 依赖 pydantic
#:      且反向依赖 VERB_REGISTRY，搬不进离线单文件），并在 docstring 说明；
#:   3. ``VERB_REGISTRY["approval"].check`` 置 ``None``（``_check_approval``
#:      惰性 ``from .proposal import``，依赖 SQLAlchemy，平台侧不可搬）；
#:   4. 两类异常改挂脚本内的 ``DslError``（入口只捕这一种）。
#: 另加前置块（平台常量别名 + 未下发的固定枚举 + ``DslError``）与尾部适配段
#: （``run()`` / ``_apply_payload()``），把 ``run_dsl`` 的 ``RunResult`` 转成
#: 导出脚本入口要的 ``(输出, 逐步日志)``。
#:
#: 以 ``str.format`` 的命名占位符注入 ``_SCRIPT_TEMPLATE``：**替换值不会被二次
#: 处理**，因此下面这 2100 行里的 ``{}`` / ``{x}`` 无需翻倍转义。
_INTERPRETER_SOURCE = r'''import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class DslError(Exception):
    """导出脚本里**唯一**的 DSL 失败根类型。

    搬运来的解释器抛 :class:`DslValidationError`（含子类 :class:`DslFieldError`）
    与 :class:`DslSuspended`，三者已统一挂到本类下；入口 ``main()`` 只
    ``except DslError`` 一次，就能把「结构/参数非法」「字段定位失败」「人工挂起」
    统统收成一句可读的 stderr，而不是漏出裸 traceback。
    """


# -- 平台常量别名 -----------------------------------------------------------
# 解释器内部沿用平台 ``services/dsl_canvas.py`` 的原名（``NODE_TYPES`` 等），而
# 脚本头部按「导出面」把它们命名为 ``VALID_*``（用户可直接读、直接改）。这里做
# 一次性别名，而不是为迁就导出脚本去改两千行搬运源码里的标识符。
NODE_TYPES = VALID_NODE_TYPES
TRANSFORM_VERBS = VALID_VERBS
INPUT_KINDS = VALID_INPUT_KINDS
OUTPUT_FORMATS = VALID_OUTPUT_FORMATS
MAP_OPS = VALID_MAP_OPS
FILTER_OPS = VALID_FILTER_OPS
CONDITION_OPS = VALID_CONDITION_OPS
AGGREGATE_OPS = VALID_AGGREGATE_OPS
MERGE_OPS = VALID_MERGE_OPS
TRIGGER_KINDS = VALID_TRIGGER_KINDS
VARIABLE_AGGREGATOR_STRATEGIES = VALID_VARIABLE_AGGREGATOR_STRATEGIES
HTTP_METHODS = VALID_HTTP_METHODS
CODE_LANGUAGES = VALID_CODE_LANGUAGES

# -- 未随导出面下发的固定枚举 ------------------------------------------------
# 这几组白名单只被编译期校验读，导出脚本不提供可改写入口，直接内联平台真值。
NUMERIC_AGGREGATE_OPS = ("sum", "min", "max", "avg")
FLOW_TYPES = ("chatflow", "workflow")
KNOWLEDGE_MODES = ("lexical", "vector", "hybrid")
SUBFLOW_CONTAINER_TYPES = ("iteration", "loop", "parallel", "subgraph")

#: 递归步数硬上限（与平台一致）：用户传入的 recursion_limit 一律被夹到它以内，
#: 绝不接受一个能把脚本拖死的无界值。
RECURSION_LIMIT_CAP = 1000



#: ``code`` 节点子进程引导脚本：在**用户代码执行前**先在子进程内封死网络与
#: shell 通道（A-画布搭建器-01 code / A-Claw安全-01 边界）。
#:
#: 诚实边界（与 runtime/sandbox.py 同一文化）：这是**进程级 best-effort**——
#: 子进程隔离 + 超时杀树 + Python 层拒绝 socket/_socket/subprocess/os.system，
#: **不是操作系统级安全边界**（那需要容器/job object）。所有拒绝都带
#: ``dsl code node sandbox`` 前缀，节点失败信息里可归因。
_CODE_SANDBOX_BOOTSTRAP = r"""
import os as _os
import socket as _socket
import sys as _sys


def _fy_deny(what):
    def _blocked(*_a, **_k):
        raise OSError("dsl code node sandbox: %s is disabled" % what)
    return _blocked


_socket.socket = _fy_deny("socket.socket")
_socket.create_connection = _fy_deny("socket.create_connection")
_socket.getaddrinfo = _fy_deny("socket.getaddrinfo")
try:
    import _socket as _c_socket
    _c_socket.socket = _fy_deny("_socket.socket")
except Exception:
    pass
try:
    import subprocess as _subprocess
    _subprocess.Popen = _fy_deny("subprocess.Popen")
    _subprocess.run = _fy_deny("subprocess.run")
    _subprocess.check_output = _fy_deny("subprocess.check_output")
    _subprocess.check_call = _fy_deny("subprocess.check_call")
except Exception:
    pass
_os.system = _fy_deny("os.system")
_os.popen = _fy_deny("os.popen")
for _name in ("execv", "execve", "execvp", "execvpe", "spawnv", "spawnve",
              "spawnvp", "spawnvpe", "fork", "forkpty"):
    if hasattr(_os, _name):
        setattr(_os, _name, _fy_deny("os.%s" % _name))

_user_code = _sys.stdin.read()
exec(compile(_user_code, "dsl_code_node.py", "exec"), {"__name__": "__main__"})
"""


class DslValidationError(DslError, ValueError):
    """DSL 文档不合法（结构 / 拓扑 / 参数 / 解析导出代码失败）。"""


class DslFieldError(DslValidationError):
    """编译期错误，且能定位到具体字段（供 IR 生成逐字段诊断）。

    :class:`DslValidationError` 只是「一句话错误」；:mod:`dsl_ir` 需要把错误落到
    ``params.op`` 这样的字段路径上。本类在**不改变既有异常层级**（仍是
    ``DslValidationError``，既有 ``except`` 一律照常命中）的前提下补一个可选
    ``field_path``。未携带 ``field_path`` 的错误回落到 ``params``。
    """

    def __init__(self, message: str, *, node_id: str = "",
                 field_path: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id
        self.field_path = field_path


# ---------------------------------------------------------------------------
# 确定性动词执行上下文与注册表
# ---------------------------------------------------------------------------

#: 注入 Agent 解析器的签名：``(agent_name, payload, params) -> Any``。
AgentResolver = Callable[[str, Any, dict[str, Any]], Any]
#: 注入「人工确认裁决读取器」的签名：``(node_id, execution_id) -> str | None``。
#: 返回 ``"approve"`` / ``"reject"`` / ``None``（尚无裁决）。**裁决只能来自受信任
#: 的存储（HITL 表），绝不能来自 DSL 文档本身** —— 否则任何能写画布的人都能给自己
#: 批一个「已批准」。
#:
#: 刻意**不**传 graph / payload / dsl_digest：让「读取器自己算一个顺眼的 digest
#: 再放行」这个后门在签名上就不存在。TOCTOU 校验归应用层编排。
ConfirmDecisionReader = Callable[[str, str | None], "str | None"]

#: 注入「审批信号读取器」的签名：``(node_id, execution_id) -> str | None``。
#: 返回 ``"approve"`` / ``"reject"`` / ``None``（尚无裁决）。与
#: :data:`ConfirmDecisionReader` 同源同理：信号只能来自**受信任的治理层**
#: （services/proposal.py 裁决后落库的状态），DSL 只**读**信号、绝不产生信号，
#: 更不自己批。``approval`` 动词据此在提案被裁决后原样续跑或明确失败。
ApprovalSignal = Callable[[str, str | None], "str | None"]

# ---------------------------------------------------------------------------
# A-画布搭建器-01：新节点类型的注入式解析器（对齐 agent 动词的「确定性外壳 +
# 不受限内核」分工——DSL 只定义契约，真实副作用由调用方注入的服务承担）。
# ---------------------------------------------------------------------------

#: ``llm`` / ``question_classifier`` / ``parameter_extractor`` 的模型解析器：
#: ``(model, prompt, params) -> CallResult | str``。生产侧经
#: :class:`~find_yourself.runtime.gateway.ModelGateway.complete`（provider 网关）。
LlmResolver = Callable[[str, str, dict[str, Any]], Any]
#: ``knowledge_retrieval`` 检索解析器：``(query, params) -> list | dict``。
#: 生产侧经 ``services/knowledge/search.py::KnowledgeSearchService.search``。
KnowledgeResolver = Callable[[str, dict[str, Any]], Any]
#: ``tool`` 工具解析器：``(tool_name, arguments, params) -> Any``。
#: 生产侧经 ``services/tool_registry.py::ToolRegistryService.invoke``。
ToolResolver = Callable[[str, dict[str, Any], dict[str, Any]], Any]
#: ``http_request`` 解析器：``(params, payload) -> dict``（``{"status","body",...}``）。
#: 注入时**替代**内置 urllib 直连（单测/离线环境）；未注入时节点自己用标准库发请求。
HttpResolver = Callable[[dict[str, Any], Any], Any]
#: ``human_input`` 输入提供器：``(node_id, execution_id, params) -> Any | None``。
#: 返回 None 表示「尚无人工输入」→ 节点抛 :class:`DslSuspended`，与
#: services/hitl.py 的 interrupt/decide 挂起-恢复语义对齐。
HumanInputProvider = Callable[[str, "str | None", dict[str, Any]], Any]


class DslSuspended(DslError):
    """工作流在 :verb:`confirm` 处挂起，等待人工裁决（需求12 的接入点）。

    这是**控制流信号**，不是节点失败：:func:`run_dsl` 会原样抛出它，让上层编排
    捕获、去建 HITL interrupt、人裁决后再开新一轮 :func:`run_dsl`（带
    ``confirm_decision`` 读取器）续跑。

    因此它**必须能穿透**执行器内部两处 ``except Exception``（节点级与run 级）——
    见 :func:`run_dsl` 里的 ``except DslSuspended: raise``。若被当成普通异常吞掉，
    挂起会静默退化成「节点失败」，那正是最难排查的假绿。

    携带字段：
    * ``checkpoint`` —— 稳定标识「哪个执行的哪个节点」，**跨轮次可重算**
      （``{execution_id}#{node_id}``），恢复时据此找回裁决。
    * ``dsl_digest`` —— 本轮 DSL 文档的规范化摘要。
    * ``context`` / ``options`` —— 交给 HITL 的问题与可决策项。

    .. note::
       **``dsl_digest`` 只出现在本异常上，不在 ``ConfirmDecisionReader`` 的入参里**
       （读取器签名是 ``(node_id, execution_id) -> str | None``）。这是刻意的：
       「人所见即所批」的校验放在**应用层编排**——它手上同时握有「人要看的
       graph」和「本轮payload」，能自己算摘要再与 ``payload["dsl_digest"]`` 比对。
       DSL 层若把graph 交给读取器，就等于把「伪PASS」的后门重新打开（读取器可
       自己算一个顺眼的 digest 再放行）。等上层编排接入、签名按需扩展即可。
    """

    def __init__(self, *, checkpoint: str, dsl_digest: str, context: dict[str, Any],
                 options: list[dict[str, Any]], node_id: str) -> None:
        super().__init__(f"工作流在节点 {node_id} 处挂起，等待人工裁决")
        self.checkpoint = checkpoint
        self.dsl_digest = dsl_digest
        self.context = context
        self.options = options
        self.node_id = node_id

    def to_dict(self) -> dict[str, Any]:
        return {"checkpoint": self.checkpoint, "dsl_digest": self.dsl_digest,
                "context": self.context, "options": self.options,
                "node_id": self.node_id}


@dataclass(frozen=True)
class VerbContext:
    """一次动词调用的全部输入。"""

    node_id: str
    payload: Any
    params: dict[str, Any]
    #: 所有入边上游的输出（按边声明顺序），供 ``merge`` 这类多路动词使用。
    upstreams: list[Any]
    #: Agent 解析器；未注入时为 None，``agent`` 动词据此诚实失败。
    agent_resolver: AgentResolver | None
    #: 人工裁决读取器 + 挂起所需的稳定标识；``confirm`` 动词专用。
    confirm_decision: ConfirmDecisionReader | None = None
    #: 审批信号读取器；``approval`` 动词专用（ADR-04：信号来自治理层 proposal.py）。
    approval_signal: ApprovalSignal | None = None
    execution_id: str | None = None
    dsl_digest: str = ""


@dataclass(frozen=True)
class VerbSpec:
    """一个受限动词的完整契约：名字 + 分类 + 参数契约 + 执行实现。"""

    name: str
    category: str
    summary: str
    #: params 的 JSON Schema 片段（type/required/properties/enum）。
    params_schema: dict[str, Any]
    execute: Callable[[VerbContext], Any]
    #: 额外的编译期检查（JSON Schema 表达不了的跨字段约束）。
    check: Callable[[str, dict[str, Any]], None] | None = None


def _as_items(payload: Any) -> list[Any]:
    """列表载荷原样返回；标量载荷包成单元素列表（与既有 map/filter 语义一致）。"""
    return payload if isinstance(payload, list) else [payload]


_TEMPLATE_RE = re.compile(r"\{([A-Za-z0-9_.\[\]]+)\}")


def _interpolate(template: str, item: Any) -> str:
    """{field} / {a.b} 插值；缺字段替换为空串（确定性，不抛错）。"""
    def repl(m: re.Match[str]) -> str:
        val = _lookup(item, m.group(1))
        return "" if val is None else str(val)
    return _TEMPLATE_RE.sub(repl, template)


def _lookup(item: Any, path: str) -> Any:
    cur: Any = item
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def _compare(left: Any, op: str, right: Any) -> bool:
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


# -- 数据变换类 -------------------------------------------------------------


def _exec_map(ctx: VerbContext) -> Any:
    params = ctx.params
    out = []
    for it in _as_items(ctx.payload):
        op = params["op"]
        if op == "set":
            if not isinstance(it, dict):
                it = {"value": it}
            it = dict(it)
            it[params["field"]] = _interpolate(str(params.get("value", "")), it)
        elif op == "upper":
            it = it.upper() if isinstance(it, str) else it
        elif op == "lower":
            it = it.lower() if isinstance(it, str) else it
        out.append(it)
    return out


def _exec_filter(ctx: VerbContext) -> Any:
    params = ctx.params
    return [it for it in _as_items(ctx.payload)
            if _compare(_lookup(it, params["field"]), params["op"], params.get("value"))]


def _exec_template(ctx: VerbContext) -> Any:
    tpl = ctx.params["template"]
    if isinstance(ctx.payload, list):
        return [_interpolate(tpl, it) for it in ctx.payload]
    return _interpolate(tpl, ctx.payload)


# -- 流程控制类 -------------------------------------------------------------


def _exec_branch(ctx: VerbContext) -> Any:
    """显式条件分支：把「走哪条路」变成**数据**，从而可被边condition 路由。

    与边condition 的分工：边condition 只在调度期决定「是否入队」；``branch``
    在数据面把判定结果写进载荷（``{"branch": 标签, "value": 原始载荷}``），
    于是同一判定结果可以被下游多个节点、乃至模板一起引用。
    """
    params = ctx.params
    matched = _compare(_lookup(ctx.payload, params["field"]), params["op"],
                       params.get("value"))
    return {"branch": params["then_label"] if matched else params["else_label"],
            "value": ctx.payload}


def _exec_aggregate(ctx: VerbContext) -> Any:
    """循环聚合：把列表载荷按field 归约成单值（或join 成的字符串/去重列表）。"""
    params = ctx.params
    op = params["op"]
    items = _as_items(ctx.payload)
    if op == "count":
        return len(items)
    field = params.get("field")
    values = [_lookup(it, field) for it in items] if field else list(items)
    if op == "first":
        return values[0] if values else None
    if op == "last":
        return values[-1] if values else None
    if op == "unique":
        seen: list[str] = []
        out: list[Any] = []
        for v in values:
            key = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.append(key)
                out.append(v)
        return out
    if op == "join":
        sep = params.get("sep", ",")
        return sep.join("" if v is None else str(v) for v in values)
    nums: list[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise DslValidationError(
                f"节点 {ctx.node_id} aggregate.op={op} 需要数值，收到 {v!r}")
        nums.append(v)
    if not nums:
        raise DslValidationError(f"节点 {ctx.node_id} aggregate.op={op} 需要至少一个数值")
    if op == "sum":
        return sum(nums)
    if op == "min":
        return min(nums)
    if op == "max":
        return max(nums)
    return sum(nums) / len(nums)


def _exec_merge(ctx: VerbContext) -> Any:
    """并行汇聚：把多路入边的输出收敛成一路（Dispatcher 语义扩展的落点）。"""
    mode = ctx.params["mode"]
    streams = list(ctx.upstreams) if ctx.upstreams else _as_items(ctx.payload)
    flat: list[Any] = []
    for s in streams:
        if isinstance(s, list):
            flat.extend(s)
        else:
            flat.append(s)
    if mode == "concat":
        return flat
    if mode == "first":
        return flat[0] if flat else None
    return flat[-1] if flat else None


# -- Agent 类 ---------------------------------------------------------------


def _exec_agent(ctx: VerbContext) -> Any:
    """调用已注册 Agent/工具。

    确定性外壳 + 不受限内核：解析器由调用方**显式注入**
    （``run_dsl(doc, agent_resolver=...)``）。未注入时诚实失败——绝不假装调用
    成功，也绝不静默跳过。
    """
    if ctx.agent_resolver is None:
        raise DslValidationError(
            f"节点 {ctx.node_id} 的 agent 动词需要注入 Agent 解析器"
            "（run_dsl(doc, agent_resolver=...)）；未注入时本平台不执行 Agent 调用"
        )
    return ctx.agent_resolver(ctx.params["agent"], ctx.payload, dict(ctx.params))


# -- 人机协作类 -------------------------------------------------------------


def _exec_confirm(ctx: VerbContext) -> Any:
    """挂起等人确认（需求 12 的接入点）。

    两个分支：

    * **尚无裁决** →抛 :class:`DslSuspended`（控制流信号，穿透执行器向上层传播）。
      上层捕获后建 HITL interrupt，人裁决后**重开一轮** ``run_dsl``。
    * **已有裁决**（``confirm_decision`` 读取器返回了approve/reject）→ 直接放行
      或明确失败，DSL 引擎保持无状态、**不需要可序列化游标**。

    安全边界：裁决**只能**由注入的读取器从受信任存储（dev-hitl 的 HITL 表）取得，
    绝不接受 DSL 文档里自带的「已批准」标记——否则任何能编辑画布的人都能给自己
    批通行证。读取器**只拿到** ``(node_id, execution_id)``，拿不到 graph也拿不到
    ``dsl_digest``：TOCTOU 校验归应用层编排（它手上有graph，可自算摘要比对）。
    """
    if ctx.confirm_decision is not None:
        decision = ctx.confirm_decision(ctx.node_id, ctx.execution_id)
        if decision == "approve":
            return ctx.payload
        if decision == "reject":
            raise DslValidationError(
                f"节点 {ctx.node_id} 的 confirm 被人工驳回（decision=reject）")
        # 读取器明确说「尚无裁决」→ 仍按挂起处理，不猜。

    raise DslSuspended(
        checkpoint=f"{ctx.execution_id or '-'}#{ctx.node_id}",
        dsl_digest=ctx.dsl_digest,
        context={"prompt": ctx.params["prompt"], "role": ctx.params.get("role", ""),
                 "node_id": ctx.node_id},
        # 二元语义：这步要不要继续。多选投票属于团队审批流（需求 6），不该泛化 confirm。
        options=[{"value": "approve", "label": "批准"},
                 {"value": "reject", "label": "驳回"}],
        node_id=ctx.node_id,
    )


# -- 治理类 -----------------------------------------------------------------


def _exec_approval(ctx: VerbContext) -> Any:
    """审批挂起点（ADR-04）：**DSL 是编排层，不是治理层**。

    本函数**不实现任何审批逻辑**：不重算 canonical digest、不做双向 digest 比对、
    不执行条件 ``UPDATE ... WHERE status='pending'`` 防并发、不写任何状态、不消费
    ``idempotency_key``、不建 outbox —— 这些全部是
    :mod:`find_yourself.services.proposal`（治理**唯一**入口）的职责，DSL 绝不重写。

    它只做一件事：把「要一次治理决策」的意图（``op`` / ``target_id`` / ``reason`` /
    ``rollback`` / ``payload``）随 :class:`DslSuspended` 交给上层编排；由编排层调
    ``ProposalService.create(...)`` 建提案，人裁决（proposal 落库）后带
    ``approval_signal`` 重开一轮 :func:`run_dsl` 续跑：

    * **尚无信号** → 抛 :class:`DslSuspended`（控制流信号，非失败）。
    * **approve** → 载荷原样放行（op 的实际执行已由 proposal 在裁决时完成）。
    * **reject** → 明确失败，绝不静默跳过。

    挂起点与 ``confirm`` 同一条（``DslSuspended``，对齐
    :attr:`~find_yourself.workflows.models.Stage.awaiting_approval`）：DSL 层
    **不新增任何 DB 状态推进**。
    """
    if ctx.approval_signal is not None:
        signal = ctx.approval_signal(ctx.node_id, ctx.execution_id)
        if signal == "approve":
            return ctx.payload
        if signal == "reject":
            raise DslValidationError(
                f"节点 {ctx.node_id} 的审批被驳回"
                f"（signal=reject，op={ctx.params['op']}）")
        # None：尚无裁决 → 仍挂起，绝不猜成 approve。

    raise DslSuspended(
        checkpoint=f"{ctx.execution_id or '-'}#{ctx.node_id}",
        dsl_digest=ctx.dsl_digest,
        context={
            # 「创建提案」的意图，字段名对齐 ProposalService.create 的入参；
            # 治理层的实际 create 由上层编排完成（DSL 不握有 session/actor）。
            "approval": {
                "operation": ctx.params["op"],
                "target_id": ctx.params.get("target_id"),
                "reason": ctx.params.get("reason", ""),
                "rollback": ctx.params.get("rollback", ""),
                "payload": ctx.payload,
            },
            "node_id": ctx.node_id,
        },
        options=[{"value": "approve", "label": "批准"},
                 {"value": "reject", "label": "驳回"}],
        node_id=ctx.node_id,
    )


# -- 输出类 -----------------------------------------------------------------


def _exec_artifact(ctx: VerbContext) -> Any:
    """产出带名字的产物信封：把载荷标记成可被下游/导出引用的产物。"""
    return {"artifact": ctx.params["name"],
            "kind": ctx.params.get("kind", "generic"),
            "content": ctx.payload}



# ---------------------------------------------------------------------------
# A-画布搭建器-01：13 类新增节点的执行语义。
#
# 分工与 agent 动词一致——**确定性外壳 + 注入式内核**：
# * 纯确定的（template / variable_aggregator / iteration / loop / trigger）
#   在本文件内完整实现；
# * 有真实副作用的（llm / knowledge_retrieval / tool / http_request /
#   human_input）只定义契约，解析器由 ``run_dsl(...)`` 显式注入，未注入时
#   **诚实失败**（对齐 agent 动词），绝不假装调用成功；
# * ``code`` 自带受控执行（子进程 + 超时 + Python 层禁网络/禁 shell），
#   不需要注入——它本身就是唯一被允许的内嵌执行点。
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s,;'\"]+")


def _redact_url(message: str, limit: int = 240) -> str:
    """传输层错误回显前抹掉 URL（对齐 runtime/providers/base.sanitize_message）。"""
    text = _URL_RE.sub("<endpoint>", str(message or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _normalize_llm_result(raw: Any) -> dict[str, Any]:
    """LLM 解析器返回值 → ``{"text", "usage"}``（CallResult / dict / str / 其它）。"""
    if hasattr(raw, "text") and not isinstance(raw, dict):
        usage = getattr(raw, "usage", None)
        return {"text": str(raw.text), "usage": dict(usage) if isinstance(usage, dict) else {}}
    if isinstance(raw, dict) and "text" in raw:
        usage = raw.get("usage")
        return {"text": str(raw["text"]),
                "usage": dict(usage) if isinstance(usage, dict) else {}}
    if isinstance(raw, str):
        return {"text": raw, "usage": {}}
    return {"text": str(raw), "usage": {}}


def _payload_text(payload: Any) -> str:
    """把上游载荷变成可塞进 prompt 的文本（str 原样；None 空串；其余 JSON）。"""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _exec_node_llm(node_id: str, params: dict[str, Any], payload: Any,
                   llm_resolver: LlmResolver | None) -> Any:
    if llm_resolver is None:
        raise DslValidationError(
            f"节点 {node_id} 的 llm 节点需要注入模型解析器"
            "（run_dsl(doc, llm_resolver=...)）；未注入时本平台不执行模型调用"
        )
    prompt = params["prompt"]
    if payload is not None:
        prompt = _interpolate(prompt, payload)
    return _normalize_llm_result(llm_resolver(params["model"], prompt, dict(params)))


def _exec_node_knowledge(node_id: str, params: dict[str, Any], payload: Any,
                         knowledge_resolver: KnowledgeResolver | None) -> Any:
    if knowledge_resolver is None:
        raise DslValidationError(
            f"节点 {node_id} 的 knowledge_retrieval 节点需要注入检索解析器"
            "（run_dsl(doc, knowledge_resolver=...)）；未注入时本平台不执行检索"
        )
    query = params["query"]
    if payload is not None:
        query = _interpolate(query, payload)
    return knowledge_resolver(query, dict(params))


def _exec_node_question_classifier(node_id: str, params: dict[str, Any], payload: Any,
                                   llm_resolver: LlmResolver | None) -> Any:
    if llm_resolver is None:
        raise DslValidationError(
            f"节点 {node_id} 的 question_classifier 节点需要注入模型解析器"
            "（run_dsl(doc, llm_resolver=...)）"
        )
    classes = params["classes"]
    query = params.get("query")
    if query is None or query == "":
        query = _payload_text(payload)
    prompt = (
        "你是问题分类器。只输出以下类别之一，不要输出其它内容："
        + " / ".join(str(c) for c in classes)
        + "\n\n问题：" + _payload_text(query if query != "" else payload)
    )
    raw = _normalize_llm_result(
        llm_resolver(params["model"], prompt, dict(params)))
    text = raw["text"].strip()
    matched = next((c for c in classes if str(c) == text), None)
    if matched is None:
        # 宽限一层：模型输出夹带说明时，按出现顺序取第一个被提及的类别；
        # 仍无命中即失败（绝不静默造类）。
        for c in classes:
            if str(c) and str(c) in text:
                matched = c
                break
    if matched is None:
        raise DslValidationError(
            f"节点 {node_id} question_classifier 输出 {text!r} 不在 classes 内")
    return {"class": matched, "query": query, "raw": text}


def _exec_node_parameter_extractor(node_id: str, params: dict[str, Any], payload: Any,
                                   llm_resolver: LlmResolver | None) -> Any:
    if llm_resolver is None:
        raise DslValidationError(
            f"节点 {node_id} 的 parameter_extractor 节点需要注入模型解析器"
            "（run_dsl(doc, llm_resolver=...)）"
        )
    fields = params["fields"]
    names = [str(f["name"]) for f in fields]
    text_in = params.get("text")
    if not text_in:
        text_in = _payload_text(payload)
    spec = "; ".join(
        f"{f['name']}({f.get('type', 'string')}"
        + (", 必填" if f.get("required") else "") + ")"
        for f in fields)
    prompt = (
        "从文本中抽取以下字段并以 JSON 对象输出（只输出 JSON）："
        + spec + "\n\n文本：" + _payload_text(text_in)
    )
    raw = _normalize_llm_result(
        llm_resolver(params["model"], prompt, dict(params)))
    try:
        extracted = json.loads(raw["text"].strip())
    except json.JSONDecodeError:
        start, end = raw["text"].find("{"), raw["text"].rfind("}")
        if start < 0 or end <= start:
            raise DslValidationError(
                f"节点 {node_id} parameter_extractor 输出不是 JSON: "
                + _redact_url(raw["text"][:120]))
        try:
            extracted = json.loads(raw["text"][start:end + 1])
        except json.JSONDecodeError as exc:
            raise DslValidationError(
                f"节点 {node_id} parameter_extractor 输出不是 JSON: {exc}") from exc
    if not isinstance(extracted, dict):
        raise DslValidationError(
            f"节点 {node_id} parameter_extractor 输出必须是 JSON 对象")
    values: dict[str, Any] = {}
    for f in fields:
        name = str(f["name"])
        if f.get("required") and extracted.get(name) is None:
            raise DslValidationError(
                f"节点 {node_id} parameter_extractor 缺少必填字段 {name}")
        values[name] = extracted.get(name)
    ignored = [k for k in extracted if k not in names]
    return {"values": values, "ignored": ignored}


def _run_subflow_once(node_id: str, subflow: dict[str, Any], value: Any,
                      resolvers: dict[str, Any], label: str) -> Any:
    """把 ``value`` 覆盖进子流程唯一 input 节点后跑一轮 ``run_dsl``。"""
    doc = json.loads(json.dumps(subflow, ensure_ascii=False))  # 深拷贝，别污染参数
    input_ids = [n["id"] for n in doc["nodes"] if n.get("type") == "input"]
    if not input_ids:
        raise DslValidationError(
            f"节点 {node_id} 的 subflow 必须包含一个 input 节点作为入口")
    target = next(n for n in doc["nodes"] if n["id"] == input_ids[0])
    target["params"] = {**(target.get("params") or {}), "value": value}
    # ``subflows`` 是本运行可见的命名子图库（随调用链显式传递，不用模块级全局：
    # ``parallel`` 在线程池里并发跑子流程，全局变量必然互相串味）。
    library = resolvers.get("subflows") or {}
    result = run_dsl(doc, run_id=f"{node_id}:{label}", subgraphs=library,
                     **{k: v for k, v in resolvers.items()
                        if v is not None and k != "subflows"})
    if result.status == "failed":
        raise DslValidationError(f"节点 {node_id} 子流程运行失败: {result.error}")
    return result.output


def _exec_node_iteration(node_id: str, params: dict[str, Any], payload: Any,
                         resolvers: dict[str, Any]) -> Any:
    if payload is None:
        raise DslValidationError(f"节点 {node_id} iteration 需要上游输入（列表）")
    items = payload if isinstance(payload, list) else [payload]
    max_items = int(params.get("max_items", ITERATION_DEFAULT_MAX_ITEMS))
    if len(items) > max_items:
        raise DslValidationError(
            f"节点 {node_id} iteration 待处理 {len(items)} 项超过 max_items={max_items}")
    item_field = params.get("item_field")
    outputs: list[Any] = []
    for idx, item in enumerate(items):
        value = _lookup(item, item_field) if item_field else item
        outputs.append(_run_subflow_once(
            node_id, params["subflow"], value, resolvers, f"item{idx}"))
    return outputs


def _exec_node_loop(node_id: str, params: dict[str, Any], payload: Any,
                    resolvers: dict[str, Any]) -> Any:
    max_iter = int(params.get("max_iterations", LOOP_DEFAULT_MAX_ITERATIONS))
    until_field = params.get("until_field")
    until_op = params.get("until_op")
    until_value = params.get("until_value")
    current = payload
    last: Any = None
    iterations = 0
    reason = "max_iterations"
    while iterations < max_iter:
        last = _run_subflow_once(
            node_id, params["subflow"], current, resolvers, f"iter{iterations}")
        iterations += 1
        if until_field and until_op and \
                _compare(_lookup(last, until_field), until_op, until_value):
            reason = "until_met"
            break
        current = last
    return {"iterations": iterations, "output": last, "reason": reason}


#: 命名子图库以**调用链显式传参**的方式流转（``run_dsl(subgraphs=...)`` →
#: ``execute_node(subflows=...)`` → ``resolvers["subflows"]``），
#: 刻意不使用模块级可变全局：``parallel`` 在线程池里并发跑子流程，全局必然串味。


def _exec_node_parallel(node_id: str, params: dict[str, Any], payload: Any,
                        resolvers: dict[str, Any]) -> Any:
    """并行扇出/汇聚（对标 LangGraph 的并行边 + ``Send`` 式动态 map 扇出）。

    与 ``iteration`` 的差别是**真并发**：``iteration`` 是串行逐项 map（有序、
    单线程、便于逐步调试），``parallel`` 把多个命名分支丢进线程池同时跑，
    再按分支声明顺序 fan-in 汇总（顺序稳定，与完成先后无关）。

    分支失败**如实上抛**：任一分支失败则本节点失败，绝不把失败分支当成
    「已完成」混进汇聚结果（否则画布作者会拿到一份看起来齐全的假回执）。
    """
    branches = params["branches"]
    workers = int(params.get("max_workers", len(branches)))
    if workers < 1:
        raise DslValidationError(f"节点 {node_id} parallel.max_workers 至少为 1")
    # 扇出：每个分支一份独立深拷贝的子流程文档，避免并发下共享可变状态。
    library = resolvers.get("subflows") or {}
    docs: list[tuple[str, dict[str, Any]]] = []
    for idx, branch in enumerate(branches):
        docs.append((str(branch["name"]),
                     _branch_subflow(node_id, branch, idx, library)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_subflow_once, node_id, doc, payload, resolvers,
                        f"branch{idx}:{name}")
            for idx, (name, doc) in enumerate(docs)
        ]
        # 按声明顺序取结果（future 完成的先后不影响输出顺序）。
        return [fut.result() for fut in futures]


def _branch_subflow(node_id: str, branch: Any, idx: int,
                    library: dict[str, Any]) -> dict[str, Any]:
    """取分支声明的子流程文档（``inline`` 或按名引用 ``subgraphs`` 库）。"""
    if isinstance(branch.get("inline"), dict):
        return branch["inline"]
    name = branch.get("ref")
    sub = library.get(name) if isinstance(name, str) else None
    if sub is None:
        raise DslValidationError(
            f"节点 {node_id} parallel.branches[{idx}] 引用了未定义的子流程 {name!r}；"
            f"可用子流程: {sorted(library)}")
    return sub


def _exec_node_subgraph(node_id: str, params: dict[str, Any], payload: Any,
                        resolvers: dict[str, Any]) -> Any:
    """命名子图节点（对标 LangGraph「把子图作为节点」）。

    与 ``parallel`` 的差别是**单次调用一张被命名的子图**：子图在文档级
    ``subgraphs`` 里声明一次，可被多个 ``subgraph`` 节点按名复用；输出额外带上
    ``subgraph`` 名字，让运行日志能看出结果来自哪张子图。
    """
    name = str(params["name"])
    subflow = _branch_subflow(node_id, {"ref": name}, 0,
                              resolvers.get("subflows") or {})
    out = _run_subflow_once(node_id, subflow, payload, resolvers, f"subgraph:{name}")
    return {"subgraph": name, "output": out}


def _exec_node_variable_aggregator(node_id: str, params: dict[str, Any],
                                   payload: Any, upstreams: list[Any]) -> Any:
    strategy = params.get("strategy", "first_non_null")
    candidates = [u for u in upstreams if u is not None]
    if not candidates and payload is not None:
        candidates = [payload]
    if not candidates:
        raise DslValidationError(
            f"节点 {node_id} variable_aggregator 所有上游变量均为空")
    return candidates[0] if strategy == "first_non_null" else candidates[-1]


def _exec_node_template(node_id: str, params: dict[str, Any], payload: Any) -> Any:
    """独立模板节点：与 transform.template 动词同一插值实现。"""
    tpl = params["template"]
    if isinstance(payload, list):
        return [_interpolate(tpl, it) for it in payload]
    return _interpolate(tpl, payload if payload is not None else {})


_HTTP_BODY_METHODS = ("POST", "PUT", "PATCH")


def _exec_node_http(node_id: str, params: dict[str, Any], payload: Any,
                    http_resolver: HttpResolver | None) -> Any:
    url = str(params["url"]).strip()
    if not url.lower().startswith(("http://", "https://")):
        raise DslValidationError(
            f"节点 {node_id} http_request.url 只允许 http/https，实际是 {url[:60]!r}")
    # ---- fail-closed 出网白名单校验（对 resolver 路径同样生效） ----
    # 设计意图：allow_domains 是**执行期强制的准入闸门**，与走真实网络还是
    # 注入解析器无关 —— 后者只是替代传输，不应绕过准入判断。
    allow = params.get("allow_domains")
    # fail-closed：未配置出网白名单时**拒绝**，而不是放行任意 host。
    # 历史行为是 `if allow:` 包裹校验 → 未配置即静默放行全部目标，
    # 对一个可由画布编排触达的节点而言等同于 SSRF 全开。
    # 若确需出网，请在权限面板显式配置 allow_domains。
    if not allow:
        raise DslValidationError(
            f"节点 {node_id} http_request 未配置 allow_domains 出网白名单，"
            "已拒绝出网（安全策略 fail-closed）。请在权限面板配置允许的域名后重试。")
    allowed = [str(d) for d in allow if isinstance(d, str) and d]
    if not allowed:
        raise DslValidationError(
            f"节点 {node_id} http_request 的 allow_domains 为空列表，"
            "已拒绝出网（安全策略 fail-closed）。")
    host = urllib.parse.urlparse(url).hostname or ""
    if not any(host == d or host.endswith("." + d) for d in allowed):
        raise DslValidationError(
            f"节点 {node_id} http_request 出网 host {host!r} 不在 "
            "allow_domains 白名单内（权限面板配置，执行期强制）")
    if http_resolver is not None:
        # 注入解析器时**替代**内置直连（单测 / 离线环境挂钩）。
        return http_resolver(dict(params), payload)
    method = params.get("method", "GET")
    timeout = min(float(params.get("timeout_seconds", 10.0)), 30.0)
    max_bytes = max(int(params.get("max_bytes", 65536)), 1)
    headers = {str(k): str(v) for k, v in (params.get("headers") or {}).items()}
    data = None
    if method in _HTTP_BODY_METHODS and params.get("body") is not None:
        body = params["body"]
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            data = str(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        # 出网是 http_request 节点的**既定职责**（对标 Dify HTTP 节点）；
        # SSRF 面由 allow_domains 白名单约束——**未配置即拒绝**（fail-closed），
        # 校验已在函数开头完成，此处仅执行已放行的请求。
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = int(resp.status)
            raw = resp.read(max_bytes + 1)
    except Exception as exc:  # 传输失败即节点失败；错误信息抹 URL（不回显连接串）
        raise DslValidationError(
            f"节点 {node_id} http_request 失败: {_redact_url(str(exc))}") from exc
    truncated = len(raw) > max_bytes
    return {"status": status, "url": url,
            "body": raw[:max_bytes].decode("utf-8", errors="replace"),
            "truncated": truncated}


def _code_sandbox_env() -> dict[str, str]:
    """code 子进程的最小化环境：只保留运行必需项，剥掉一切凭据形变量。"""
    keep: dict[str, str] = {}
    for key in ("SystemRoot", "SYSTEMROOT", "windir", "TEMP", "TMP", "PATH",
                "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONIOENCODING"):
        value = os.environ.get(key)
        if value:
            keep[key] = value
    keep.setdefault("PYTHONIOENCODING", "utf-8")
    return keep


def _exec_node_code(node_id: str, params: dict[str, Any], payload: Any) -> Any:
    """受控执行（A-画布搭建器-01 code / A-Claw安全-01 边界）。

    * **子进程**：``sys.executable -I -c <bootstrap>``（isolated mode，无 shell），
      用户代码经 stdin 注入，stdout/stderr 捕获；
    * **禁网络/禁 shell**：bootstrap 在执行用户代码前封死
      ``socket/_socket/subprocess/os.system/os.popen/os.exec*/spawn*``；
    * **超时**：参数值被裁到 :data:`CODE_TIMEOUT_CAP_SECONDS` 上界，超时杀进程；
    * **诚实边界**：进程级 best-effort，不是 OS 级安全边界（与
      runtime/sandbox.py 同一诚实文化）。
    """
    language = params.get("language", "python")
    if language != "python":
        raise DslValidationError(
            f"节点 {node_id} code.language 只支持 {CODE_LANGUAGES}")
    code = params["code"]
    timeout = min(float(params.get("timeout_seconds", 5.0)),
                  CODE_TIMEOUT_CAP_SECONDS)
    max_bytes = max(int(params.get("max_output_bytes", 65536)), 1)
    # 上游载荷经 `_fy_payload` 注入用户代码（repr 是 JSON 安全数据的合法字面量）。
    script = code
    if payload is not None:
        script = f"_fy_payload = {payload!r}\n" + script
    with tempfile.TemporaryDirectory(prefix="fy-dsl-code-") as tmpdir:
        try:
            proc = subprocess.Popen(  # noqa: S603 - 固定 argv，无 shell，代码走 stdin
                [sys.executable, "-I", "-c", _CODE_SANDBOX_BOOTSTRAP],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=_code_sandbox_env(), cwd=tmpdir,
                text=True, encoding="utf-8", errors="replace")
            try:
                stdout, stderr = proc.communicate(input=script, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise DslValidationError(
                    f"节点 {node_id} code 执行超时（>{timeout:g}s），子进程已终止")
        except OSError as exc:
            raise DslValidationError(
                f"节点 {node_id} code 子进程启动失败: {_redact_url(str(exc))}") from exc
    if proc.returncode != 0:
        detail = (stderr or "").strip()[:400] or (stdout or "").strip()[:400]
        raise DslValidationError(
            f"节点 {node_id} code 非零退出（exit={proc.returncode}）: {detail}")
    truncated = len(stdout) > max_bytes
    return {"stdout": stdout[:max_bytes], "exit_code": proc.returncode,
            "truncated": truncated}


def _exec_node_tool(node_id: str, params: dict[str, Any],
                    tool_resolver: ToolResolver | None) -> Any:
    if tool_resolver is None:
        raise DslValidationError(
            f"节点 {node_id} 的 tool 节点需要注入工具解析器"
            "（run_dsl(doc, tool_resolver=...)）；生产侧经 "
            "services/tool_registry.py::ToolRegistryService.invoke")
    return tool_resolver(params["tool"], dict(params.get("arguments") or {}),
                         dict(params))


def _exec_node_human_input(node_id: str, params: dict[str, Any], payload: Any,
                           provider: HumanInputProvider | None,
                           execution_id: str | None, digest: str) -> Any:
    if provider is not None:
        value = provider(node_id, execution_id, dict(params))
        if value is not None:
            return {"input": value, "prompt": params["prompt"]}
    raw_options = params.get("options") or ["provide"]
    options = [{"value": str(o), "label": str(o)} for o in raw_options] or \
        [{"value": "provide", "label": "提供输入"}]
    raise DslSuspended(
        checkpoint=f"{execution_id or '-'}#{node_id}",
        dsl_digest=digest,
        context={"prompt": params["prompt"], "role": params.get("role", ""),
                 "options": raw_options, "node_id": node_id},
        options=options,
        node_id=node_id,
    )


def _exec_node_trigger(node_id: str, params: dict[str, Any]) -> Any:
    """触发器/入口节点：产出本轮运行的初始载荷（装配语义见 validate_assembly）。"""
    kind = params["kind"]
    config = params.get("config") or {}
    if kind == "conversation":
        return {"text": config.get("value", ""),
                "conversation_id": config.get("conversation_id", ""),
                "trigger": "conversation"}
    return {"value": config.get("value"), "trigger": kind}


# -- 编译期跨字段检查 --------------------------------------------------------


def _check_map(node_id: str, params: dict[str, Any]) -> None:
    if params["op"] == "set" and not isinstance(params.get("field"), str):
        raise DslValidationError(f"节点 {node_id} map.set 需要 field")


def _check_aggregate(node_id: str, params: dict[str, Any]) -> None:
    if params["op"] in NUMERIC_AGGREGATE_OPS and not isinstance(params.get("field"), str):
        raise DslValidationError(
            f"节点 {node_id} aggregate.op={params['op']} 需要 field")




# -- 节点级跨字段检查（A-画布搭建器-01：非 transform 节点的编译期约束） --------


def _check_subflow_doc(node_id: str, subflow: Any, *, depth: int = 0,
                       library: dict[str, Any] | None = None) -> None:
    """子流程必须是一张**合法 DSL 文档**，且嵌套深度有上界（防编译期深爆炸）。

    ``library`` 是**外层可见的子图名表**：嵌套子流程按名引用父图子图是运行期支持的
    （:func:`run_dsl` 会把父图库合并进子流程），编译期必须同语义下传，否则会出现
    「编译报错但实际能跑」的双标。**下层声明遮蔽上层同名**（与 run_dsl 的合并顺序一致）。
    """
    if not isinstance(subflow, dict):
        raise DslValidationError(
            f"节点 {node_id} 的 subflow 必须是一张 DSL 文档对象")
    try:
        validate_dsl(subflow, _visible_subgraphs=library)
    except DslValidationError as exc:
        raise DslValidationError(f"节点 {node_id} 的 subflow 不合法: {exc}") from exc
    if depth >= SUBFLOW_MAX_DEPTH:
        raise DslValidationError(
            f"节点 {node_id} 的子流程嵌套超过 {SUBFLOW_MAX_DEPTH} 层上界")
    for n in subflow.get("nodes", []):
        if isinstance(n, dict) and n.get("type") in SUBFLOW_CONTAINER_TYPES:
            nested = (n.get("params") or {}).get("subflow")
            if isinstance(nested, dict):
                _check_subflow_doc(f"{node_id}>{n['id']}", nested,
                                   depth=depth + 1, library=library)


def _check_iteration(node_id: str, params: dict[str, Any],
                     library: dict[str, Any] | None = None) -> None:
    _check_subflow_doc(node_id, params.get("subflow"), library=library)
    subflow = params.get("subflow") or {}
    if not any(isinstance(n, dict) and n.get("type") == "input"
               for n in subflow.get("nodes", [])):
        raise DslFieldError(
            f"节点 {node_id} 的 iteration.subflow 必须包含一个 input 节点作为入口",
            node_id=node_id, field_path="params.subflow")
    max_items = params.get("max_items")
    if max_items is not None and (isinstance(max_items, bool) or
                                  not isinstance(max_items, int) or
                                  not 1 <= max_items <= ITERATION_MAX_ITEMS_CAP):
        raise DslFieldError(
            f"节点 {node_id} 的 iteration.max_items 必须在 "
            f"1..{ITERATION_MAX_ITEMS_CAP} 内，实际是 {max_items!r}",
            node_id=node_id, field_path="params.max_items")


def _check_loop(node_id: str, params: dict[str, Any],
                library: dict[str, Any] | None = None) -> None:
    _check_subflow_doc(node_id, params.get("subflow"), library=library)
    max_iter = params.get("max_iterations")
    if max_iter is not None and (isinstance(max_iter, bool) or
                                 not isinstance(max_iter, int) or
                                 not 1 <= max_iter <= LOOP_MAX_ITERATIONS_CAP):
        raise DslFieldError(
            f"节点 {node_id} 的 loop.max_iterations 必须在 "
            f"1..{LOOP_MAX_ITERATIONS_CAP} 内（循环必须有终止上界），实际是 {max_iter!r}",
            node_id=node_id, field_path="params.max_iterations")
    until_op = params.get("until_op")
    if until_op is not None and not params.get("until_field"):
        raise DslFieldError(
            f"节点 {node_id} 的 loop.until_op 需要同时给出 until_field",
            node_id=node_id, field_path="params.until_field")


def _check_question_classifier(node_id: str, params: dict[str, Any],
                              library: dict[str, Any] | None = None) -> None:
    classes = params.get("classes")
    if not isinstance(classes, list) or len(classes) < 2 or \
            not all(isinstance(c, str) and c for c in classes):
        raise DslFieldError(
            f"节点 {node_id} 的 question_classifier.classes 必须是至少 2 个"
            "非空字符串（分类器至少要有两类）",
            node_id=node_id, field_path="params.classes")


def _check_parameter_extractor(node_id: str, params: dict[str, Any],
                               library: dict[str, Any] | None = None) -> None:
    fields = params.get("fields")
    if not isinstance(fields, list) or not fields or not all(
            isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"]
            for f in fields):
        raise DslFieldError(
            f"节点 {node_id} 的 parameter_extractor.fields 必须是非空数组，"
            "每项是带非空 name 的对象",
            node_id=node_id, field_path="params.fields")


# -- N5：并行扇出 与 命名子图的编译期跨字段检查 ------------------------------


def _subflow_entry_ok(subflow: Any) -> bool:
    """子流程是否含一个 ``input`` 节点作为入口（子流程由 payload 喂输入）。"""
    return isinstance(subflow, dict) and any(
        isinstance(n, dict) and n.get("type") == "input"
        for n in subflow.get("nodes", []))


def _check_parallel(node_id: str, params: dict[str, Any],
                    library: dict[str, Any] | None = None) -> None:
    """``parallel`` 的分支声明逐项校验（**分支数硬闸门**在此生效）。

    每项必须是 ``{name, inline | ref}``：``inline`` 是一张内联子图文档，
    ``ref`` 引用文档级 ``subgraphs`` 里的名字。二者**有且只有一个**——
    同时给或都不给都是编译错误，绝不靠运行期猜。
    """
    branches = params.get("branches")
    if not isinstance(branches, list) or not branches:
        raise DslFieldError(
            f"节点 {node_id} 的 parallel.branches 必须是非空数组",
            node_id=node_id, field_path="params.branches")
    if len(branches) > PARALLEL_MAX_BRANCHES:
        raise DslFieldError(
            f"节点 {node_id} 的 parallel.branches 有 {len(branches)} 个分支，"
            f"超过规模闸门 PARALLEL_MAX_BRANCHES={PARALLEL_MAX_BRANCHES}",
            node_id=node_id, field_path="params.branches")
    seen: set[str] = set()
    for idx, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise DslFieldError(
                f"节点 {node_id} 的 parallel.branches[{idx}] 必须是对象",
                node_id=node_id, field_path=f"params.branches.{idx}")
        name = branch.get("name")
        if not isinstance(name, str) or not name:
            raise DslFieldError(
                f"节点 {node_id} 的 parallel.branches[{idx}].name 必须是非空字符串"
                "（分支名决定 fan-in 结果的可读顺序）",
                node_id=node_id, field_path=f"params.branches.{idx}.name")
        if name in seen:
            raise DslFieldError(
                f"节点 {node_id} 的 parallel 分支名 {name!r} 重复",
                node_id=node_id, field_path=f"params.branches.{idx}.name")
        seen.add(name)
        inline, ref = branch.get("inline"), branch.get("ref")
        if isinstance(inline, dict) and ref is not None:
            raise DslFieldError(
                f"节点 {node_id} 的 parallel.branches[{idx}] 同时给了 inline 与 ref，"
                "二者只能选一个",
                node_id=node_id, field_path=f"params.branches.{idx}")
        if isinstance(inline, dict):
            _check_subflow_doc(f"{node_id}.branches[{idx}]", inline,
                               library=library)
            if not _subflow_entry_ok(inline):
                raise DslFieldError(
                    f"节点 {node_id} 的 parallel.branches[{idx}].inline 必须包含"
                    "一个 input 节点作为入口",
                    node_id=node_id, field_path=f"params.branches.{idx}.inline")
        elif isinstance(ref, str) and ref:
            pass  # 按名引用：文档级 ``subgraphs`` 的存在性由 validate_dsl 检查
        else:
            raise DslFieldError(
                f"节点 {node_id} 的 parallel.branches[{idx}] 必须给 inline"
                "（内联子图文档）或 ref（子图名）之一",
                node_id=node_id, field_path=f"params.branches.{idx}")
    workers = params.get("max_workers")
    if workers is not None and (isinstance(workers, bool) or
                                not isinstance(workers, int) or
                                not 1 <= workers <= PARALLEL_MAX_BRANCHES):
        raise DslFieldError(
            f"节点 {node_id} 的 parallel.max_workers 必须在 "
            f"1..{PARALLEL_MAX_BRANCHES} 内，实际是 {workers!r}",
            node_id=node_id, field_path="params.max_workers")


def _check_subgraph(node_id: str, params: dict[str, Any],
                    library: dict[str, Any] | None = None) -> None:
    """``subgraph`` 必须按名引用文档级子图；名字本身由 JSON Schema 保证非空。"""
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise DslFieldError(
            f"节点 {node_id} 的 subgraph.name 必须是非空子图名",
            node_id=node_id, field_path="params.name")


#: 非 transform 节点类型的编译期跨字段检查（由 validate_dsl 在参数校验后调用）。
#: 第三个参数 ``library`` 是当前文档可见的子图名表（外层继承 + 本层声明）。
NODE_PARAM_CHECKS: dict[str, Callable[[str, dict[str, Any], dict[str, Any]], None]] = {
    "iteration": _check_iteration,
    "loop": _check_loop,
    "question_classifier": _check_question_classifier,
    "parameter_extractor": _check_parameter_extractor,
    "parallel": _check_parallel,
    "subgraph": _check_subgraph,
}


def _obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """构造 params 的 JSON Schema 片段（封闭：additionalProperties=False）。"""
    return {"type": "object", "additionalProperties": False,
            "required": list(required), "properties": properties}


VERB_REGISTRY: dict[str, VerbSpec] = {
    "map": VerbSpec(
        name="map", category="数据变换",
        summary="逐项设置字段或转换大小写",
        params_schema=_obj({
            "op": {"enum": list(MAP_OPS)},
            "field": {"type": "string"},
            "value": {},
        }, required=("op",)),
        execute=_exec_map, check=_check_map,
    ),
    "filter": VerbSpec(
        name="filter", category="数据变换",
        summary="按字段与比较符筛选",
        params_schema=_obj({
            "field": {"type": "string"},
            "op": {"enum": list(FILTER_OPS)},
            "value": {},
        }, required=("field", "op")),
        execute=_exec_filter,
    ),
    "template": VerbSpec(
        name="template", category="数据变换",
        summary="用 {field} 插值渲染文本",
        params_schema=_obj({"template": {"type": "string", "minLength": 1}},
                           required=("template",)),
        execute=_exec_template,
    ),
    "branch": VerbSpec(
        name="branch", category="流程控制",
        summary="显式条件分支，产出 {branch: 标签, value: 载荷}",
        params_schema=_obj({
            "field": {"type": "string"},
            "op": {"enum": list(CONDITION_OPS)},
            "value": {},
            "then_label": {"type": "string", "minLength": 1},
            "else_label": {"type": "string", "minLength": 1},
        }, required=("field", "op", "then_label", "else_label")),
        execute=_exec_branch,
    ),
    "aggregate": VerbSpec(
        name="aggregate", category="流程控制",
        summary="循环聚合：计数/求和/最值/均值/连接/去重",
        params_schema=_obj({
            "op": {"enum": list(AGGREGATE_OPS)},
            "field": {"type": "string"},
            "sep": {"type": "string"},
        }, required=("op",)),
        execute=_exec_aggregate, check=_check_aggregate,
    ),
    "merge": VerbSpec(
        name="merge", category="流程控制",
        summary="并行汇聚：多路入边收敛成一路",
        params_schema=_obj({"mode": {"enum": list(MERGE_OPS)}}, required=("mode",)),
        execute=_exec_merge,
    ),
    "agent": VerbSpec(
        name="agent", category="Agent",
        summary="调用已注册 Agent/工具（需注入 agent_resolver）",
        params_schema=_obj({"agent": {"type": "string", "minLength": 1}},
                           required=("agent",)),
        execute=_exec_agent,
    ),
    "confirm": VerbSpec(
        name="confirm", category="人机协作",
        summary="挂起等人确认（动词位：尚未接入 HITL，执行必定失败）",
        params_schema=_obj({
            "prompt": {"type": "string", "minLength": 1},
            "role": {"type": "string"},
        }, required=("prompt",)),
        execute=_exec_confirm,
    ),
    "artifact": VerbSpec(
        name="artifact", category="输出",
        summary="产出带名字的产物信封 {artifact, kind, content}",
        params_schema=_obj({
            "name": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "minLength": 1},
        }, required=("name",)),
        execute=_exec_artifact,
    ),
    "approval": VerbSpec(
        name="approval", category="治理",
        summary="委托 proposal.py 发起一次治理操作，挂起等待人工裁决（ADR-04）",
        # op 的白名单**不在此硬编码**（否则与 proposal 漂移）：schema 只约束它是
        # 非空字符串。平台侧真正的安全边界是 _check_approval 惰性读取 proposal 白名单；
        # 离线导出脚本搬不进 SQLAlchemy 依赖，故此处 check=None——合法 op 的约束
        # 由平台 export_script 前置的 validate_dsl 兜底（见模块头诚实边界）。
        params_schema=_obj({
            "op": {"type": "string", "minLength": 1},
            "target_id": {"type": "string", "minLength": 1},
            "reason": {"type": "string"},
            "rollback": {"type": "string"},
        }, required=("op",)),
        execute=_exec_approval,
        check=None,  # 见模块头诚实边界：op 白名单由平台 export_script 前置校验把守
    ),
}

#: ``transform`` 节点的合法动词白名单（由注册表派生，**封闭**）。



#: ``input`` / ``output`` 节点的 params 契约（同样封闭）。
NODE_PARAMS_SCHEMAS: dict[str, dict[str, Any]] = {
    "input": _obj({"kind": {"enum": list(INPUT_KINDS)}, "value": {}}),
    "output": _obj({"format": {"enum": list(OUTPUT_FORMATS)}}),
    # ------------------------------------------------------------------
    # A-画布搭建器-01：13 类新增节点的参数契约（全部封闭：多余键即错）。
    # 需要注入解析器（llm/knowledge/tool/http/human_input）的节点，未注入时
    # 在执行期**诚实失败**（对齐 agent 动词），绝不假装调用成功。
    # ------------------------------------------------------------------
    # 经 provider 网关（runtime/gateway.py::ModelGateway.complete）调模型。
    "llm": _obj({
        "model": {"type": "string", "minLength": 1},
        "prompt": {"type": "string", "minLength": 1},
        "system": {"type": "string"},
        "temperature": {"type": "number"},
        "max_tokens": {"type": "integer"},
        "timeout_seconds": {"type": "number"},
    }, required=("model", "prompt")),
    # 调 services/knowledge/search.py::KnowledgeSearchService.search（现有公开函数）。
    "knowledge_retrieval": _obj({
        "query": {"type": "string", "minLength": 1},
        "top_k": {"type": "integer"},
        "mode": {"enum": list(KNOWLEDGE_MODES)},
        "document_ids": {"type": "array"},
    }, required=("query",)),
    # LLM 兜底分类：输出必须落在 classes 内，否则节点失败（不静默造类）。
    "question_classifier": _obj({
        "classes": {"type": "array"},
        "query": {"type": "string"},
        "model": {"type": "string", "minLength": 1},
        "temperature": {"type": "number"},
        "max_tokens": {"type": "integer"},
    }, required=("classes", "model")),
    # LLM 兜底抽取：fields = [{name, type, description?, required?}]。
    "parameter_extractor": _obj({
        "fields": {"type": "array"},
        "text": {"type": "string"},
        "model": {"type": "string", "minLength": 1},
        "temperature": {"type": "number"},
        "max_tokens": {"type": "integer"},
    }, required=("fields", "model")),
    # 子流程对上游列表逐项映射；subflow 是一张**合法 DSL 文档**（递归校验）。
    "iteration": _obj({
        "subflow": {"type": "object"},
        "item_field": {"type": "string"},
        "max_items": {"type": "integer"},
    }, required=("subflow",)),
    # 子流程循环：上一轮输出喂下一轮输入，直到条件满足或达迭代上界。
    "loop": _obj({
        "subflow": {"type": "object"},
        "until_field": {"type": "string"},
        "until_op": {"enum": list(CONDITION_OPS)},
        "until_value": {},
        "max_iterations": {"type": "integer"},
    }, required=("subflow",)),
    "variable_aggregator": _obj({
        "strategy": {"enum": list(VARIABLE_AGGREGATOR_STRATEGIES)},
    }),
    # 独立模板节点：与 transform.template 动词同一 {field} 插值实现（不另造语法）。
    "template": _obj({
        "template": {"type": "string", "minLength": 1},
    }, required=("template",)),
    # 标准库 urllib 实现（禁 shell）；allow_domains 是权限面板的出网白名单，
    # 提供时 URL host 必须命中其中一项（后缀匹配），否则执行期拒绝。
    "http_request": _obj({
        "url": {"type": "string", "minLength": 1},
        "method": {"enum": list(HTTP_METHODS)},
        "headers": {"type": "object"},
        "body": {},
        "timeout_seconds": {"type": "number"},
        "max_bytes": {"type": "integer"},
        "allow_domains": {"type": "array"},
    }, required=("url",)),
    # 受控执行：子进程 + 超时（上界 CODE_TIMEOUT_CAP_SECONDS）+ Python 层
    # 禁网络/禁 shell（_CODE_SANDBOX_BOOTSTRAP）。
    "code": _obj({
        "language": {"enum": list(CODE_LANGUAGES)},
        "code": {"type": "string", "minLength": 1},
        "timeout_seconds": {"type": "number"},
        "max_output_bytes": {"type": "integer"},
    }, required=("code",)),
    # 经 services/tool_registry.py::ToolRegistryService.invoke（现有公开接口）。
    "tool": _obj({
        "tool": {"type": "string", "minLength": 1},
        "arguments": {"type": "object"},
    }, required=("tool",)),
    # 挂 services/hitl.py 挂起/恢复语义：尚无输入时抛 DslSuspended（控制流
    # 信号），人工输入经注入的 human_input_provider 读取后放行。
    "human_input": _obj({
        "prompt": {"type": "string", "minLength": 1},
        "options": {"type": "array"},
        "role": {"type": "string"},
        "timeout_seconds": {"type": "integer"},
    }, required=("prompt",)),
    # 触发器/入口节点（A-画布搭建器-02）：chatflow 的 conversation 触发器是
    # 唯一合法会话入口；workflow 的入口是 manual/schedule/webhook（或 input）。
    "trigger": _obj({
        "kind": {"enum": list(TRIGGER_KINDS)},
        "config": {"type": "object"},
    }, required=("kind",)),
    # ------------------------------------------------------------------
    # N5「拓扑结构扩大数量」新增 2 类：并行扇出/汇聚 与 命名子图。
    # 分支与子图的**元素级结构**由 ``_check_parallel`` / ``_check_subgraph``
    # 逐项校验（JSON Schema 片段只约束「是数组/是字符串」，逐项语义交这两个
    # 跨字段检查——与 ``iteration.subflow`` 同一分工口径）。
    # ------------------------------------------------------------------
    "parallel": _obj({
        # [{name: 分支名, inline?: 内联子图文档, ref?: 文档级 subgraphs 里的名字}]
        "branches": {"type": "array"},
        # 并发线程数；缺省=分支数。真正生效的闸门在 _check_parallel。
        "max_workers": {"type": "integer"},
    }, required=("branches",)),
    "subgraph": _obj({
        # 引用文档级 ``subgraphs`` 里的哪一张子图（对标 LangGraph 具名子图）。
        "name": {"type": "string", "minLength": 1},
    }, required=("name",)),
}


# ---------------------------------------------------------------------------
# 编译器：校验 → 拓扑排序 → 环检测
# ---------------------------------------------------------------------------


@dataclass
class CompiledPlan:
    """编译产物：拓扑序 + 邻接表（供 Dispatcher 按边条件动态入队）。"""

    order: list[str]
    adjacency: dict[str, list[dict[str, Any]]]  # node_id -> [edge]
    indegree: dict[str, int]
    nodes: dict[str, dict[str, Any]]


def _validate_subgraph_library(
        doc: dict[str, Any],
        outer: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """校验文档级 ``subgraphs`` 命名子图库，返回 ``{名字: 子图文档}``。

    每张子图都是一张**完整 DSL 文档**，递归过 :func:`validate_dsl`（单一真源），
    且必须能作为子流程运行（含 ``input`` 入口节点）。命名重复即编译错误——
    绝不让「后定义的覆盖前面的」这种静默行为存在。

    ``outer`` 是**外层文档可见的子图名表**：子图文档可以按名引用父图库的子图
    （与 :func:`run_dsl` 运行期的库合并语义一致），编译期必须同语义，否则会
    出现「编译期报错 / 运行期能跑」的双标。
    """
    library = doc.get("subgraphs")
    if library is None:
        return {}
    if not isinstance(library, dict):
        raise DslValidationError("subgraphs 必须是对象（名字 -> 子图文档）")
    declared: dict[str, dict[str, Any]] = {}
    for name in library:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(name)):
            raise DslValidationError(f"子图名不合法: {name!r}")
        declared[str(name)] = library[name]
    # 先把本层所有名字收齐，再校验每张子图：允许同层子图互相引用。
    visible: dict[str, Any] = {**(outer or {}), **declared}
    for name, sub in declared.items():
        _check_subflow_doc(f"subgraphs[{name}]", sub, library=visible)
        if not _subflow_entry_ok(sub):
            raise DslValidationError(
                f"子图 {name!r} 必须包含一个 input 节点作为入口"
                "（子流程由上游 payload 喂输入，没有入口就无法运行）")
    return declared


def _check_subgraph_refs(node_id: str, ntype: str, params: dict[str, Any],
                         library: dict[str, dict[str, Any]]) -> None:
    """``subgraph.name`` 与 ``parallel.branches[].ref`` 必须在子图库中真实存在。

    编译期就报「引用了未定义的子图」，而不是运行到该节点才失败——否则画布作者
    会拿到一份「前半段跑通、后半段炸掉」的运行记录，很难定位是引用写错。
    """
    if ntype == "subgraph":
        name = params.get("name")
        if name not in library:
            raise DslFieldError(
                f"节点 {node_id} 引用了未定义的子图 {name!r}；"
                f"可用子图: {sorted(library) or '（文档未声明 subgraphs）'}",
                node_id=node_id, field_path="params.name")
    elif ntype == "parallel":
        for idx, branch in enumerate(params.get("branches") or []):
            if not isinstance(branch, dict):
                continue
            ref = branch.get("ref")
            if isinstance(ref, str) and ref and ref not in library:
                raise DslFieldError(
                    f"节点 {node_id} 的 parallel.branches[{idx}] 引用了未定义的"
                    f"子图 {ref!r}；可用子图: {sorted(library) or '（文档未声明 subgraphs）'}",
                    node_id=node_id, field_path=f"params.branches.{idx}.ref")



def validate_dsl(doc: Any, *,
                 _visible_subgraphs: dict[str, Any] | None = None) -> dict[str, Any]:
    """结构校验（手写轻量校验，覆盖 JSON Schema 中的约束）。

    ``_visible_subgraphs`` 是**递归内部通道**：校验嵌套子流程时，把外层文档的子图
    名表传下去，使「子流程按名引用父图子图」在编译期与运行期同语义（下层遮蔽上层，
    与 :func:`run_dsl` 的库合并顺序一致）。顶层调用不需要传。
    """
    if not isinstance(doc, dict):
        raise DslValidationError("DSL 文档必须是 JSON 对象")
    if doc.get("version") != "1":
        raise DslValidationError("version 必须为 \"1\"")
    # A-画布搭建器-05：可选 flow_type（chatflow/workflow），白名单外即错。
    flow_type = doc.get("flow_type")
    if flow_type is not None and flow_type not in FLOW_TYPES:
        raise DslValidationError(f"flow_type 必须是 {FLOW_TYPES}，实际是 {flow_type!r}")
    nodes = doc.get("nodes")
    edges = doc.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise DslValidationError("nodes 必须是非空数组")
    if not isinstance(edges, list):
        raise DslValidationError("edges 必须是数组")
    # N5：图级规模闸门（对标 LangGraph 的大图承载）。不设闸门时一张超大图会把
    # 拓扑排序/入队排序拖成 O(n²) 乃至更差，且没有任何可解释的失败理由。
    if len(nodes) > MAX_NODES_PER_GRAPH:
        raise DslValidationError(
            f"节点数 {len(nodes)} 超过单图上限 {MAX_NODES_PER_GRAPH}；"
            "请拆成多张图或用 subgraph 复用")
    if len(edges) > MAX_EDGES_PER_GRAPH:
        raise DslValidationError(
            f"边数 {len(edges)} 超过单图上限 {MAX_EDGES_PER_GRAPH}")

    # N5：文档级命名子图库——先整体校验，再作为 ``ref`` 的唯一可解析来源。
    # 可见名表 = 外层继承 + 本层声明（本层遮蔽外层，与 run_dsl 合并顺序一致）。
    library: dict[str, Any] = {
        **(_visible_subgraphs or {}),
        **_validate_subgraph_library(doc, outer=_visible_subgraphs),
    }

    ids: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not n["id"]:
            raise DslValidationError("每个节点必须有非空字符串 id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n["id"]):
            raise DslValidationError(f"节点 id 不合法: {n['id']!r}")
        if n["id"] in ids:
            raise DslValidationError(f"节点 id 重复: {n['id']}")
        ids.add(n["id"])
        if n.get("type") not in NODE_TYPES:
            raise DslValidationError(f"节点 {n['id']} type 必须是 {NODE_TYPES}")
        if n["type"] == "transform":
            verb = n.get("verb")
            # 封闭性闸门：白名单外的动词**明确报错**（并回显实际写出的动词，
            # 便于调用方/模型自我修正），绝不静默忽略。
            if verb not in VERB_REGISTRY:
                raise DslValidationError(
                    f"transform 节点 {n['id']} verb 必须是 {TRANSFORM_VERBS}，"
                    f"实际是 {verb!r}")
            params = n.get("params", {})
            if not isinstance(params, dict):
                raise DslValidationError(f"节点 {n['id']} params 必须是对象")
            _validate_params_against(n["id"], VERB_REGISTRY[verb].params_schema, params)
            spec = VERB_REGISTRY[verb]
            if spec.check is not None:
                spec.check(n["id"], params)
        else:
            params = n.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise DslValidationError(f"节点 {n['id']} params 必须是对象")
            _validate_params_against(n["id"], NODE_PARAMS_SCHEMAS[n["type"]], params)
            # 节点级跨字段检查（iteration/loop 子流程、parallel 分支、classifier 类别等）。
            node_check = NODE_PARAM_CHECKS.get(n["type"])
            if node_check is not None:
                node_check(n["id"], params, library)
        # N5：按名引用子图必须在文档级 ``subgraphs`` 库中真实存在（编译期拦，
        # 不留到运行期才因「找不到子图」失败）。
        _check_subgraph_refs(n["id"], n["type"], n.get("params") or {}, library)

    for e in edges:
        if not isinstance(e, dict):
            raise DslValidationError("边必须是对象")
        src, dst = e.get("from"), e.get("to")
        if src not in ids or dst not in ids:
            raise DslValidationError(f"边 {src!r}->{dst!r} 引用了未定义节点")
        if "condition" in e and e["condition"] is not None:
            c = e["condition"]
            if not isinstance(c, dict) or c.get("op") not in CONDITION_OPS \
                    or not isinstance(c.get("field"), str) or not c["field"]:
                raise DslValidationError(
                    f"边 {src}->{dst} condition 需要 field/op/value 且 op 属于 {CONDITION_OPS}")
    return doc


def _validate_params_against(node_id: str, schema: dict[str, Any],
                            params: dict[str, Any]) -> None:
    """按 params 的 JSON Schema 片段校验（封闭：多余键即错）。"""
    props: dict[str, Any] = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in params:
            raise DslValidationError(f"节点 {node_id} 缺少参数 {key}")
    for key in params:
        if key not in props:
            raise DslValidationError(
                f"节点 {node_id} 参数 {key!r} 不被支持，允许的参数是 {tuple(props)}")
    for key, sub in props.items():
        if key not in params:
            continue
        value = params[key]
        if "enum" in sub and value not in sub["enum"]:
            raise DslValidationError(
                f"节点 {node_id} 参数 {key} 必须是 {tuple(sub['enum'])}")
        expected = sub.get("type")
        if expected == "string":
            if not isinstance(value, str):
                raise DslValidationError(f"节点 {node_id} 参数 {key} 必须是字符串")
            if len(value) < sub.get("minLength", 0):
                raise DslValidationError(f"节点 {node_id} 参数 {key} 不能为空")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise DslValidationError(f"节点 {node_id} 参数 {key} 必须是整数")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DslValidationError(f"节点 {node_id} 参数 {key} 必须是数值")
        elif expected == "boolean" and not isinstance(value, bool):
            raise DslValidationError(f"节点 {node_id} 参数 {key} 必须是布尔值")
        elif expected == "array" and not isinstance(value, list):
            raise DslValidationError(f"节点 {node_id} 参数 {key} 必须是数组")
        elif expected == "object" and not isinstance(value, dict):
            raise DslValidationError(f"节点 {node_id} 参数 {key} 必须是对象")


def canonical_dsl(doc: Any, *,
                  visible_subgraphs: dict[str, Any] | None = None) -> dict[str, Any]:
    """规范化 DSL 文档（省略空params / 非transform 的 verb / 空 condition）。

    与既有 :func:`~find_yourself.services.workflow_gen.graph_to_dsl` 的省略规则
    一致，因此规范化结果可以直接当DSL 文档使用（等价于画布的语义层）。

    ``visible_subgraphs`` 是调用方从外部注入的子图名表：子流程里按名引用父图子图时，
    没有它本校验会误报「引用了未定义的子图」。

    N5：``subgraphs`` **进入摘要**。子图库是语义的一部分——两张主图节点边完全相同
    但子图库不同，运行结果可以天差地别；若摘要不覆盖它，人所审批的那份 digest
    就无法代表实际执行的行为（TOCTOU 防护被绕过）。
    """
    validate_dsl(doc, _visible_subgraphs=visible_subgraphs)
    nodes: list[dict[str, Any]] = []
    for n in doc["nodes"]:
        entry: dict[str, Any] = {"id": n["id"], "type": n["type"]}
        if n["type"] == "transform":
            entry["verb"] = n["verb"]
        params = n.get("params") or {}
        if params:
            entry["params"] = params
        nodes.append(entry)
    edges: list[dict[str, Any]] = []
    for e in doc["edges"]:
        entry = {"from": e["from"], "to": e["to"]}
        if e.get("condition") is not None:
            entry["condition"] = e["condition"]
        edges.append(entry)
    canonical: dict[str, Any] = {"version": doc["version"], "nodes": nodes,
                                 "edges": edges}
    # flow_type 参与规范化（它改变触发器/入口语义，摘要必须随它变）。
    if doc.get("flow_type") is not None:
        canonical["flow_type"] = doc["flow_type"]
    # N5：命名子图库同样参与摘要（否则改子图不改 digest，审批失去意义）。
    library = doc.get("subgraphs")
    if isinstance(library, dict) and library:
        canonical["subgraphs"] = {
            name: canonical_dsl(sub, visible_subgraphs=library)
            for name, sub in sorted(library.items())
        }
    return canonical


def dsl_digest(doc: Any, *,
               visible_subgraphs: dict[str, Any] | None = None) -> str:
    """DSL 文档的规范化摘要（SHA-256 前16 位）。

    基于 :func:`canonical_dsl`（省略规则归一），因此「语义等价的两张画布摘要相同」。
    用途：人工裁决的 TOCTOU 防护——人看的是某个版本的图，裁决时必须确认还是那一版
    （对齐 dev-hitl 的 ``expected_version`` 乐观锁）。
    """
    canonical = canonical_dsl(doc, visible_subgraphs=visible_subgraphs)
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]




def compile_dsl(doc: Any, *,
                 visible_subgraphs: dict[str, Any] | None = None) -> CompiledPlan:
    """校验 + 拓扑排序（Kahn）+ 环检测。

    导出脚本**没有** ADR-02 的类型化 IR 闸门（:mod:`dsl_ir` 依赖 pydantic 且反向
    依赖本解释器的 :data:`VERB_REGISTRY`，搬不进离线单文件）；结构校验 + 参数封闭
    校验（:func:`_validate_params_against`）已足够把非法文档挡在执行之前。
    平台侧 :func:`dsl_canvas.compile_dsl` 仍会追加该闸门。
    原注释（已移除的闸门）：
    节点/边的强类型契约（``extra="forbid"``）在执行之前就把类型错误拦下，且错误能
    定位到「哪张画布的哪个节点、哪个字段」。既有合法文档全部照常通过。

    ``visible_subgraphs`` 是**调用方从外部注入**的子图名表（如 DSL SDK 场景：库存在
    编排器里而不在文档里）。不传就等于文档自声明的库，编译期引用校验据此判定。
    """
    validate_dsl(doc, _visible_subgraphs=visible_subgraphs)
    nodes = {n["id"]: n for n in doc["nodes"]}
    adjacency: dict[str, list[dict[str, Any]]] = {nid: [] for nid in nodes}
    indegree = {nid: 0 for nid in nodes}
    for e in doc["edges"]:
        adjacency[e["from"]].append(e)
        indegree[e["to"]] += 1
    pristine_indegree = dict(indegree)  # Kahn 会原地扣减，Dispatcher 需要原始入度

    # Kahn 拓扑排序；若无法覆盖全部节点则存在环。
    ready = sorted(nid for nid, d in indegree.items() if d == 0)
    order: list[str] = []
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
        raise DslValidationError(f"DSL 存在环，涉及节点: {cyclic}")
    return CompiledPlan(order=order, adjacency=adjacency,
                        indegree=pristine_indegree, nodes=nodes)


def execute_node(node: dict[str, Any], payload: Any, *,
                 upstreams: list[Any] | None = None,
                 agent_resolver: AgentResolver | None = None,
                 confirm_decision: ConfirmDecisionReader | None = None,
                 approval_signal: ApprovalSignal | None = None,
                 llm_resolver: LlmResolver | None = None,
                 knowledge_resolver: KnowledgeResolver | None = None,
                 tool_resolver: ToolResolver | None = None,
                 http_resolver: HttpResolver | None = None,
                 human_input_provider: HumanInputProvider | None = None,
                 execution_id: str | None = None,
                 dsl_digest: str = "",
                 subflows: dict[str, Any] | None = None) -> Any:
    """执行单个节点。payload 为所有入边数据的合并（None 表示无输入）。

    ``upstreams`` 是全部入边上游的输出列表，供 :verb:`merge` 与
    ``variable_aggregator`` 这类多路节点使用；其余节点只用 ``payload``。
    注入式解析器（llm/knowledge/tool/http/human_input）只被对应新节点类型
    使用，未注入时这些节点**诚实失败**（绝不假装调用成功）。
    ``subflows`` 是本运行可见的命名子图库（``parallel`` / ``subgraph`` 按名取图）。
    """
    ntype = node["type"]
    params = node.get("params", {}) or {}

    if ntype == "input":
        kind = params.get("kind", "literal")
        if kind == "text_lines":
            text = params.get("value", "")
            if not isinstance(text, str):
                raise DslValidationError(f"input 节点 {node['id']} text_lines.value 必须是字符串")
            return [line for line in text.splitlines() if line.strip()]
        if kind == "literal":
            return params.get("value")
        raise DslValidationError(f"input 节点 {node['id']} kind 必须是 {INPUT_KINDS}")

    if ntype == "transform":
        verb = node["verb"]
        spec = VERB_REGISTRY.get(verb)
        if spec is None:
            # 兜底闸门：validate_dsl 已拦住，这里是「不认得的动词绝不静默执行」。
            raise DslValidationError(
                f"transform 节点 {node['id']} 的 verb 不在受限动词集内: {verb!r}")
        if payload is None:
            raise DslValidationError(f"transform 节点 {node['id']} 无上游输入")
        return spec.execute(VerbContext(
            node_id=node["id"], payload=payload, params=params,
            upstreams=list(upstreams or []), agent_resolver=agent_resolver,
            confirm_decision=confirm_decision, approval_signal=approval_signal,
            execution_id=execution_id,
            dsl_digest=dsl_digest))

    if ntype == "output":
        fmt = params.get("format", "json")
        if fmt == "text":
            if isinstance(payload, list):
                return "\n".join(str(x) for x in payload)
            return "" if payload is None else str(payload)
        if fmt == "json":
            return payload
        raise DslValidationError(f"output 节点 {node['id']} format 必须是 {OUTPUT_FORMATS}")

    # -- A-画布搭建器-01：13 类新增节点 -------------------------------------
    if ntype == "llm":
        return _exec_node_llm(node["id"], params, payload, llm_resolver)
    if ntype == "knowledge_retrieval":
        return _exec_node_knowledge(node["id"], params, payload, knowledge_resolver)
    if ntype == "question_classifier":
        return _exec_node_question_classifier(node["id"], params, payload, llm_resolver)
    if ntype == "parameter_extractor":
        return _exec_node_parameter_extractor(node["id"], params, payload, llm_resolver)
    if ntype == "iteration":
        return _exec_node_iteration(node["id"], params, payload, _resolvers_dict(
            agent_resolver=agent_resolver, llm_resolver=llm_resolver,
            knowledge_resolver=knowledge_resolver, tool_resolver=tool_resolver,
            http_resolver=http_resolver, human_input_provider=human_input_provider,
            confirm_decision=confirm_decision, approval_signal=approval_signal,
            subflows=subflows))
    if ntype == "loop":
        return _exec_node_loop(node["id"], params, payload, _resolvers_dict(
            agent_resolver=agent_resolver, llm_resolver=llm_resolver,
            knowledge_resolver=knowledge_resolver, tool_resolver=tool_resolver,
            http_resolver=http_resolver, human_input_provider=human_input_provider,
            confirm_decision=confirm_decision, approval_signal=approval_signal,
            subflows=subflows))
    if ntype == "parallel":
        return _exec_node_parallel(node["id"], params, payload, _resolvers_dict(
            agent_resolver=agent_resolver, llm_resolver=llm_resolver,
            knowledge_resolver=knowledge_resolver, tool_resolver=tool_resolver,
            http_resolver=http_resolver, human_input_provider=human_input_provider,
            confirm_decision=confirm_decision, approval_signal=approval_signal,
            subflows=subflows))
    if ntype == "subgraph":
        return _exec_node_subgraph(node["id"], params, payload, _resolvers_dict(
            agent_resolver=agent_resolver, llm_resolver=llm_resolver,
            knowledge_resolver=knowledge_resolver, tool_resolver=tool_resolver,
            http_resolver=http_resolver, human_input_provider=human_input_provider,
            confirm_decision=confirm_decision, approval_signal=approval_signal,
            subflows=subflows))
    if ntype == "variable_aggregator":
        return _exec_node_variable_aggregator(node["id"], params, payload,
                                              list(upstreams or []))
    if ntype == "template":
        return _exec_node_template(node["id"], params, payload)
    if ntype == "http_request":
        return _exec_node_http(node["id"], params, payload, http_resolver)
    if ntype == "code":
        return _exec_node_code(node["id"], params, payload)
    if ntype == "tool":
        return _exec_node_tool(node["id"], params, tool_resolver)
    if ntype == "human_input":
        return _exec_node_human_input(node["id"], params, payload,
                                      human_input_provider, execution_id, dsl_digest)
    if ntype == "trigger":
        return _exec_node_trigger(node["id"], params)

    raise DslValidationError(f"未知节点类型: {ntype}")


#: 子流程递归执行时透传给 ``run_dsl`` 的解析器集合（None 项被过滤）。
def _resolvers_dict(**kwargs: Any) -> dict[str, Any]:
    return kwargs


@dataclass
class NodeLog:
    node_id: str
    node_type: str
    verb: str | None
    status: str  # succeeded | skipped | failed | suspended
    input: Any = None
    output: Any = None
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "node_type": self.node_type, "verb": self.verb,
            "status": self.status, "input": self.input, "output": self.output,
            "error": self.error, "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class RunResult:
    run_id: str
    status: str  # succeeded | failed | suspended
    doc: dict[str, Any]
    logs: list[NodeLog] = field(default_factory=list)
    output: Any = None
    error: str | None = None
    #: 由调用方显式传入的稳定执行标识（挂起时用于关联 HITL interrupt）。
    execution_id: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "status": self.status, "dsl": self.doc,
            "output": self.output, "error": self.error,
            "execution_id": self.execution_id,
            "created_at": self.created_at,
            "logs": [log.to_dict() for log in self.logs],
        }


def _emit(on_event: Callable[[str, dict], None] | None,
          event_type: str, data: dict) -> None:
    """观测侧回调的安全出口：``on_event`` 为 None 或抛异常都不影响运行。

    观测通道（G2 实时推送）是**叠加**在确定性执行之上的：任何回调异常都绝不
    允许污染或中断一次运行。
    """
    if on_event is None:
        return
    try:
        on_event(event_type, data)
    except Exception:  # noqa: BLE001 - 观测侧异常绝不允许中断运行
        pass


def run_dsl(doc: dict[str, Any], *, run_id: str | None = None,
            agent_resolver: AgentResolver | None = None,
            confirm_decision: ConfirmDecisionReader | None = None,
            approval_signal: ApprovalSignal | None = None,
            llm_resolver: LlmResolver | None = None,
            knowledge_resolver: KnowledgeResolver | None = None,
            tool_resolver: ToolResolver | None = None,
            http_resolver: HttpResolver | None = None,
            human_input_provider: HumanInputProvider | None = None,
            execution_id: str | None = None,
            recursion_limit: int | None = None,
            subgraphs: dict[str, Any] | None = None,
            on_event: Callable[[str, dict], None] | None = None) -> RunResult:
    """编译并执行一份 DSL 文档，返回带逐步日志的运行结果。

    调度采用「节点完成后按后继边条件入队」的动态展开：
    入度为 0 的节点先入队；节点成功后逐条评估出边条件，满足者使其
    后继节点待入队计数减一，减到 0 即入队；全部入边条件都不满足的
    后继节点记为 skipped。

    :param agent_resolver: 仅被 :verb:`agent` 使用；不传时该动词明确失败
        （平台不会在无解析器的情况下假装调用成功）。
    :param confirm_decision: :func:`ConfirmDecisionReader`。``confirm`` 节点据此
        查询人工裁决：有approve/reject 就放行/驳回，没有就抛
        :class:`DslSuspended`。
    :param approval_signal: :func:`ApprovalSignal`。``approval`` 节点据此读取
        **治理层**（services/proposal.py）裁决后的信号：approve 放行、reject 驳回、
        尚无裁决则抛 :class:`DslSuspended`。DSL 只读信号，绝不自己产生信号。
    :param llm_resolver: ``llm`` / ``question_classifier`` / ``parameter_extractor``
        节点的模型解析器（生产侧经 runtime/gateway.py provider 网关）；不传时
        这些节点明确失败。
    :param knowledge_resolver: ``knowledge_retrieval`` 节点的检索解析器（生产侧
        经 services/knowledge/search.py 现有公开检索）；不传时明确失败。
    :param tool_resolver: ``tool`` 节点的工具解析器（生产侧经
        services/tool_registry.py::ToolRegistryService.invoke）；不传时明确失败。
    :param http_resolver: ``http_request`` 节点的 HTTP 解析器；不传时节点用
        标准库 urllib 直连（受 allow_domains 白名单约束）。注入时**替代**直连。
    :param human_input_provider: ``human_input`` 节点的人工输入提供器（对齐
        services/hitl.py 的挂起-恢复语义）；不传或返回 None 时抛
        :class:`DslSuspended`。
    :param execution_id: **由调用方显式传入**的稳定执行标识（不要用 ``run_id``：
        它每次运行重新生成且只在内存里，重启后恢复链会断）。它进入
        :attr:`DslSuspended.checkpoint`，供上层把 HITL interrupt 与本执行关联。
    :param recursion_limit: 图执行的步数硬上限（对标 LangGraph 的同名参数）。
        缺省 :data:`DEFAULT_RECURSION_LIMIT`，超出即裁到
        :data:`RECURSION_LIMIT_CAP`——绝不接受一个能把服务拖死的无界值。
    :param subgraphs: 父图传下来的文档级子图库。子流程内部若也要按名引用子图，
        靠它继承；本图自己声明的 ``subgraphs`` 优先。

    :raises DslSuspended: 走到 ``confirm`` / ``approval`` / ``human_input`` 且尚无
        人工输入/裁决时。**该异常穿透本函数**（不被转成
        ``RunResult.status="failed"``），上层编排据此建 interrupt 并在人工裁决后
        重开一轮。
    """
    plan = compile_dsl(doc, visible_subgraphs=subgraphs)
    rid = run_id or f"dsl-{uuid.uuid4().hex[:12]}"
    result = RunResult(run_id=rid, status="succeeded", doc=doc,
                       execution_id=execution_id)
    _emit(on_event, "run_started", {"run_id": rid, "status": "running"})
    # N5：文档级命名子图库在运行期对 ``parallel.branches[].ref`` / ``subgraph.name``
    # 可见。子流程继承父图的库（本图自声明的同名key 优先）。**必须先于 digest
    # 计算**——``canonical_dsl`` 内部会再校验一次文档，库不可见就会把一份合法的
    # 文档误判成「引用了未定义的子图」。
    subflows = {**(subgraphs or {}), **(doc.get("subgraphs") or {})}
    # 供confirm 裁决读取器自校验「人所见即所批」的文档摘要。
    digest = dsl_digest(doc, visible_subgraphs=subgraphs)
    # N5：步数硬闸门——被裁到合法区间，不存在「传进来一个 10**9 就真的跑 10**9」。
    try:
        limit = int(recursion_limit if recursion_limit is not None
                    else DEFAULT_RECURSION_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_RECURSION_LIMIT
    limit = max(1, min(limit, RECURSION_LIMIT_CAP))
    steps = 0
    #: 步数闸门是否触发（触发后直接收尾，不把剩余节点谎报成 skipped）。
    step_guard_hit = False

    outputs: dict[str, Any] = {}
    # node -> 剩余未满足的入边数（入队闸门）
    pending = dict(plan.indegree)
    # node -> 已有一 Condition 通过（用于 skipped 判定）
    triggered: dict[str, bool] = {nid: False for nid in plan.nodes}
    queue: list[str] = [nid for nid in plan.order if pending[nid] == 0]
    # 拓扑序 -> 排名表（稳定出队用，避免每次 O(n) 线性查找）。
    _rank = {nid: i for i, nid in enumerate(plan.order)}

    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def enqueue_ready(nid: str) -> None:
        pending[nid] -= 1
        if pending[nid] == 0 and not triggered[nid]:
            pending[nid] = 1  # 永不出队 → 最后统一记 skipped
        elif pending[nid] == 0:
            queue.append(nid)

    try:
        while True:
            while queue:
                # N5：按拓扑序稳定出队。用预计算的 rank 表替代每次
                # ``plan.order.index`` —— 后者是 O(n) 线性扫描，整轮下来 O(n²)。
                queue.sort(key=lambda nid: _rank[nid])
                nid = queue.pop(0)
                steps += 1
                if steps > limit:
                    # fail-closed：超限即失败，绝不「跑着跑着就没了」。
                    result.status = "failed"
                    result.error = (
                        f"图执行步数超过 recursion_limit={limit}"
                        f"（已跑 {steps - 1} 步后仍需继续）；"
                        "请提高上界或拆分流程")
                    step_guard_hit = True
                    break
                node = plan.nodes[nid]
                log = NodeLog(node_id=nid, node_type=node["type"],
                              verb=node.get("verb"), status="succeeded", started_at=now())
                result.logs.append(log)
                _emit(on_event, "node_started", {
                    "node_id": nid, "node_type": node["type"],
                    "verb": node.get("verb")})
                # 合并所有入边上游输出（payload 取第一个非 None，与既有语义一致）；
                # upstreams 保留全部，供 merge 这类多路动词使用。
                incoming = [e["from"] for e in doc["edges"] if e["to"] == nid]
                payload = None
                upstreams: list[Any] = []
                for src in incoming:
                    up = _latest_log(result, src)
                    if up and up.status == "succeeded":
                        upstreams.append(outputs.get(src))
                        if payload is None and outputs.get(src) is not None:
                            payload = outputs[src]
                log.input = payload
                try:
                    out = execute_node(node, payload, upstreams=upstreams,
                                       agent_resolver=agent_resolver,
                                       confirm_decision=confirm_decision,
                                       approval_signal=approval_signal,
                                       llm_resolver=llm_resolver,
                                       knowledge_resolver=knowledge_resolver,
                                       tool_resolver=tool_resolver,
                                       http_resolver=http_resolver,
                                       human_input_provider=human_input_provider,
                                       execution_id=execution_id,
                                       dsl_digest=digest,
                                       subflows=subflows)
                    outputs[nid] = out
                    log.output = out
                    log.finished_at = now()
                    _emit(on_event, "node_finished", log.to_dict())
                except DslSuspended:
                    # 控制流信号，**不是节点失败**：必须穿透，否则挂起会静默退化成
                    # 「节点 failed + 分支终止」，上层永远等不到 interrupt。
                    # 本节点的日志如实记为 suspended（已完成的上游日志保留在result 里，
                    # 供上层看到「跑到哪儿了」）。
                    log.status = "suspended"
                    log.finished_at = now()
                    _emit(on_event, "node_finished", log.to_dict())
                    result.status = "suspended"
                    result.error = None
                    raise
                except Exception as exc:  # 节点失败 → 该分支终止
                    log.status = "failed"
                    log.error = str(exc)
                    log.finished_at = now()
                    _emit(on_event, "node_finished", log.to_dict())
                    result.status = "failed"
                    result.error = f"节点 {nid} 执行失败: {exc}"
                    continue
                # 动态展开：按出边条件决定后继是否入队
                for edge in plan.adjacency[nid]:
                    cond = edge.get("condition")
                    passed = True if not cond else _compare(
                        _lookup(out, cond["field"]), cond["op"], cond.get("value"))
                    if passed:
                        triggered[edge["to"]] = True
                        if pending[edge["to"]] > 0:
                            pending[edge["to"]] -= 1
                            if pending[edge["to"]] == 0:
                                queue.append(edge["to"])
                    else:
                        enqueue_ready(edge["to"])
            # 步数闸门触发时直接收尾：既不继续跑，也不把剩余节点谎报成 skipped。
            if step_guard_hit:
                break
            # 跳过传播：未被触发的节点按拓扑序标记 skipped，并把其出边按
            # 「条件不满足」向下传播；若某下游已被其他分支触发且计数归零，
            # 则重新进入主循环执行（requeue）。
            requeued = False
            for nid in plan.order:
                if _latest_log(result, nid) is not None:
                    continue
                node = plan.nodes[nid]
                skipped_log = NodeLog(
                    node_id=nid, node_type=node["type"], verb=node.get("verb"),
                    status="skipped", started_at=now(), finished_at=now())
                result.logs.append(skipped_log)
                _emit(on_event, "node_finished", skipped_log.to_dict())
                for edge in plan.adjacency[nid]:
                    dst = edge["to"]
                    pending[dst] -= 1
                    if pending[dst] == 0 and triggered[dst] and \
                            _latest_log(result, dst) is None:
                        queue.append(dst)
                        requeued = True
            if not requeued:
                break
        if result.status == "succeeded":
            outs = [outputs[n["id"]] for n in doc["nodes"]
                    if n["type"] == "output" and n["id"] in outputs]
            result.output = outs[-1] if outs else outputs.get(plan.order[-1])
    except DslSuspended:
        # 已在节点级记为 suspended；这里原样再抛一次，确保不被降级成 failed。
        raise
    except Exception as exc:  # 编译期错误等
        result.status = "failed"
        result.error = str(exc)
    # 收尾事件：``DslSuspended`` 穿透时走 ``raise``，故挂起运行**不发** run_finished
    # （上层据此另建 interrupt，而不是把它误读成一次跑完）。
    _emit(on_event, "run_finished", {
        "run_id": rid, "status": result.status,
        "output": result.output, "error": result.error})
    return result


def _latest_log(result: RunResult, node_id: str) -> NodeLog | None:
    """按节点 id 取最近一条日志（无则 None）。"""
    for log in reversed(result.logs):
        if log.node_id == node_id:
            return log
    return None



# --------------------------------------------------------------------------- #
# 导出脚本适配层
#
# 上面搬运来的是平台的 ``run_dsl(doc, ...) -> RunResult``；导出脚本的入口要的是
# ``(输出, 逐步日志)``，且日志必须能直接 ``json.dumps``。本段只做三件事：
#   1) 应用 ``--payload``；2) ``NodeLog`` -> dict；3) 控制流信号统一成 DslError。
# **不重写调度**：入队 / 条件边 / 跳过传播 / 失败传播全在 ``run_dsl`` 里，
# 与平台同源 —— 这正是本切片要消灭「校验放行、执行拒绝」双标的地方。
# --------------------------------------------------------------------------- #


def _apply_payload(doc, payload_override):
    """把 ``--payload`` 写进第一个 ``literal`` input 节点（没有则静默忽略）。"""
    for node in doc.get("nodes") or []:
        if node.get("type") != "input":
            continue
        if (node.get("params") or {}).get("kind", "literal") == "literal":
            node.setdefault("params", {})["value"] = payload_override
            return


def run(doc, payload_override=None):
    """顺序执行一份 DSL 文档，返回 ``(输出, 逐步日志)``。"""
    if payload_override is not None:
        _apply_payload(doc, payload_override)
    try:
        result = run_dsl(doc)
    except DslSuspended as exc:
        # 人工裁决类节点在离线脚本里没有裁决来源，如实报失败而不是假装跑完。
        raise DslError("DSL 执行被挂起（导出脚本无法提供人工裁决）: %s" % exc)
    return result.output, [log.to_dict() for log in result.logs]
'''


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
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

#: 平台地址占位符 —— 由使用者替换为本机平台地址；本地模式不使用。
BASE_URL = "{base_url_placeholder}"

#: DSL 字面量用 ``json.loads`` 还原，而不是当 Python 字面量直接内联：
#: ``json.dumps`` 的 ``null/true/false`` 不是 Python 字面量，直接内联会让脚本
#: 一导入就 ``NameError``（含布尔/空值的工作流必然崩溃）。
DSL = json.loads({dsl_literal!r})

VALID_NODE_TYPES = {node_types!r}
VALID_VERBS = {verbs!r}
VALID_INPUT_KINDS = {input_kinds!r}
VALID_OUTPUT_FORMATS = {output_formats!r}
VALID_MAP_OPS = {map_ops!r}
VALID_FILTER_OPS = {filter_ops!r}
VALID_CONDITION_OPS = {condition_ops!r}
VALID_AGGREGATE_OPS = {aggregate_ops!r}
VALID_MERGE_OPS = {merge_ops!r}
VALID_TRIGGER_KINDS = {trigger_kinds!r}
VALID_VARIABLE_AGGREGATOR_STRATEGIES = {variable_aggregator_strategies!r}
VALID_HTTP_METHODS = {http_methods!r}
VALID_CODE_LANGUAGES = {code_languages!r}

CODE_TIMEOUT_CAP_SECONDS = {code_timeout_cap_seconds!r}
SUBFLOW_MAX_DEPTH = {subflow_max_depth!r}
ITERATION_DEFAULT_MAX_ITEMS = {iteration_default_max_items!r}
ITERATION_MAX_ITEMS_CAP = {iteration_max_items_cap!r}
LOOP_DEFAULT_MAX_ITERATIONS = {loop_default_max_iterations!r}
LOOP_MAX_ITERATIONS_CAP = {loop_max_iterations_cap!r}
PARALLEL_MAX_BRANCHES = {parallel_max_branches!r}
DEFAULT_RECURSION_LIMIT = {default_recursion_limit!r}
MAX_NODES_PER_GRAPH = {max_nodes_per_graph!r}
MAX_EDGES_PER_GRAPH = {max_edges_per_graph!r}

#: 子流程容器的运行期上下文键（与平台 ``execute_node(subflows=...)`` 同名）。
_SUBFLOWS_KEY = "subflows"


# --------------------------------------------------------------------------- #
# 受限解释器（与平台 services/dsl_canvas.py 语义一致）
#
# 整段以命名占位符注入。注意方向相反的两条规则：
#   * 被替换进去的解释器源码一个花括号都不用翻倍 —— ``str.format`` 不会二次
#     处理替换值。这是把近千行执行语义搬进模板而不引入转义事故的关键。
#   * 但**本注释块自己**属于模板本体，字面花括号必须写成双份，否则会被当成
#     替换域：空花括号触发自动编号直接抛 IndexError；与某个关键字恰好同名的
#     则被静默替换成整段源码塞进注释，产出的脚本当场 SyntaxError。
#     本块因此刻意不写任何字面花括号举例。
# --------------------------------------------------------------------------- #

{interpreter_source}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


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
    # 有节点失败就不许 exit 0：否则调用方（CI / 父脚本 / shell ``&&`` 链）会把
    # 「跑不出结果」当成「跑成功」——这正是本脚本过去最有害的谎报成功缺陷。
    for entry in logs:
        if entry.get("status") == "failed":
            print("工作流执行失败：节点 %s -> %s"
                  % (entry["node_id"], entry.get("error")), file=sys.stderr)
            return 1
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
        aggregate_ops=list(AGGREGATE_OPS),
        merge_ops=list(MERGE_OPS),
        trigger_kinds=list(TRIGGER_KINDS),
        variable_aggregator_strategies=list(VARIABLE_AGGREGATOR_STRATEGIES),
        http_methods=list(HTTP_METHODS),
        code_languages=list(CODE_LANGUAGES),
        code_timeout_cap_seconds=CODE_TIMEOUT_CAP_SECONDS,
        subflow_max_depth=SUBFLOW_MAX_DEPTH,
        iteration_default_max_items=ITERATION_DEFAULT_MAX_ITEMS,
        iteration_max_items_cap=ITERATION_MAX_ITEMS_CAP,
        loop_default_max_iterations=LOOP_DEFAULT_MAX_ITERATIONS,
        loop_max_iterations_cap=LOOP_MAX_ITERATIONS_CAP,
        parallel_max_branches=PARALLEL_MAX_BRANCHES,
        default_recursion_limit=DEFAULT_RECURSION_LIMIT,
        max_nodes_per_graph=MAX_NODES_PER_GRAPH,
        max_edges_per_graph=MAX_EDGES_PER_GRAPH,
        interpreter_source=_INTERPRETER_SOURCE,
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
