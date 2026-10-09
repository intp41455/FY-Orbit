"""代码优先 SDK（A-三重模式-02 / A-代码SDK-01 / A-代码SDK-02 / A-三重模式-04）。

定位与同源原则
--------------
小白（画布）/ 技术（代码）/ 企业（治理）三重模式的**同一 IR 驱动**落点：
本模块不发明第二套图结构、第二套动词表，全部**只读消费**既有单一真源——

* 图文档先过 :func:`find_yourself.services.dsl_ir.assert_ir_valid`
  （强类型 IR 闸门，``extra="forbid"``），再经 :func:`run_dsl` 执行——
  与画布走的是同一条编译执行路径；
* 动词目录经 :func:`verb_catalog` 只读读出 ``VERB_REGISTRY``；
* 自定义节点**绝不**向 ``VERB_REGISTRY`` 注入新动词（ADR-003 封闭性），
  而是映射到平台唯一的扩展点 ``agent`` 动词 + 注入解析器（见下文契约节）。

四块能力
--------
1. **代码定义图**：:class:`GraphBuilder` 在代码里装配画布同构 DSL 文档，
   与画布可互相导入（``export_dsl_code`` / ``parse_dsl_code``）。
2. **状态 schema**：:class:`StateSchema` / :class:`StateField` 声明变量与
   类型，诊断复用 :class:`~find_yourself.services.dsl_ir.Diagnostic`
   （同一诊断模型，前端一套渲染）。
3. **自定义 reducer**：:func:`register_reducer` + 节点级归约——节点成功后
   按执行日志顺序把输出折叠进状态（诚实边界：折叠发生在 run 之后，按日志
   顺序重放；不是执行器内的流式钩子，那需要改 ``run_dsl``，超出本包边界）。
4. **自定义节点契约**：:func:`register_custom_node` —— 第三方节点在
   SDK 层注册、经 ``agent`` 动词注入执行，参数契约用与 ``VERB_REGISTRY``
   同一套 JSON Schema 片段约定（只读兼容对接，文档
   ``docs/dsl-自定义节点契约-2026-10-06.md``）。

三重模式框架
------------
:class:`AuthoringMode`（beginner 小白 / technical 技术 / enterprise 企业）
+ :func:`register_mode_entry` 入口注册 + :func:`mode_overview` 概览
（HTTP 骨架见 ``api/routes/dsl_debug.py::GET /api/dsl/modes``）。

全确定性：无 LLM 参与；所有注册表是进程内字典，不落库、不写迁移。
"""

from __future__ import annotations

import enum
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .dsl_canvas import (
    AgentResolver,
    DslValidationError,
    RunResult,
    run_dsl,
    verb_catalog,
)
from .dsl_ir import Diagnostic, assert_ir_valid, validate_ir

__all__ = [
    "AuthoringMode", "ModeEntry", "register_mode_entry", "mode_entries",
    "mode_overview",
    "ModeTemplate", "MODE_TEMPLATES", "DEFAULT_TEMPLATE_IDS",
    "mode_template_catalog", "build_mode_template", "default_template_for",
    "StateField", "StateSchema",
    "Reducer", "register_reducer", "get_reducer", "REDUCER_REGISTRY",
    "GraphBuilder", "CodeWorkflow", "SdkRunResult",
    "CustomNodeSpec", "CUSTOM_NODE_REGISTRY", "register_custom_node",
    "custom_node_catalog", "CUSTOM_AGENT_PREFIX",
    "DslSdkError",
]


class DslSdkError(ValueError):
    """SDK 层的注册/契约错误（不与 DSL 文档错误混淆）。"""


# ---------------------------------------------------------------------------
# 三重模式注册框架（A-三重模式-04 最小落地）
# ---------------------------------------------------------------------------


class AuthoringMode(str, enum.Enum):
    """三重模式的创作入口枚举（同一 IR，三种入口）。"""

    BEGINNER = "beginner"      # 小白：模板 / 画布搭建
    TECHNICAL = "technical"    # 技术：代码优先 SDK
    ENTERPRISE = "enterprise"  # 企业：治理接入（approval / proposal）


#: 模式的展示名（API 骨架直接消费）。
MODE_LABELS: dict[AuthoringMode, str] = {
    AuthoringMode.BEGINNER: "小白（画布模板）",
    AuthoringMode.TECHNICAL: "技术（代码 SDK）",
    AuthoringMode.ENTERPRISE: "企业（治理接入）",
}

#: 入口工厂的懒加载签名：``() -> CodeWorkflow``。
EntryFactory = Callable[[], "CodeWorkflow"]


@dataclass(frozen=True)
class ModeEntry:
    """一个模式入口（注册即生效；``factory`` 为空表示纯骨架入口）。"""

    mode: AuthoringMode
    entry_id: str
    label: str
    description: str = ""
    factory: EntryFactory | None = None
    builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value, "entry_id": self.entry_id,
            "label": self.label, "description": self.description,
            "builtin": self.builtin,
        }


_ENTRY_ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_MODE_ENTRIES: dict[AuthoringMode, dict[str, ModeEntry]] = {
    mode: {} for mode in AuthoringMode
}


def register_mode_entry(mode: AuthoringMode, entry_id: str, *, label: str,
                        description: str = "",
                        factory: EntryFactory | None = None,
                        builtin: bool = False) -> ModeEntry:
    """注册一个模式入口；``entry_id`` 重复即明确报错（绝不静默覆盖）。"""
    if not isinstance(mode, AuthoringMode):
        raise DslSdkError(f"mode 必须是 AuthoringMode 成员，实际是 {mode!r}")
    if not _ENTRY_ID_RE.fullmatch(entry_id or ""):
        raise DslSdkError(
            f"entry_id 不合法: {entry_id!r}（需匹配 {_ENTRY_ID_RE.pattern}）")
    if not label:
        raise DslSdkError("label 不能为空")
    bucket = _MODE_ENTRIES[mode]
    if entry_id in bucket:
        raise DslSdkError(
            f"模式 {mode.value} 的入口 {entry_id!r} 已注册，拒绝覆盖")
    entry = ModeEntry(mode=mode, entry_id=entry_id, label=label,
                      description=description, factory=factory,
                      builtin=builtin)
    bucket[entry_id] = entry
    return entry


def mode_entries(mode: AuthoringMode | None = None) -> list[ModeEntry]:
    """已注册入口（稳定排序；``mode=None`` 返回全部三种模式）。"""
    modes = [mode] if mode is not None else list(AuthoringMode)
    out: list[ModeEntry] = []
    for m in modes:
        out.extend(_MODE_ENTRIES[m][k] for k in sorted(_MODE_ENTRIES[m]))
    return out


def mode_overview() -> list[dict[str, Any]]:
    """三模式概览（供 ``GET /api/dsl/modes``）。

    A-三重模式-01/04 真落地：除入口位之外，每个模式还给出**可运行的起手模板**
    （``templates`` + ``default_template``）——前端「小白入口」据此做
    「模板起手 → 装配 → 运行」，不必在前端另内置一份假模板。
    """
    return [
        {
            "mode": mode.value, "label": MODE_LABELS[mode],
            "entries": [entry.to_dict()
                        for entry in mode_entries(mode)],
            "templates": mode_template_catalog(mode),
            "default_template": DEFAULT_TEMPLATE_IDS[mode],
        }
        for mode in AuthoringMode
    ]


# ---------------------------------------------------------------------------
# A-三重模式-01/04：三入口**真落地**——每个入口都能产出可运行的同源 IR
# ---------------------------------------------------------------------------
#
# 骨架期三个 builtin 入口的 ``factory=None``：入口位在、点进去没东西，小白模式
# 因此无法实际使用。真落地后每个入口挂一个**默认工厂**，产出同一套 IR——
# ``{"version": "1", "nodes": [...], "edges": [...]}``，且全部过
# :func:`assert_ir_valid` 强类型闸门（与画布共用同一条编译执行路径）。
# 判据「同源」由此成立：三条入口产出的文档在同一
# ``POST /api/dsl-canvas/validate-ir`` 下判定一致。
#
# 模板只使用**确定性、零外部依赖**的节点与受限动词，保证「模板起手 → 装配 →
# 运行」在小白模式下一定跑得通（需要注入解析器的 llm / tool / http 等节点不
# 进入起手模板——它们会诚实失败，不该做新手的第一屏）。


@dataclass(frozen=True)
class ModeTemplate:
    """一个模式的起手模板（模板 = 一份可运行的 :class:`CodeWorkflow`）。"""

    template_id: str
    mode: AuthoringMode
    label: str
    description: str
    build: Callable[[], "CodeWorkflow"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id, "mode": self.mode.value,
            "label": self.label, "description": self.description,
        }


def _starter_pipeline(b: "GraphBuilder") -> None:
    """三入口**共用**的那张图（不含末端汇入 ``out`` 的那条边）。

    同一段装配被小白（模板起手）/ 技术（代码 SDK）/ 企业（治理画布，在 ``label``
    与 ``out`` 之间插一个 ``approval`` 节点）三条入口复用——同源不是文档里的
    一句口号，而是真的只有一份图定义。

    末端汇入边由调用方补：企业模式要把它换成 `label → govern → out`，
    否则 ``out`` 会同时吃到治理前与治理后两路输入。
    """
    b.input_node("src", kind="literal",
                 value=[{"text": "甲"}, {"text": ""}, {"text": "丙"}])
    b.transform_node("drop_empty", "filter", field="text", op="ne", value="")
    b.transform_node("label", "map", op="set", field="line", value="行：{text}")
    b.edge("src", "drop_empty")
    b.edge("drop_empty", "label")


def _beginner_starter_workflow() -> "CodeWorkflow":
    """小白起手模板：就是那只三基础节点的小流水线，确定性、零依赖。"""
    b = GraphBuilder()
    _starter_pipeline(b)
    b.output_node("out", format="json")
    b.edge("label", "out")
    return b.build()


def _technical_starter_workflow() -> "CodeWorkflow":
    """技术模式起手模板：**同一张图**的代码定义 + 状态 schema。

    与小白起手模板产出逐字节相同的 IR（判据「同源」的直接证据）；差别只在
    装配方式（GraphBuilder 代码 vs 画布拖拽）与额外声明的状态变量。
    """
    b = GraphBuilder()
    _starter_pipeline(b)
    b.output_node("out", format="json")
    b.edge("label", "out")
    return b.build(state_schema=StateSchema(fields=(
        StateField(name="rows", type="array", required=True, default=[],
                   description="本轮产出的行（reducer 可折叠进来）"),
    )))


def _enterprise_starter_workflow() -> "CodeWorkflow":
    """企业模式起手模板：同一张图 + 一个 ``approval`` 治理节点。

    ``approval`` 是**挂起**语义（:class:`DslSuspended`），不是失败：节点把
    治理操作委托给 ``services/proposal.py``，等人工裁决后带
    ``approval_signal`` 重开一轮续跑。治理 op 取值受 proposal 白名单约束，
    这里用最轻的 ``memory.upsert``。
    """
    b = GraphBuilder()
    _starter_pipeline(b)
    b.transform_node("govern", "approval", op="memory.upsert",
                     target_id="starter-governed-run",
                     reason="起手模板：产出落库前先走一次治理裁决")
    b.output_node("out", format="json")
    b.edge("label", "govern")
    b.edge("govern", "out")
    return b.build()


#: 起手模板注册表（``template_id`` → :class:`ModeTemplate`；封闭、启动即定）。
MODE_TEMPLATES: dict[str, ModeTemplate] = {
    "beginner-starter": ModeTemplate(
        template_id="beginner-starter", mode=AuthoringMode.BEGINNER,
        label="起手：筛选非空行",
        description="input → filter → map → output，三基础节点，可直接运行",
        build=_beginner_starter_workflow),
    "technical-pipeline": ModeTemplate(
        template_id="technical-pipeline", mode=AuthoringMode.TECHNICAL,
        label="起手：代码装配 + 状态 schema",
        description="GraphBuilder 装配同一张图，附 StateSchema（rows）",
        build=_technical_starter_workflow),
    "enterprise-governed": ModeTemplate(
        template_id="enterprise-governed", mode=AuthoringMode.ENTERPRISE,
        label="起手：治理裁决",
        description="同一张图 + approval(memory.upsert) 节点，挂起等裁决",
        build=_enterprise_starter_workflow),
}

#: 每个模式默认使用的模板 id（入口工厂包装的就是它）。
DEFAULT_TEMPLATE_IDS: dict[AuthoringMode, str] = {
    AuthoringMode.BEGINNER: "beginner-starter",
    AuthoringMode.TECHNICAL: "technical-pipeline",
    AuthoringMode.ENTERPRISE: "enterprise-governed",
}


def mode_template_catalog(mode: AuthoringMode | None = None, *,
                          include_dsl: bool = True) -> list[dict[str, Any]]:
    """起手模板清单（``mode=None`` 返回全部；稳定排序）。

    ``include_dsl=True``（默认）时逐项附上 ``dsl``——**模板真正的 IR 文档**。
    前端「小白入口」据此做「模板起手」：直接把后端产出的图放进画布，前端不另
    建一份同名假模板（那就又是第二套图定义了）。模板本身是确定性纯函数，
    每次现算，不存在被改脏的缓存。
    """
    items = [t for t in MODE_TEMPLATES.values()
             if mode is None or t.mode is mode]
    items.sort(key=lambda t: (t.mode.value, t.template_id))
    out: list[dict[str, Any]] = []
    for t in items:
        entry = t.to_dict()
        if include_dsl:
            entry["dsl"] = t.build().doc
        out.append(entry)
    return out


def build_mode_template(template_id: str) -> "CodeWorkflow":
    """按 ``template_id`` 装配模板（未注册即明确报错，绝不静默回落）。"""
    template = MODE_TEMPLATES.get(template_id)
    if template is None:
        raise DslSdkError(
            f"模板 {template_id!r} 未注册；已注册：{sorted(MODE_TEMPLATES)}")
    return template.build()


def default_template_for(mode: AuthoringMode) -> str:
    """模式的默认模板 id。"""
    return DEFAULT_TEMPLATE_IDS[mode]


# 三入口：入口位 + **真工厂**（factory 就是该模式的默认模板）。
register_mode_entry(AuthoringMode.BEGINNER, "canvas",
                    label="画布搭建器", builtin=True,
                    description="拖拽画布 + 受限动词面板（同源 DSL）",
                    factory=_beginner_starter_workflow)
register_mode_entry(AuthoringMode.TECHNICAL, "code-sdk",
                    label="代码优先 SDK", builtin=True,
                    description="GraphBuilder 代码定义图 / StateSchema / Reducer",
                    factory=_technical_starter_workflow)
register_mode_entry(AuthoringMode.ENTERPRISE, "governed-canvas",
                    label="治理画布", builtin=True,
                    description="approval 动词 + proposal.py 治理裁决接入",
                    factory=_enterprise_starter_workflow)


# ---------------------------------------------------------------------------
# 状态 schema（A-三重模式-02 技术模式：代码定义状态）
# ---------------------------------------------------------------------------

#: 状态字段允许的类型词汇（JSON 同构；诊断/校验都按这套走）。
STATE_FIELD_TYPES = ("string", "number", "boolean", "array", "object", "any")

_STATE_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: not isinstance(v, bool) and isinstance(v, (int, float)),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "any": lambda _v: True,
}


@dataclass(frozen=True)
class StateField:
    """一个状态变量：名字 + 类型 + 可选默认值。

    ``required=True`` 时必须给 ``default``（保证初始状态天然合法——
    「画布从合法状态开始」比「跑了才知道缺字段」诚实）。
    """

    name: str
    type: str
    required: bool = False
    default: Any = None
    description: str = ""


class StateSchema:
    """一组状态变量的封闭 schema（多余字段是违约）。

    诊断复用 :class:`~find_yourself.services.dsl_ir.Diagnostic`
    （``node_id=""``、``field_path="state.<name>"``），与画布 IR 校验
    共用同一诊断模型，前端一套渲染。
    """

    def __init__(self, fields: Sequence[StateField]) -> None:
        if not fields:
            raise DslSdkError("StateSchema 至少需要一个 StateField")
        names: set[str] = set()
        for f in fields:
            if not isinstance(f, StateField):
                raise DslSdkError(f"字段必须是 StateField，实际是 {type(f)!r}")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", f.name or ""):
                raise DslSdkError(f"状态字段名不合法: {f.name!r}")
            if f.name in names:
                raise DslSdkError(f"状态字段名重复: {f.name}")
            names.add(f.name)
            if f.type not in STATE_FIELD_TYPES:
                raise DslSdkError(
                    f"状态字段 {f.name} 的类型 {f.type!r} 必须是 {STATE_FIELD_TYPES}")
            if f.required and f.default is None:
                raise DslSdkError(
                    f"状态字段 {f.name} 是 required，必须给 default"
                    "（保证初始状态合法）")
            if f.default is not None and not _STATE_TYPE_CHECKS[f.type](f.default):
                raise DslSdkError(
                    f"状态字段 {f.name} 的默认值与类型 {f.type} 不匹配")
        self._fields: tuple[StateField, ...] = tuple(fields)

    @property
    def fields(self) -> tuple[StateField, ...]:
        return self._fields

    def field(self, name: str) -> StateField | None:
        return next((f for f in self._fields if f.name == name), None)

    def initial_state(self) -> dict[str, Any]:
        """初始状态：全部字段取 default（required 必有 default，见上）。"""
        return {f.name: f.default for f in self._fields}

    def validate(self, state: Any) -> list[Diagnostic]:
        """校验一个状态快照，返回**全部**诊断（合法时为空，不 fail-fast）。"""
        diags: list[Diagnostic] = []
        if not isinstance(state, dict):
            return [Diagnostic("", "state", "invalid_type",
                               "状态必须是对象（dict）")]
        for f in self._fields:
            path = f"state.{f.name}"
            if f.name not in state:
                if f.required:
                    diags.append(Diagnostic("", path, "missing_field",
                                            f"状态缺少必填字段 {f.name}"))
                continue
            value = state[f.name]
            if not _STATE_TYPE_CHECKS[f.type](value):
                diags.append(Diagnostic(
                    "", path, "invalid_type",
                    f"状态字段 {f.name} 类型必须是 {f.type}，"
                    f"实际是 {type(value).__name__}"))
        known = {f.name for f in self._fields}
        for key in state:
            if key not in known:
                diags.append(Diagnostic("", f"state.{key}", "extra_field",
                                        f"状态不支持的字段 {key!r}"))
        diags.sort(key=lambda d: (d.node_id, d.field_path, d.code, d.message))
        return diags

    def to_json_schema(self) -> dict[str, Any]:
        """导出 JSON Schema 片段（供前端变量监视面板 / 文档化）。"""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for f in self._fields:
            json_type = {"number": "number", "any": {}}.get(f.type, f.type)
            properties[f.name] = (
                {"description": f.description} if json_type == {} else
                {"type": json_type, "description": f.description})
            if f.required:
                required.append(f.name)
        schema: dict[str, Any] = {
            "type": "object", "additionalProperties": False,
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema


# ---------------------------------------------------------------------------
# 自定义 reducer（A-代码SDK-01）
# ---------------------------------------------------------------------------

#: reducer 签名：``(state, node_output) -> new_state``（纯函数，返回新字典）。
Reducer = Callable[[dict[str, Any], Any], "dict[str, Any]"]

REDUCER_REGISTRY: dict[str, Reducer] = {}


def register_reducer(name: str, fn: Reducer) -> Reducer:
    """注册一个命名 reducer；重名 / 签名不对 / 非二元纯函数都明确报错。"""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name or ""):
        raise DslSdkError(f"reducer 名不合法: {name!r}")
    if not callable(fn):
        raise DslSdkError(f"reducer {name} 必须是可调用对象")
    try:
        sig = inspect.signature(fn)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):  # 内置类型等拿不到签名
        positional = []
    if len(positional) < 2:
        raise DslSdkError(
            f"reducer {name} 必须接受两个位置参数 (state, output)，"
            f"实际可接收 {len(positional)} 个")
    if name in REDUCER_REGISTRY:
        raise DslSdkError(f"reducer {name!r} 已注册，拒绝覆盖")
    REDUCER_REGISTRY[name] = fn
    return fn


def get_reducer(name: str) -> Reducer:
    """按名取 reducer；未注册即报错（绝不静默跳过折叠）。"""
    try:
        return REDUCER_REGISTRY[name]
    except KeyError:
        raise DslSdkError(
            f"reducer {name!r} 未注册；已注册：{sorted(REDUCER_REGISTRY)}") from None


# ---------------------------------------------------------------------------
# 自定义节点契约（A-代码SDK-02）—— 只读对接 VERB_REGISTRY，绝不注入
# ---------------------------------------------------------------------------

#: 自定义节点实例在 DSL 文档里的载体：``agent`` 动词节点，agent 名为
#: ``custom:<node_id>``（平台对 agent 名只约束非空字符串，实例参数由 SDK
#: 侧表携带；节点 id 字符集 ``[A-Za-z0-9_-]`` 对该格式安全）。
CUSTOM_AGENT_PREFIX = "custom:"

#: 自定义节点处理器签名：``(payload, params) -> Any``（确定性纯函数）。
CustomNodeHandler = Callable[[Any, dict[str, Any]], Any]


@dataclass(frozen=True)
class CustomNodeSpec:
    """一个第三方自定义节点的完整契约（与平台 ``VerbSpec`` 同构的 SDK 层版）。"""

    name: str
    category: str
    summary: str
    #: params 的 JSON Schema 片段——与 ``VERB_REGISTRY[...].params_schema``
    #: 同一套约定（type=object / additionalProperties=False / properties）。
    params_schema: dict[str, Any]
    execute: CustomNodeHandler
    #: 跨字段检查（与 ``VerbSpec.check`` 同签名同语义）。
    check: Callable[[str, dict[str, Any]], None] | None = None
    #: 参数默认值（实例未传时合并进来）。
    defaults: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "category": self.category,
            "summary": self.summary, "params_schema": self.params_schema,
            "origin": "custom", "executable": True,
        }


CUSTOM_NODE_REGISTRY: dict[str, CustomNodeSpec] = {}

_CUSTOM_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _validate_schema_conventions(name: str, params_schema: Any) -> None:
    """params_schema 必须遵守与 ``VERB_REGISTRY`` 相同的片段约定（只读兼容）。"""
    if not isinstance(params_schema, dict):
        raise DslSdkError(f"自定义节点 {name} 的 params_schema 必须是对象")
    if params_schema.get("type") != "object" \
            or params_schema.get("additionalProperties") is not False \
            or not isinstance(params_schema.get("properties"), dict):
        raise DslSdkError(
            f"自定义节点 {name} 的 params_schema 必须是 "
            '{"type": "object", "additionalProperties": false, "properties": {...}} 形态')


def _validate_params(node_id: str, schema: dict[str, Any],
                     params: dict[str, Any], *, partial: bool = False) -> None:
    """按 JSON Schema 片段校验 params（与平台 ``_validate_params_against`` 同语义）。

    ``partial=True`` 用于注册期校验 ``defaults``——默认值允许是**片段**
    （只覆盖部分参数，其余由实例传入），故不做 required 检查，但封闭键与
    类型约束照常生效。
    """
    props: dict[str, Any] = schema.get("properties", {})
    if not partial:
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
        checks = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: not isinstance(v, bool) and isinstance(v, int),
            "number": lambda v: not isinstance(v, bool)
                                and isinstance(v, (int, float)),
            "boolean": lambda v: isinstance(v, bool),
        }
        check = checks.get(expected)
        if check is not None and not check(value):
            raise DslValidationError(
                f"节点 {node_id} 参数 {key} 的类型必须是 {expected}")
        if expected == "string" and isinstance(value, str) \
                and len(value) < sub.get("minLength", 0):
            raise DslValidationError(f"节点 {node_id} 参数 {key} 不能为空")


def register_custom_node(name: str, *, category: str, summary: str,
                         params_schema: dict[str, Any],
                         execute: CustomNodeHandler,
                         check: Callable[[str, dict[str, Any]], None] | None = None,
                         defaults: dict[str, Any] | None = None) -> CustomNodeSpec:
    """注册第三方自定义节点（**只读**对接 ``VERB_REGISTRY``，绝不注入）。

    安全边界（详见契约文档）：
    * ``name`` 不得与平台动词冲突——``VERB_REGISTRY`` 是封闭白名单
      （ADR-003），SDK 注册表对平台名**避让**而非覆盖；
    * 执行载体是 ``agent`` 动词节点（平台唯一注入点），SDK 在运行时把
      ``agent_resolver`` 接到本注册表；
    * 参数契约用与平台同一套 JSON Schema 片段约定，注册期即校验。
    """
    if not _CUSTOM_NAME_RE.fullmatch(name or ""):
        raise DslSdkError(f"自定义节点名不合法: {name!r}（需匹配 {_CUSTOM_NAME_RE.pattern}）")
    from .dsl_canvas import VERB_REGISTRY  # 只读消费；惰性避免导入环
    if name in VERB_REGISTRY:
        raise DslSdkError(
            f"自定义节点名 {name!r} 与平台受限动词冲突：VERB_REGISTRY 是封闭"
            "白名单（ADR-003），平台名一律保留，请换名注册")
    if name in CUSTOM_NODE_REGISTRY:
        raise DslSdkError(f"自定义节点 {name!r} 已注册，拒绝覆盖")
    _validate_schema_conventions(name, params_schema)
    if not callable(execute):
        raise DslSdkError(f"自定义节点 {name} 的 execute 必须是可调用对象")
    try:
        sig = inspect.signature(execute)
        positional = [p for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        positional = []
    if len(positional) < 2:
        raise DslSdkError(
            f"自定义节点 {name} 的 execute 必须接受两个位置参数 (payload, params)")
    if check is not None and not callable(check):
        raise DslSdkError(f"自定义节点 {name} 的 check 必须是可调用对象")
    defaults = dict(defaults or {})
    _validate_params(name, params_schema, defaults, partial=True)  # 片段默认值
    spec = CustomNodeSpec(name=name, category=category, summary=summary,
                          params_schema=params_schema, execute=execute,
                          check=check, defaults=defaults)
    CUSTOM_NODE_REGISTRY[name] = spec
    return spec


def custom_node_catalog() -> list[dict[str, Any]]:
    """节点目录：平台 ``verb_catalog()``（只读）在前，自定义节点在后。"""
    return [dict(entry, origin="platform") for entry in verb_catalog()] \
        + [spec.to_dict() for _, spec in sorted(CUSTOM_NODE_REGISTRY.items())]


# ---------------------------------------------------------------------------
# 代码定义图 + 工作流执行（A-三重模式-02 / A-代码SDK-01）
# ---------------------------------------------------------------------------


class GraphBuilder:
    """在代码里装配画布同构 DSL 文档（同一 IR 驱动，可与画布互导）。

    * ``input_node`` / ``transform_node`` / ``output_node`` / ``edge``
      产出平台 DSL 字典；
    * :meth:`custom_node` 产出 ``agent`` 动词节点并登记 SDK 侧实例参数；
    * :meth:`build` 过 :func:`assert_ir_valid` 强类型闸门后返回
      :class:`CodeWorkflow`——非法文档在这里就红，绝不带病执行。
    """

    def __init__(self, *, version: str = "1") -> None:
        if version != "1":
            raise DslSdkError('version 必须为 "1"')
        self._nodes: list[dict[str, Any]] = []
        self._edges: list[dict[str, Any]] = []
        self._custom: dict[str, tuple[CustomNodeSpec, dict[str, Any]]] = {}
        self._ids: set[str] = set()

    # -- 节点 ---------------------------------------------------------------

    def _add(self, node: dict[str, Any]) -> dict[str, Any]:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise DslSdkError("节点必须有非空字符串 id")
        if node_id in self._ids:
            raise DslSdkError(f"节点 id 重复: {node_id}")
        self._ids.add(node_id)
        self._nodes.append(node)
        return node

    def input_node(self, node_id: str, **params: Any) -> dict[str, Any]:
        node: dict[str, Any] = {"id": node_id, "type": "input"}
        if params:
            node["params"] = params
        return self._add(node)

    def transform_node(self, node_id: str, verb: str,
                       **params: Any) -> dict[str, Any]:
        node: dict[str, Any] = {"id": node_id, "type": "transform", "verb": verb}
        if params:
            node["params"] = params
        return self._add(node)

    def output_node(self, node_id: str, **params: Any) -> dict[str, Any]:
        node: dict[str, Any] = {"id": node_id, "type": "output"}
        if params:
            node["params"] = params
        return self._add(node)

    def custom_node(self, node_id: str, name: str,
                    **params: Any) -> dict[str, Any]:
        """声明一个自定义节点实例（载体是 ``agent`` 动词节点，见契约文档）。

        实例参数与注册期 defaults 合并后**立即**过该节点的 params 契约；
        合法性在装配期就锁定，不留到执行期。
        """
        spec = CUSTOM_NODE_REGISTRY.get(name)
        if spec is None:
            raise DslSdkError(
                f"自定义节点 {name!r} 未注册；已注册："
                f"{sorted(CUSTOM_NODE_REGISTRY)}")
        merged = {**spec.defaults, **params}
        _validate_params(node_id, spec.params_schema, merged)
        if spec.check is not None:
            spec.check(node_id, merged)
        self._custom[f"{CUSTOM_AGENT_PREFIX}{node_id}"] = (spec, merged)
        node = {"id": node_id, "type": "transform", "verb": "agent",
                "params": {"agent": f"{CUSTOM_AGENT_PREFIX}{node_id}"}}
        return self._add(node)

    # -- 边 -----------------------------------------------------------------

    def edge(self, src: str, dst: str,
             condition: dict[str, Any] | None = None) -> dict[str, Any]:
        edge_entry: dict[str, Any] = {"from": src, "to": dst}
        if condition is not None:
            edge_entry["condition"] = condition
        self._edges.append(edge_entry)
        return edge_entry

    # -- 产出 ---------------------------------------------------------------

    def to_document(self) -> dict[str, Any]:
        """平台同构 DSL 文档（过强类型 IR 闸门才返回）。"""
        doc = {"version": "1", "nodes": list(self._nodes),
               "edges": list(self._edges)}
        return assert_ir_valid(doc)

    def build(self, *, state_schema: StateSchema | None = None,
              node_reducers: dict[str, str] | None = None,
              agent_resolver: AgentResolver | None = None) -> "CodeWorkflow":
        """装配成可运行的 :class:`CodeWorkflow`（强类型 IR 闸门在此）。"""
        return CodeWorkflow(
            self.to_document(),
            state_schema=state_schema, node_reducers=node_reducers,
            custom_nodes=dict(self._custom),
            agent_resolver=agent_resolver)


@dataclass
class SdkRunResult:
    """SDK 运行结果：平台 :class:`RunResult` + 折叠后的状态与诊断。"""

    result: RunResult
    state: dict[str, Any]
    state_diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.result.status

    @property
    def output(self) -> Any:
        return self.result.output

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(), "state": self.state,
            "state_diagnostics": [d.to_dict() for d in self.state_diagnostics],
        }


class CodeWorkflow:
    """一份代码定义（或画布导入）的工作流：强类型校验 + 执行 + 状态折叠。"""

    def __init__(self, doc: dict[str, Any], *,
                 state_schema: StateSchema | None = None,
                 node_reducers: dict[str, str] | None = None,
                 custom_nodes: dict[str, tuple[CustomNodeSpec, dict[str, Any]]]
                 | None = None,
                 agent_resolver: AgentResolver | None = None) -> None:
        # 同源闸门：与画布走同一个强类型 IR 校验（extra="forbid"）。
        assert_ir_valid(doc)
        self.doc = doc
        self.state_schema = state_schema
        self.node_reducers = dict(node_reducers or {})
        self._custom = dict(custom_nodes or {})
        self._agent_resolver = agent_resolver
        # reducer 引用提前解析：拼错名字在装配期就红，不带病执行。
        for node_id, reducer_name in self.node_reducers.items():
            if node_id not in {n["id"] for n in doc["nodes"]}:
                raise DslSdkError(
                    f"node_reducers 引用了未定义节点 {node_id!r}")
            get_reducer(reducer_name)
        if state_schema is not None:
            diags = state_schema.validate(state_schema.initial_state())
            if diags:
                raise DslSdkError(
                    "初始状态不合法: " + "；".join(d.message for d in diags))

    def validate(self) -> list[Diagnostic]:
        """收集式 IR 校验（同一诊断模型，供编辑器逐字段定位）。"""
        return validate_ir(self.doc)

    def _make_resolver(self) -> AgentResolver:
        """把 ``agent`` 注入点接到自定义节点注册表（未命中则透传/诚实失败）。"""
        custom = self._custom
        fallback = self._agent_resolver

        def resolver(agent_name: str, payload: Any, params: dict[str, Any]) -> Any:
            if agent_name.startswith(CUSTOM_AGENT_PREFIX):
                entry = custom.get(agent_name)
                if entry is None:
                    raise DslValidationError(
                        f"agent {agent_name!r} 不是本工作流声明的自定义节点")
                spec, merged = entry
                return spec.execute(payload, dict(merged))
            if fallback is not None:
                return fallback(agent_name, payload, params)
            raise DslValidationError(
                f"agent {agent_name!r} 未注入解析器；自定义节点请用 "
                f"GraphBuilder.custom_node 声明，普通 agent 请传 agent_resolver")

        return resolver

    def run(self, **run_kwargs: Any) -> SdkRunResult:
        """执行（:func:`run_dsl` 同源）并折叠状态。

        状态折叠的诚实边界：按**执行日志顺序**对 ``succeeded`` 节点应用
        注册的 reducer（skipped/failed/suspended 不折叠）；折叠发生在
        run 完成后，不是执行器内的流式钩子。
        """
        result = run_dsl(self.doc, agent_resolver=self._make_resolver(),
                         **run_kwargs)
        state = self.state_schema.initial_state() if self.state_schema else {}
        for log in result.logs:
            reducer_name = self.node_reducers.get(log.node_id)
            if reducer_name is None or log.status != "succeeded":
                continue
            state = get_reducer(reducer_name)(state, log.output)
        state_diags = self.state_schema.validate(state) \
            if self.state_schema else []
        return SdkRunResult(result=result, state=state,
                            state_diagnostics=state_diags)
