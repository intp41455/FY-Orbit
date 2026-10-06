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
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 受限动词集：静态封闭的枚举（运行时不可扩展）
# ---------------------------------------------------------------------------

NODE_TYPES = (
    # 既有三类（受限画布的起点，保持不变）。
    "input", "transform", "output",
    # A-画布搭建器-01：对标 Dify 节点库的 13 类新增（launch-gate）。
    "llm",                    # 经 provider 网关（runtime/gateway.py）调模型
    "knowledge_retrieval",    # 调 services/knowledge/search.py 现有公开检索
    "question_classifier",    # 问题分类（LLM 兜底，需注入 llm_resolver）
    "parameter_extractor",    # 参数抽取（LLM 兜底，需注入 llm_resolver）
    "iteration",              # 子流程对列表逐项映射（fan-out）
    "loop",                   # 子流程循环直到条件满足或达迭代上界
    "variable_aggregator",    # 多路变量汇聚（first/last non-null）
    "template",               # 独立模板节点（{field} 插值）
    "http_request",           # 标准库 HTTP（urllib，禁 shell；allow_domains 白名单）
    "code",                   # 受控执行：子进程 + 超时 + Python 层禁网络/禁 shell
    "tool",                   # 经 services/tool_registry.py 现有公开接口调工具
    "human_input",            # 挂 services/hitl.py 挂起/恢复语义（DslSuspended）
    "trigger",                # 触发器/入口节点（manual/conversation/schedule/webhook）
)
#: 除既有三类之外的新增节点类型（A-画布搭建器-01 清单口径）。
EXTENDED_NODE_TYPES: tuple[str, ...] = NODE_TYPES[3:]

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

#: A-画布搭建器-05：Chatflow/Workflow 双形态（v2 裁决：双页签基建≠达标，
#: 产品语义必须真落地）。chatflow=会话型（以 conversation 触发器为入口、
#: 每轮对话为一次可挂起/续跑的运行）；workflow=自动化型（manual/schedule/
#: webhook/input 单次运行）。
FLOW_TYPES = ("chatflow", "workflow")
#: 触发器节点的触发种类。
TRIGGER_KINDS = ("manual", "conversation", "schedule", "webhook")
#: ``variable_aggregator`` 的汇聚策略。
VARIABLE_AGGREGATOR_STRATEGIES = ("first_non_null", "last_non_null")
#: ``http_request`` 允许的方法。
HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
#: ``knowledge_retrieval`` 允许的检索模式（对齐 search.py 的 mode 语义）。
KNOWLEDGE_MODES = ("lexical", "vector", "hybrid")
#: ``code`` 节点允许的语言（当前只有 Python；白名单封闭）。
CODE_LANGUAGES = ("python",)
#: ``code`` 节点超时上界（秒）。参数超上界按上界裁剪，防画布作者把超时调成无限。
CODE_TIMEOUT_CAP_SECONDS = 15.0
#: ``iteration`` / ``loop`` 子流程嵌套深度上界（编译期拒绝深爆炸）。
SUBFLOW_MAX_DEPTH = 3
#: ``iteration`` 默认/上界条数：超过即编译错误，防一次性展开把执行器拖死。
ITERATION_DEFAULT_MAX_ITEMS = 100
ITERATION_MAX_ITEMS_CAP = 1000
#: ``loop`` 默认/上界迭代次数：循环必须有终止上界，绝不静默死循环。
LOOP_DEFAULT_MAX_ITERATIONS = 10
LOOP_MAX_ITERATIONS_CAP = 1000
#: 入口节点类型（装配完整性 R1 的可达起点）。
ENTRY_NODE_TYPES = ("input", "trigger")

#: 默认导出文件名（代码导出）。
CODE_EXPORT_FILENAME = "flow_restricted.py"

#: ``code`` 节点子进程引导脚本：在**用户代码执行前**先在子进程内封死网络与
#: shell 通道（A-画布搭建器-01 code / A-Claw安全-01 边界）。
#:
#: 诚实边界（与 runtime/sandbox.py 同一文化）：这是**进程级 best-effort**——
#: 子进程隔离 + 超时杀树 + Python 层拒绝 socket/_socket/subprocess/os.system，
#: **不是操作系统级安全边界**（那需要容器/job object）。所有拒绝都带
#: ``dsl code node sandbox`` 前缀，节点失败信息里可归因。
_CODE_SANDBOX_BOOTSTRAP = r'''
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
'''


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
    result = run_dsl(doc, run_id=f"{node_id}:{label}",
                     **{k: v for k, v in resolvers.items() if v is not None})
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
    allow = params.get("allow_domains")
    if allow:
        host = urllib.parse.urlparse(url).hostname or ""
        allowed = [str(d) for d in allow if isinstance(d, str) and d]
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
        # SSRF 面由 allow_domains 白名单约束（提供时强制）。
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


# -- 节点级跨字段检查（A-画布搭建器-01：非 transform 节点的编译期约束） --------


def _check_subflow_doc(node_id: str, subflow: Any, *, depth: int = 0) -> None:
    """子流程必须是一张**合法 DSL 文档**，且嵌套深度有上界（防编译期深爆炸）。"""
    if not isinstance(subflow, dict):
        raise DslValidationError(
            f"节点 {node_id} 的 subflow 必须是一张 DSL 文档对象")
    try:
        validate_dsl(subflow)
    except DslValidationError as exc:
        raise DslValidationError(f"节点 {node_id} 的 subflow 不合法: {exc}") from exc
    if depth >= SUBFLOW_MAX_DEPTH:
        raise DslValidationError(
            f"节点 {node_id} 的子流程嵌套超过 {SUBFLOW_MAX_DEPTH} 层上界")
    for n in subflow.get("nodes", []):
        if isinstance(n, dict) and n.get("type") in ("iteration", "loop"):
            nested = (n.get("params") or {}).get("subflow")
            if isinstance(nested, dict):
                _check_subflow_doc(f"{node_id}>{n['id']}", nested, depth=depth + 1)


def _check_iteration(node_id: str, params: dict[str, Any]) -> None:
    _check_subflow_doc(node_id, params.get("subflow"))
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


def _check_loop(node_id: str, params: dict[str, Any]) -> None:
    _check_subflow_doc(node_id, params.get("subflow"))
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


def _check_question_classifier(node_id: str, params: dict[str, Any]) -> None:
    classes = params.get("classes")
    if not isinstance(classes, list) or len(classes) < 2 or \
            not all(isinstance(c, str) and c for c in classes):
        raise DslFieldError(
            f"节点 {node_id} 的 question_classifier.classes 必须是至少 2 个"
            "非空字符串（分类器至少要有两类）",
            node_id=node_id, field_path="params.classes")


def _check_parameter_extractor(node_id: str, params: dict[str, Any]) -> None:
    fields = params.get("fields")
    if not isinstance(fields, list) or not fields or not all(
            isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"]
            for f in fields):
        raise DslFieldError(
            f"节点 {node_id} 的 parameter_extractor.fields 必须是非空数组，"
            "每项是带非空 name 的对象",
            node_id=node_id, field_path="params.fields")


#: 非 transform 节点类型的编译期跨字段检查（由 validate_dsl 在参数校验后调用）。
NODE_PARAM_CHECKS: dict[str, Callable[[str, dict[str, Any]], None]] = {
    "iteration": _check_iteration,
    "loop": _check_loop,
    "question_classifier": _check_question_classifier,
    "parameter_extractor": _check_parameter_extractor,
}


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


def _node_type_schema_branches() -> list[dict[str, Any]]:
    """按 ``NODE_PARAMS_SCHEMAS`` 逐类型生成 if/then 分支（单一真源，不手抄）。"""
    return [
        {"if": {"properties": {"type": {"const": node_type}},
                "required": ["type"]},
         "then": {"properties": {"params": schema}}}
        for node_type, schema in NODE_PARAMS_SCHEMAS.items()
    ]


DSL_JSON_SCHEMA: dict[str, Any] = {
    "$id": "find-yourself:dsl-canvas:1",
    "title": "Find Yourself 受限 DSL 画布文档",
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "nodes", "edges"],
    "properties": {
        "version": {"const": "1"},
        # A-画布搭建器-05：Chatflow/Workflow 双形态（可选；缺省 workflow，
        # 与既有文档向后兼容）。
        "flow_type": {"enum": list(FLOW_TYPES)},
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
                    # transform 的 verb 必填 + 按 verb 的 params 约束在前。
                    {"if": {"properties": {"type": {"const": "transform"}},
                            "required": ["type"]},
                     "then": {"required": ["verb"],
                              "properties": {"params": _verb_params_schema()}}},
                    *_node_type_schema_branches(),
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
            # 节点级跨字段检查（iteration/loop 子流程、classifier 类别等）。
            node_check = NODE_PARAM_CHECKS.get(n["type"])
            if node_check is not None:
                node_check(n["id"], params)

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
    canonical: dict[str, Any] = {"version": doc["version"], "nodes": nodes,
                                 "edges": edges}
    # flow_type 参与规范化（它改变触发器/入口语义，摘要必须随它变）。
    if doc.get("flow_type") is not None:
        canonical["flow_type"] = doc["flow_type"]
    return canonical


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
# A-画布搭建器-02：装配完整性 + 触发器/入口语义
# ---------------------------------------------------------------------------


def flow_type_of(doc: Any) -> str:
    """文档的 flow_type（缺省 ``workflow``，与既有文档向后兼容）。"""
    ft = doc.get("flow_type") if isinstance(doc, dict) else None
    return ft if ft in FLOW_TYPES else "workflow"


def _subflow_containers(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """主图中所有 iteration/loop 节点（其 subflow 的合法性已由编译期检查保证）。"""
    return [n for n in doc.get("nodes", [])
            if isinstance(n, dict) and n.get("type") in ("iteration", "loop")]


def validate_assembly(doc: Any, *, flow_type: str | None = None) -> dict[str, Any]:
    """装配完整性校验（**叠加**在结构校验之上，不替代它）。

    与 :func:`validate_dsl`（语法/参数/拓扑）的分工：本函数回答「这张图**能不能
    作为一张可运行的画布**被装配起来」——

    * **R1 入口可达**：至少一个入口节点（``input`` / ``trigger``）；从入口沿边
      必须能到达全部节点（孤儿节点 = 装配不完整）。
    * **R2 触发器纯度**：``trigger`` 节点是入口，**不得有入边**。
    * **R3 出口**：主图至少一个 ``output`` 节点（iteration/loop 的 subflow 除外，
      子流程的输出即其 output/末节点，不在此强制）。
    * **R4 形态语义**（A-画布搭建器-05）：
      - ``chatflow`` —— **必须且只能有一个** ``conversation`` 触发器作为会话入口
        （以对话输入为入口、每轮对话一次可挂起/续跑的运行）；
      - ``workflow`` —— **禁止** ``conversation`` 触发器（自动化单次运行，入口是
        manual/schedule/webhook 触发器或 ``input``）。

    :param flow_type: 显式指定形态（优先于文档内 ``flow_type`` 字段）。
    :returns: ``{"flow_type", "entry_nodes", "reachable", "node_count",
        "conversation_triggers"}``。
    :raises DslValidationError: 任一装配约束不满足。
    """
    plan = compile_dsl(doc)  # 结构/参数/拓扑/环先过——装配只谈「装得上」
    ft = flow_type if flow_type in FLOW_TYPES else flow_type_of(doc)
    nodes = plan.nodes
    edges = doc["edges"]

    triggers = [n for n in doc["nodes"] if n["type"] == "trigger"]
    conversation_triggers = [
        n for n in triggers
        if (n.get("params") or {}).get("kind") == "conversation"]

    # R2 触发器纯度
    for t in triggers:
        if any(e["to"] == t["id"] for e in edges):
            raise DslValidationError(
                f"触发器节点 {t['id']} 是入口，不允许有入边")

    entries = [n["id"] for n in doc["nodes"] if n["type"] in ENTRY_NODE_TYPES]
    if not entries:
        raise DslValidationError(
            "装配不完整：至少需要一个入口节点（input 或 trigger）")

    # R1 可达性（从入口 BFS）
    adjacency: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        adjacency[e["from"]].append(e["to"])
    seen: set[str] = set()
    stack = list(entries)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adjacency.get(cur, []))
    unreachable = sorted(nid for nid in nodes if nid not in seen)
    if unreachable:
        raise DslValidationError(
            f"装配不完整：以下节点从任何入口都不可达: {unreachable}")

    # R3 出口
    if not any(n["type"] == "output" for n in doc["nodes"]):
        raise DslValidationError("装配不完整：主图至少需要一个 output 节点")

    # R4 形态语义
    if ft == "chatflow":
        if len(conversation_triggers) != 1:
            raise DslValidationError(
                "chatflow 必须且只能有一个 conversation 触发器作为会话入口，"
                f"实际有 {len(conversation_triggers)} 个")
    elif conversation_triggers:
        raise DslValidationError(
            "workflow（自动化单次运行）不允许 conversation 触发器；"
            "会话入口请改用 chatflow 形态")

    return {"flow_type": ft, "entry_nodes": entries, "reachable": len(seen),
            "node_count": len(nodes),
            "conversation_triggers": [n["id"] for n in conversation_triggers]}


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
                 llm_resolver: LlmResolver | None = None,
                 knowledge_resolver: KnowledgeResolver | None = None,
                 tool_resolver: ToolResolver | None = None,
                 http_resolver: HttpResolver | None = None,
                 human_input_provider: HumanInputProvider | None = None,
                 execution_id: str | None = None,
                 dsl_digest: str = "") -> Any:
    """执行单个节点。payload 为所有入边数据的合并（None 表示无输入）。

    ``upstreams`` 是全部入边上游的输出列表，供 :verb:`merge` 与
    ``variable_aggregator`` 这类多路节点使用；其余节点只用 ``payload``。
    注入式解析器（llm/knowledge/tool/http/human_input）只被对应新节点类型
    使用，未注入时这些节点**诚实失败**（绝不假装调用成功）。
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
            confirm_decision=confirm_decision, approval_signal=approval_signal))
    if ntype == "loop":
        return _exec_node_loop(node["id"], params, payload, _resolvers_dict(
            agent_resolver=agent_resolver, llm_resolver=llm_resolver,
            knowledge_resolver=knowledge_resolver, tool_resolver=tool_resolver,
            http_resolver=http_resolver, human_input_provider=human_input_provider,
            confirm_decision=confirm_decision, approval_signal=approval_signal))
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
            llm_resolver: LlmResolver | None = None,
            knowledge_resolver: KnowledgeResolver | None = None,
            tool_resolver: ToolResolver | None = None,
            http_resolver: HttpResolver | None = None,
            human_input_provider: HumanInputProvider | None = None,
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

    :raises DslSuspended: 走到 ``confirm`` / ``approval`` / ``human_input`` 且尚无
        人工输入/裁决时。**该异常穿透本函数**（不被转成
        ``RunResult.status="failed"``），上层编排据此建 interrupt 并在人工裁决后
        重开一轮。
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
                                       llm_resolver=llm_resolver,
                                       knowledge_resolver=knowledge_resolver,
                                       tool_resolver=tool_resolver,
                                       http_resolver=http_resolver,
                                       human_input_provider=human_input_provider,
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


# ---------------------------------------------------------------------------
# A-画布搭建器-05/06：flow_type 双形态 + 草稿/发布版本化 + 批量评估
#
# 与 0037（capability_grants）同款设计：ORM 模型放在本模块而**不**导入
# ``db/models.py``，因此 ``0001`` 的 ``Base.metadata.create_all`` 看不到这两张表，
# 迁移 ``0038_dsl_flow_lifecycle`` 是它们唯一的建表者（测试 conftest 的
# create_all 因测试模块 import 本模块而同样能看到）。
# ---------------------------------------------------------------------------

from find_yourself.db.base import Base as _FlowBase  # noqa: E402
from find_yourself.db.types import ID as _FlowID  # noqa: E402
from find_yourself.db.types import TZDateTime as _FlowTZ  # noqa: E402
from find_yourself.db.types import utcnow as _flow_utcnow  # noqa: E402
from sqlalchemy import CheckConstraint as _FlowCheck  # noqa: E402
from sqlalchemy import ForeignKey as _FlowFK  # noqa: E402
from sqlalchemy import Integer as _FlowInt  # noqa: E402
from sqlalchemy import String as _FlowStr  # noqa: E402
from sqlalchemy import UniqueConstraint as _FlowUQ  # noqa: E402
from sqlalchemy.orm import Mapped as _FlowMapped  # noqa: E402
from sqlalchemy.orm import mapped_column as _flow_column  # noqa: E402
from sqlalchemy.types import JSON as _FlowJSON  # noqa: E402

from .errors import NotFound as _FlowNotFound  # noqa: E402
from .errors import ValidationFailed as _FlowValidation  # noqa: E402


class DslFlowRow(_FlowBase):
    """一张画布流程（chatflow/workflow 双形态）+ 草稿/发布双文档。"""

    __tablename__ = "dsl_flows"

    id: _FlowMapped[str] = _flow_column(_FlowID, primary_key=True)
    owner_id: _FlowMapped[str] = _flow_column(_FlowStr(200), index=True)
    name: _FlowMapped[str] = _flow_column(_FlowStr(200))
    #: ``chatflow``（会话型）/ ``workflow``（自动化型）——A-画布搭建器-05。
    flow_type: _FlowMapped[str] = _flow_column(_FlowStr(16), default="workflow")
    draft_doc: _FlowMapped[dict | None] = _flow_column(_FlowJSON, nullable=True)
    published_doc: _FlowMapped[dict | None] = _flow_column(_FlowJSON, nullable=True)
    #: 已发布版本号（从 0 起；发布一次 +1，append-only）。
    published_version: _FlowMapped[int] = _flow_column(_FlowInt, default=0)
    created_at: _FlowMapped[object] = _flow_column(_FlowTZ, default=_flow_utcnow)
    updated_at: _FlowMapped[object] = _flow_column(
        _FlowTZ, default=_flow_utcnow, onupdate=_flow_utcnow)

    __table_args__ = (
        _FlowCheck(f"flow_type IN {FLOW_TYPES}", name="ck_dsl_flow_type"),
    )


class DslFlowVersionRow(_FlowBase):
    """一次发布的历史快照（append-only：发布历史只增不减，回滚不改写历史）。"""

    __tablename__ = "dsl_flow_versions"

    id: _FlowMapped[str] = _flow_column(_FlowID, primary_key=True)
    flow_id: _FlowMapped[str] = _flow_column(
        _FlowFK("dsl_flows.id", ondelete="CASCADE"), index=True)
    version: _FlowMapped[int] = _flow_column(_FlowInt)
    doc: _FlowMapped[dict] = _flow_column(_FlowJSON)
    note: _FlowMapped[str] = _flow_column(_FlowStr(200), default="")
    published_at: _FlowMapped[object] = _flow_column(_FlowTZ, default=_flow_utcnow)

    __table_args__ = (
        _FlowUQ("flow_id", "version", name="uq_dsl_flow_version"),
    )


def _flow_view(row: DslFlowRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_id": row.owner_id,
        "name": row.name,
        "flow_type": row.flow_type,
        "draft_doc": row.draft_doc,
        "published_doc": row.published_doc,
        "published_version": row.published_version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class DslFlowStore:
    """草稿/发布版本化（A-画布搭建器-06）。

    语义：

    * **草稿**（``draft_doc``）随时可改，只要求 ``validate_dsl`` 合法
      （半成品画布允许暂存）；
    * **发布** = 草稿通过 :func:`validate_assembly`（装配完整性 + 形态语义）后
      快照为 ``published_doc``，版本号 +1，并写入版本表；
    * **回滚** = 把历史版本文档取回**草稿**（可切换回滚）；发布历史不被改写，
      回滚后再发布即生成新版本（版本线单调递增）。
    """

    def __init__(self, session: Any):
        self.s = session

    # -- 内部 ---------------------------------------------------------------
    @staticmethod
    def _actor_identity(actor: Any) -> str:
        return actor.owner_id or actor.service_id or ""

    def _own_flow(self, actor: Any, flow_id: str) -> DslFlowRow:
        actor.require_authenticated()
        row = self.s.get(DslFlowRow, flow_id)
        if row is None or row.owner_id != self._actor_identity(actor):
            # 属主外一律 NotFound：不泄漏「存在但不属于你」。
            raise _FlowNotFound("dsl_flow", flow_id or "")
        return row

    @staticmethod
    def _check_doc_matches_flow_type(doc: dict[str, Any], flow_type: str) -> None:
        doc_ft = doc.get("flow_type")
        if doc_ft is not None and doc_ft != flow_type:
            raise _FlowValidation(
                f"文档 flow_type={doc_ft!r} 与流程记录 {flow_type!r} 不一致；"
                "请在保存草稿时显式切换 flow_type")

    # -- API ----------------------------------------------------------------
    def create_flow(self, actor: Any, *, name: str, flow_type: str,
                    doc: dict[str, Any] | None = None) -> dict[str, Any]:
        actor.require_authenticated()
        if flow_type not in FLOW_TYPES:
            raise _FlowValidation(
                f"flow_type 必须是 {FLOW_TYPES}，实际是 {flow_type!r}")
        if not isinstance(name, str) or not name.strip():
            raise _FlowValidation("流程名称不能为空")
        if doc is not None:
            validate_dsl(doc)
            self._check_doc_matches_flow_type(doc, flow_type)
        row = DslFlowRow(
            id=f"flow-{uuid.uuid4().hex[:12]}",
            owner_id=actor.owner_id or actor.service_id,
            name=name.strip(), flow_type=flow_type,
            draft_doc=canonical_dsl(doc) if doc is not None else None,
            published_version=0,
        )
        self.s.add(row)
        self.s.flush()
        return _flow_view(row)

    def get_flow(self, actor: Any, flow_id: str) -> dict[str, Any]:
        return _flow_view(self._own_flow(actor, flow_id))

    def list_flows(self, actor: Any) -> list[dict[str, Any]]:
        actor.require_authenticated()
        rows = self.s.query(DslFlowRow).filter(
            DslFlowRow.owner_id == (actor.owner_id or actor.service_id)
        ).order_by(DslFlowRow.updated_at.desc()).all()
        return [_flow_view(r) for r in rows]

    def save_draft(self, actor: Any, flow_id: str, doc: dict[str, Any], *,
                   flow_type: str | None = None) -> dict[str, Any]:
        """保存草稿；``flow_type`` 显式传入时**切换形态**（05：两形态可切换）。"""
        row = self._own_flow(actor, flow_id)
        if flow_type is not None:
            if flow_type not in FLOW_TYPES:
                raise _FlowValidation(
                    f"flow_type 必须是 {FLOW_TYPES}，实际是 {flow_type!r}")
            row.flow_type = flow_type
        validate_dsl(doc)
        self._check_doc_matches_flow_type(doc, row.flow_type)
        row.draft_doc = canonical_dsl(doc)
        self.s.flush()
        return _flow_view(row)

    def publish(self, actor: Any, flow_id: str, *, note: str = "") -> dict[str, Any]:
        """草稿 → 发布：装配校验通过后快照 + 版本号 +1 + 版本表快照。"""
        row = self._own_flow(actor, flow_id)
        doc = row.draft_doc
        if not doc:
            raise _FlowValidation("草稿为空：发布前请先保存草稿")
        self._check_doc_matches_flow_type(doc, row.flow_type)
        validate_assembly(doc, flow_type=row.flow_type)  # 装配完整性 + 形态语义
        canonical = canonical_dsl(doc)
        version = row.published_version + 1
        row.published_doc = canonical
        row.published_version = version
        self.s.add(DslFlowVersionRow(
            id=f"flv-{uuid.uuid4().hex[:12]}",
            flow_id=row.id, version=version, doc=canonical,
            note=(note or "")[:200],
        ))
        self.s.flush()
        return _flow_view(row)

    def list_versions(self, actor: Any, flow_id: str) -> list[dict[str, Any]]:
        row = self._own_flow(actor, flow_id)
        snaps = self.s.query(DslFlowVersionRow).filter(
            DslFlowVersionRow.flow_id == row.id
        ).order_by(DslFlowVersionRow.version.desc()).all()
        return [{"version": s.version, "note": s.note,
                 "published_at": s.published_at.isoformat()
                 if s.published_at else None,
                 "flow_type": (s.doc or {}).get("flow_type", row.flow_type)}
                for s in snaps]

    def get_version(self, actor: Any, flow_id: str, version: int) -> dict[str, Any]:
        row = self._own_flow(actor, flow_id)
        snap = self.s.query(DslFlowVersionRow).filter(
            DslFlowVersionRow.flow_id == row.id,
            DslFlowVersionRow.version == version,
        ).one_or_none()
        if snap is None:
            raise _FlowNotFound("dsl_flow_version", f"{flow_id}@{version}")
        return {"version": snap.version, "note": snap.note, "doc": snap.doc}

    def rollback(self, actor: Any, flow_id: str, version: int) -> dict[str, Any]:
        """回滚：把历史版本取回**草稿**（发布历史不动；再发布即生成新版本）。"""
        row = self._own_flow(actor, flow_id)
        snap = self.s.query(DslFlowVersionRow).filter(
            DslFlowVersionRow.flow_id == row.id,
            DslFlowVersionRow.version == version,
        ).one_or_none()
        if snap is None:
            raise _FlowNotFound("dsl_flow_version", f"{flow_id}@{version}")
        row.draft_doc = snap.doc
        self.s.flush()
        view = _flow_view(row)
        view["rolled_back_to"] = version
        return view


# -- 批量测试集评估（A-画布搭建器-06：接线 runtime/evaluation.UnifiedEvaluator）


def make_flow_runner(doc: dict[str, Any],
                     **resolver_kwargs: Any) -> Callable[[str], dict[str, Any]]:
    """把「一行测试输入」接到一张已发布画布：覆盖入口节点的值后跑一轮。

    入口选择：chatflow 优先 ``conversation`` 触发器（对话输入即行文本）；
    否则取第一个 ``trigger`` / ``input``。运行失败（节点错误）按异常上抛，
    由评估器如实记为该用例失败——绝不静默算 PASS。
    """
    canonical = canonical_dsl(doc)
    doc_nodes = canonical["nodes"]
    entry: dict[str, Any] | None = None
    for n in doc_nodes:
        if n["type"] == "trigger" and \
                (n.get("params") or {}).get("kind") == "conversation":
            entry = n
            break
    if entry is None:
        for n in doc_nodes:
            if n["type"] in ENTRY_NODE_TYPES:
                entry = n
                break
    if entry is None:
        raise DslValidationError("批量评估需要画布有入口节点（trigger 或 input）")

    def run(prompt: str) -> dict[str, Any]:
        d = json.loads(json.dumps(canonical, ensure_ascii=False))
        target = next(n for n in d["nodes"] if n["id"] == entry["id"])
        params = dict(target.get("params") or {})
        if target["type"] == "trigger":
            params["config"] = {**(params.get("config") or {}), "value": prompt}
        else:
            params["value"] = prompt
        target["params"] = params
        result = run_dsl(d, **resolver_kwargs)
        if result.status == "failed":
            raise DslValidationError(result.error or "flow run failed")
        return {"status": result.status, "output": result.output}

    return run


def evaluate_flow_batch(doc: dict[str, Any], csv_text: str, *,
                        runs_per_case: int = 1,
                        runner_kwargs: dict[str, Any] | None = None,
                        evaluator: Any | None = None) -> dict[str, Any]:
    """CSV 批量评估：每行一个用例，跑 :func:`make_flow_runner` 并汇总指标。

    惰性导入 :mod:`find_yourself.runtime.evaluation`（评测器依赖较重，且
    :class:`UnifiedEvaluator` 此前无人 import——本函数是它的产品接线点）。
    """
    from ..runtime.evaluation import UnifiedEvaluator, parse_eval_csv

    cases = parse_eval_csv(csv_text)
    if not cases:
        raise _FlowValidation("CSV 未解析出任何用例（表头需含 input 列）")
    runner = make_flow_runner(doc, **(runner_kwargs or {}))
    return (evaluator or UnifiedEvaluator()).evaluate_flow_batch(
        cases, runner, runs_per_case=runs_per_case)
