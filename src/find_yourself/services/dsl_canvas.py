"""受限 DSL 画布：动词集注册表、编译器与确定性执行引擎（工单 P1-18 / 需求 5）。

实现蓝本（claw-dialogue-extraction §1.2/§1.3）：
* 模型/视图分离 —— DSL 文档（model）只包含节点/边/参数；坐标、层级等视图
  状态属于 layout，绝不进入 DSL。
* Compiler —— 拓扑排序 + 环检测；条件分支按「节点完成后按后继边条件入队」
  动态展开（queue 驱动的调度，而非一次性静态计划）。
* Executor —— 受限动词集（input / transform: 见 ``VERB_REGISTRY`` / output），
  全部确定性执行，无外部 LLM 依赖；每个节点的输入输出都记入运行日志。

动词集（需求 5①）
-----------------
:data:`VERB_REGISTRY` 是**唯一真源**：每个动词同时给出「名字 + 参数契约
（JSON Schema + 编译期检查）+ 执行实现（确定性纯函数）」，三样缺一不可。
分类：

==========  ==========================  ============================================
类别        动词                语义
==========  ==========================  ============================================
数据变换      ``map``                    逐项设字段 / 转大写 / 转小写
数据变换      ``filter``                 按字段比较筛选
数据变换      ``template``               ``{field}`` 插值成文本
流程控制      ``branch``                 显式条件分支，产出 ``{branch, value}``
流程控制      ``aggregate``              循环聚合（count/sum/min/max/avg/join/…）
流程控制      ``merge``                  并行汇聚（多路入边收敛成一路）
Agent      ``agent``                   调用已注册 Agent/工具（需注入解析器）
人机协作      ``confirm``                挂起等人确认（抛 :class:`DslSuspended`）
治理        ``approval``               委托 proposal.py 发起治理操作后挂起等裁决（ADR-04）
输出        ``artifact``                产出带名字的产物信封
==========  ==========================  ============================================

**封闭性**（ADR-003）：动词集由代码静态定义，运行时不可扩展；白名单外的动词
在 :func:`validate_dsl` 期即抛 :class:`DslValidationError`，绝不静默忽略。

**诚实边界**：``agent`` 需要调用方显式注入 ``agent_resolver``；``confirm`` 在尚无
人工裁决时抛 :class:`DslSuspended`（控制流信号，非失败），由上层建 HITL interrupt
并在裁决后带 ``confirm_decision`` 重开一轮。裁决**只能**来自受信任存储，DSL 文档
自身不能声明「已批准」。

执行记录只落内存 + 归档目录（env ``FY_DSL_ARCHIVE_DIR``），不写数据库迁移。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 受限动词集：静态封闭的枚举（运行时不可扩展）
# ---------------------------------------------------------------------------

NODE_TYPES = ("input", "transform", "output")
INPUT_KINDS = ("literal", "text_lines")
OUTPUT_FORMATS = ("json", "text")
MAP_OPS = ("set", "upper", "lower")
FILTER_OPS = ("eq", "ne", "gt", "lt", "contains")
CONDITION_OPS = FILTER_OPS
#: ``aggregate`` 的聚合算子。
AGGREGATE_OPS = ("count", "sum", "min", "max", "avg", "first", "last", "join", "unique")
#: ``aggregate`` 中需要数值型field 的算子。
NUMERIC_AGGREGATE_OPS = ("sum", "min", "max", "avg")
#: ``merge`` 的汇聚策略。
MERGE_OPS = ("concat", "first", "last")

#: 默认导出文件名（代码导出）。
CODE_EXPORT_FILENAME = "flow_restricted.py"


class DslValidationError(ValueError):
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


class DslSuspended(Exception):
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


# -- 编译期跨字段检查 --------------------------------------------------------


def _check_map(node_id: str, params: dict[str, Any]) -> None:
    if params["op"] == "set" and not isinstance(params.get("field"), str):
        raise DslValidationError(f"节点 {node_id} map.set 需要 field")


def _check_aggregate(node_id: str, params: dict[str, Any]) -> None:
    if params["op"] in NUMERIC_AGGREGATE_OPS and not isinstance(params.get("field"), str):
        raise DslValidationError(
            f"节点 {node_id} aggregate.op={params['op']} 需要 field")


def allowed_approval_ops() -> frozenset[str]:
    """``approval`` 动词允许的 ``op`` 全集：``IMMEDIATE_OPS ∪ EXTERNAL_OPS``。

    **唯一真源是** :mod:`find_yourself.services.proposal` —— 本模块绝不另抄一份
    白名单：抄一份就等于给契约 §6 的治理入口开了一条旁路。惰性导入（而非模块级）
    是因为 proposal 依赖 SQLAlchemy 模型，模块级导入会把重量级依赖带进 DSL 编译器，
    且可能成环。
    """
    from .proposal import EXTERNAL_OPS, IMMEDIATE_OPS  # 惰性：避免重量级/循环依赖
    return frozenset(IMMEDIATE_OPS) | frozenset(EXTERNAL_OPS)


def _check_approval(node_id: str, params: dict[str, Any]) -> None:
    """编译期安全边界：``op`` 必须落在 proposal 的治理白名单内。

    ``op`` 不在 ``IMMEDIATE_OPS ∪ EXTERNAL_OPS`` 内即**编译错误**（不是运行期
    才失败），错误信息点名具体 op。DSL 只允许「发起一次既有治理操作」，绝不允许
    凭空发明一个可以绕过 :mod:`~find_yourself.services.proposal` 的操作。
    """
    op = params.get("op")
    if op not in allowed_approval_ops():
        raise DslFieldError(
            f"节点 {node_id} 的 approval.op={op!r} 不被支持；"
            "op 必须是 services/proposal.py 治理白名单"
            "（IMMEDIATE_OPS ∪ EXTERNAL_OPS）的成员",
            node_id=node_id, field_path="params.op")


def _obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """构造 params 的 JSON Schema 片段（封闭：additionalProperties=False）。"""
    return {"type": "object", "additionalProperties": False,
            "required": list(required), "properties": properties}


#: 受限动词注册表（顺序即 ``TRANSFORM_VERBS`` 的顺序）。**唯一真源**。
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
        # 非空字符串，真正的安全边界由 _check_approval 惰性读取 proposal 白名单执行，
        # 因此 DSL_JSON_SCHEMA 与 IR 派生模型无需在导入期依赖 SQLAlchemy。
        params_schema=_obj({
            "op": {"type": "string", "minLength": 1},
            "target_id": {"type": "string", "minLength": 1},
            "reason": {"type": "string"},
            "rollback": {"type": "string"},
        }, required=("op",)),
        execute=_exec_approval, check=_check_approval,
    ),
}

#: ``transform`` 节点的合法动词白名单（由注册表派生，**封闭**）。
TRANSFORM_VERBS: tuple[str, ...] = tuple(VERB_REGISTRY)

#: ``input`` / ``output`` 节点的 params 契约（同样封闭）。
NODE_PARAMS_SCHEMAS: dict[str, dict[str, Any]] = {
    "input": _obj({"kind": {"enum": list(INPUT_KINDS)}, "value": {}}),
    "output": _obj({"format": {"enum": list(OUTPUT_FORMATS)}}),
}


def verb_catalog() -> list[dict[str, Any]]:
    """动词集元数据（供 ``GET /api/dsl-canvas/schema`` 与前端节点面板使用）。"""
    return [
        {
            "name": spec.name,
            "category": spec.category,
            "summary": spec.summary,
            "params_schema": spec.params_schema,
            # 诚实标记：未接入的能力在执行期必定失败，不在这里假装可用。
            "executable": spec.execute is not _exec_confirm,
        }
        for spec in VERB_REGISTRY.values()
    ]


def _verb_params_schema() -> dict[str, Any]:
    """transform 节点的 params schema：按 verb 逐个约束（由注册表派生）。"""
    return {
        "allOf": [
            {"if": {"properties": {"verb": {"const": name}}, "required": ["verb"]},
             "then": {"properties": {"params": spec.params_schema}}}
            for name, spec in VERB_REGISTRY.items()
        ]
    }


DSL_JSON_SCHEMA: dict[str, Any] = {
    "$id": "find-yourself:dsl-canvas:1",
    "title": "Find Yourself 受限 DSL 画布文档",
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "nodes", "edges"],
    "properties": {
        "version": {"const": "1"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 64,
                           "pattern": "^[A-Za-z0-9_-]+$"},
                    "type": {"enum": list(NODE_TYPES)},
                    # 仅 transform 节点使用；白名单外取值会被 validate_dsl 拒绝。
                    "verb": {"enum": list(TRANSFORM_VERBS)},
                    "params": {"type": "object"},
                },
                "allOf": [
                    {"if": {"properties": {"type": {"const": "transform"}},
                            "required": ["type"]},
                     "then": {"required": ["verb"],
                              "properties": {"params": _verb_params_schema()}}},
                    {"if": {"properties": {"type": {"const": "input"}},
                            "required": ["type"]},
                     "then": {"properties": {"params": NODE_PARAMS_SCHEMAS["input"]}}},
                    {"if": {"properties": {"type": {"const": "output"}},
                            "required": ["type"]},
                     "then": {"properties": {"params": NODE_PARAMS_SCHEMAS["output"]}}},
                ],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "to"],
                "additionalProperties": False,
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    # 可选边条件：条件不满足则后继节点不入队（动态展开）
                    "condition": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["field", "op", "value"],
                        "properties": {
                            "field": {"type": "string"},
                            "op": {"enum": list(CONDITION_OPS)},
                            "value": {},
                        },
                    },
                },
            },
        },
    },
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


def validate_dsl(doc: Any) -> dict[str, Any]:
    """结构校验（手写轻量校验，覆盖 JSON Schema 中的约束）。"""
    if not isinstance(doc, dict):
        raise DslValidationError("DSL 文档必须是 JSON 对象")
    if doc.get("version") != "1":
        raise DslValidationError("version 必须为 \"1\"")
    nodes = doc.get("nodes")
    edges = doc.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise DslValidationError("nodes 必须是非空数组")
    if not isinstance(edges, list):
        raise DslValidationError("edges 必须是数组")

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


def canonical_dsl(doc: Any) -> dict[str, Any]:
    """规范化 DSL 文档（省略空params / 非transform 的 verb / 空 condition）。

    与既有 :func:`~find_yourself.services.workflow_gen.graph_to_dsl` 的省略规则
    一致，因此规范化结果可以直接当DSL 文档使用（等价于画布的语义层）。
    """
    validate_dsl(doc)
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
    return {"version": doc["version"], "nodes": nodes, "edges": edges}


def dsl_digest(doc: Any) -> str:
    """DSL 文档的规范化摘要（SHA-256 前16 位）。

    基于 :func:`canonical_dsl`（省略规则归一），因此「语义等价的两张画布摘要相同」。
    用途：人工裁决的 TOCTOU 防护——人看的是某个版本的图，裁决时必须确认还是那一版
    （对齐 dev-hitl 的 ``expected_version`` 乐观锁）。
    """
    canonical = canonical_dsl(doc)
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compile_dsl(doc: Any) -> CompiledPlan:
    """校验 + 拓扑排序（Kahn）+ 环检测。

    在既有结构校验之后追加 **ADR-02 的类型化 IR 闸门**（:func:`dsl_ir.assert_ir_valid`）：
    节点/边的强类型契约（``extra="forbid"``）在执行之前就把类型错误拦下，且错误能
    定位到「哪张画布的哪个节点、哪个字段」。既有合法文档全部照常通过。
    """
    validate_dsl(doc)
    # 局部导入：dsl_ir 反向依赖本模块的 VERB_REGISTRY，模块级导入会成环。
    from .dsl_ir import assert_ir_valid
    assert_ir_valid(doc)
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


# ---------------------------------------------------------------------------
# 确定性动词执行器
# ---------------------------------------------------------------------------
# 动词实现与查表工具（_lookup/_compare/_interpolate）见文件上部VERB_REGISTRY
# 区域：注册表必须在实现之后、执行之前，故辅助函数随注册表一起前置。


def execute_node(node: dict[str, Any], payload: Any, *,
                 upstreams: list[Any] | None = None,
                 agent_resolver: AgentResolver | None = None,
                 confirm_decision: ConfirmDecisionReader | None = None,
                 approval_signal: ApprovalSignal | None = None,
                 execution_id: str | None = None,
                 dsl_digest: str = "") -> Any:
    """执行单个节点。payload 为所有入边数据的合并（None 表示无输入）。

    ``upstreams`` 是全部入边上游的输出列表，供 :verb:`merge` 这类多路动词使用；
    其余动词只用``payload``，语义与既有实现完全一致。
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

    raise DslValidationError(f"未知节点类型: {ntype}")


# ---------------------------------------------------------------------------
# Dispatcher：拓扑波次 + 按边条件入队的动态展开
# ---------------------------------------------------------------------------


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


def run_dsl(doc: dict[str, Any], *, run_id: str | None = None,
            agent_resolver: AgentResolver | None = None,
            confirm_decision: ConfirmDecisionReader | None = None,
            approval_signal: ApprovalSignal | None = None,
            execution_id: str | None = None) -> RunResult:
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
    :param execution_id: **由调用方显式传入**的稳定执行标识（不要用 ``run_id``：
        它每次运行重新生成且只在内存里，重启后恢复链会断）。它进入
        :attr:`DslSuspended.checkpoint`，供上层把 HITL interrupt 与本执行关联。

    :raises DslSuspended: 走到 ``confirm`` 且尚无人工裁决时。**该异常穿透本函数**
        （不被转成 ``RunResult.status="failed"``），上层编排据此建 interrupt 并在
        人工裁决后带 ``confirm_decision`` 重开一轮。
    """
    plan = compile_dsl(doc)
    rid = run_id or f"dsl-{uuid.uuid4().hex[:12]}"
    result = RunResult(run_id=rid, status="succeeded", doc=doc,
                       execution_id=execution_id)
    # 供confirm 裁决读取器自校验「人所见即所批」的文档摘要。
    digest = dsl_digest(doc)

    outputs: dict[str, Any] = {}
    # node -> 剩余未满足的入边数（入队闸门）
    pending = dict(plan.indegree)
    # node -> 已有一 Condition 通过（用于 skipped 判定）
    triggered: dict[str, bool] = {nid: False for nid in plan.nodes}
    queue: list[str] = [nid for nid in plan.order if pending[nid] == 0]

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
                queue.sort(key=lambda nid: plan.order.index(nid))  # 稳定按拓扑序
                nid = queue.pop(0)
                node = plan.nodes[nid]
                log = NodeLog(node_id=nid, node_type=node["type"],
                              verb=node.get("verb"), status="succeeded", started_at=now())
                result.logs.append(log)
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
                                       execution_id=execution_id,
                                       dsl_digest=digest)
                    outputs[nid] = out
                    log.output = out
                    log.finished_at = now()
                except DslSuspended:
                    # 控制流信号，**不是节点失败**：必须穿透，否则挂起会静默退化成
                    # 「节点 failed + 分支终止」，上层永远等不到 interrupt。
                    # 本节点的日志如实记为 suspended（已完成的上游日志保留在result 里，
                    # 供上层看到「跑到哪儿了」）。
                    log.status = "suspended"
                    log.finished_at = now()
                    result.status = "suspended"
                    result.error = None
                    raise
                except Exception as exc:  # 节点失败 → 该分支终止
                    log.status = "failed"
                    log.error = str(exc)
                    log.finished_at = now()
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
            # 跳过传播：未被触发的节点按拓扑序标记 skipped，并把其出边按
            # 「条件不满足」向下传播；若某下游已被其他分支触发且计数归零，
            # 则重新进入主循环执行（requeue）。
            requeued = False
            for nid in plan.order:
                if _latest_log(result, nid) is not None:
                    continue
                node = plan.nodes[nid]
                result.logs.append(NodeLog(
                    node_id=nid, node_type=node["type"], verb=node.get("verb"),
                    status="skipped", started_at=now(), finished_at=now()))
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
    return result


def _latest_log(result: RunResult, node_id: str) -> NodeLog | None:
    """按节点 id 取最近一条日志（无则 None）。"""
    for log in reversed(result.logs):
        if log.node_id == node_id:
            return log
    return None


# ---------------------------------------------------------------------------
# 运行记录存档：内存 + 可选目录归档（不写数据库迁移）
# ---------------------------------------------------------------------------


class DslRunStore:
    """run_id -> RunResult；可选把完整结果 JSON 落到归档目录。"""

    def __init__(self, archive_dir: str | os.PathLike[str] | None = None,
                 max_in_memory: int = 200):
        self._runs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._max = max_in_memory
        self.archive_dir = Path(archive_dir) if archive_dir else None
        if self.archive_dir:
            self.archive_dir.mkdir(parents=True, exist_ok=True)

    def save(self, result: RunResult) -> dict[str, Any]:
        payload = result.to_dict()
        self._runs[result.run_id] = payload
        self._order.append(result.run_id)
        if len(self._order) > self._max:
            self._runs.pop(self._order.pop(0), None)
        if self.archive_dir:
            path = self.archive_dir / f"run-{result.run_id}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return payload

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._runs.get(run_id)

    def list_ids(self) -> list[str]:
        return list(self._order)
