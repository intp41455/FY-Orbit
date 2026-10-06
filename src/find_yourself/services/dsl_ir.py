"""DSL 强类型 IR（ADR-02 第一切片）。

背景
----
改造前，受限 DSL 画布只有**运行时**的 ``validate_dsl``（手写、遇错即抛）与一份
``DSL_JSON_SCHEMA``；节点 ``params`` 在类型层面是裸 ``object``，**类型错误只能在
运行期（甚至编译期之后）才暴露**，且错误信息是单条字符串，无法被前端逐字段定位。

本模块引入一个**静态、确定性**的类型化 IR：

* 节点 / 边 / 边条件用 Pydantic 模型表达，``extra="forbid"``（与
  ``workflows/models.py::Strict`` 同范式）——多余字段是契约违约，宁可报错也
  不让它「看起来生效了」。
* :func:`validate_ir` **不抛异常**，而是返回全部 :class:`Diagnostic`
  （``node_id`` + ``field_path`` + ``code`` + ``message``），供前端/工具逐条定位。
* :func:`assert_ir_valid` 在编译路径入口把诊断汇总成一条 ``DslValidationError``，
  从而**在执行之前**拦住类型错误。

唯一真源，杜绝双份 schema
-------------------------
各动词的 ``params`` 模型**不在本文件手写**，而是由
``dsl_canvas.VERB_REGISTRY[...].params_schema`` 与
``dsl_canvas.NODE_PARAMS_SCHEMAS`` **动态派生**（见 :func:`_build_model`）。
跨字段约束（JSON Schema 表达不了，如 ``aggregate.op=sum`` 需要 ``field``）同样
复用注册表里的 ``VerbSpec.check``，不另写一套。因此 IR 与注册表**不可能漂移**。

诚实边界（如实标注，不假装已强类型化）
-------------------------------------
* ``map.value`` / ``filter.value`` / ``branch.value`` / ``input.value`` /
  边条件 ``condition.value`` 在既有 JSON Schema 里就是 ``{}``（任意 JSON），
  它们是**用户数据本身**，无法在不改变语义的前提下强类型化 —— 派生为 ``Any``。
* 目前注册表里的参数类型只用到 ``enum`` 与 ``string``；``integer`` / ``number`` /
  ``boolean`` 分支为派生器完整性保留（``number`` 需显式拒绝 ``bool``，与
  ``_validate_params_against`` 的语义对齐）。见 ``TODO(ADR-02)``。

确定性铁律（R-04）
------------------
本模块**全确定性**：只做纯函数式的类型推断与校验，**LLM 绝不参与编译期**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    create_model,
)

from .dsl_canvas import (
    CONDITION_OPS,
    FLOW_TYPES,
    NODE_PARAMS_SCHEMAS,
    NODE_TYPES,
    TRANSFORM_VERBS,
    VERB_REGISTRY,
    DslValidationError,
)

#: DSL 文档允许的顶层字段（模型/视图分离：``layout`` 等视图状态不得进入 DSL）。
#: A-画布搭建器-05：``flow_type``（chatflow/workflow）是语义字段，允许进入文档。
DOCUMENT_KEYS = frozenset({"version", "nodes", "edges", "flow_type"})


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnostic:
    """一条可定位的类型诊断。

    :param node_id: 出错的节点 id；文档级错误为空串。
    :param field_path: 出错字段的点分路径（如 ``params.op`` / ``params.field``）；
        无具体字段时为空串。
    :param code: 稳定的机器可读错误码（见 :data:`_ERROR_CODE_MAP`）。
    :param message: 人类可读说明。
    """

    node_id: str
    field_path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "field_path": self.field_path,
            "code": self.code,
            "message": self.message,
        }

    def __str__(self) -> str:
        where = f"节点 {self.node_id}" if self.node_id else "文档"
        field = f" 字段 {self.field_path}" if self.field_path else ""
        return f"[{self.code}] {where}{field}: {self.message}"


# ---------------------------------------------------------------------------
# JSON Schema 片段 → Pydantic 模型（派生，不手写）
# ---------------------------------------------------------------------------


class Strict(BaseModel):
    """``extra="forbid"`` 基类（与 ``workflows/models.py::Strict`` 同范式）。"""

    model_config = ConfigDict(extra="forbid")


def _reject_bool(value: Any) -> Any:
    """``int`` / ``float`` 字段不得把 ``bool`` 当数值（``True`` 是 ``int`` 子类）。"""
    if isinstance(value, bool):
        raise ValueError("布尔值不能当数值使用")
    return value


def _annotation_for(sub: dict[str, Any]) -> Any:
    """把单个 JSON Schema 属性片段映射为 Pydantic 类型注解。

    只覆盖注册表实际使用的词汇（``enum`` / ``string``）并为其它的
    常用标量保留分支；无法识别者诚实退化为 ``Any``。
    """
    if "enum" in sub:
        values = tuple(sub["enum"])
        if not values:  # pragma: no cover - 现有注册表不会走到
            return Any
        return Literal[values]
    kind = sub.get("type")
    if kind == "string":
        min_length = sub.get("minLength", 0)
        if min_length:
            return Annotated[str, Field(min_length=min_length)]
        return str
    if kind == "integer":
        # TODO(ADR-02): 现有注册表尚无 integer 参数；此处为派生器完整性保留。
        return StrictInt
    if kind == "number":
        # TODO(ADR-02): 同上。显式拒绝 bool（与 _validate_params_against 对齐）。
        return Annotated[float, BeforeValidator(_reject_bool)]
    if kind == "boolean":
        # TODO(ADR-02): 同上。
        return bool
    if kind == "array":
        # A-画布搭建器-01：knowledge_retrieval.document_ids / classifier.classes /
        # extractor.fields / http.allow_domains / human_input.options 等。
        return list
    if kind == "object":
        # iteration/loop.subflow、http_request.headers、tool.arguments、
        # trigger.config 等。
        return dict
    return Any


def _build_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """由 ``params_schema`` 派生一个封闭（``extra="forbid"``）的 Pydantic 模型。

    ``required`` 之外的属性一律可选、默认 ``None``——与既有 JSON Schema 的
    「非 required 即可省略」语义一致。
    """
    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for key, sub in properties.items():
        annotation = _annotation_for(sub)
        if key in required:
            fields[key] = (annotation, ...)
        else:
            # ``Any | None`` 退化为 ``Any``，仍保留默认 None 以匹配「可省略」。
            fields[key] = (annotation if annotation is Any else annotation | None, None)
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


#: 各 transform 动词的 params 模型：``verb -> model``（由注册表派生）。
TRANSFORM_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    verb: _build_model(f"{verb}_params", VERB_REGISTRY[verb].params_schema)
    for verb in TRANSFORM_VERBS
}

#: 全部节点类型的 params 模型（由 ``NODE_PARAMS_SCHEMAS`` 派生；A-画布搭建器-01
#: 起 16 类节点全部有封闭契约，不再只有 input/output 两类）。
NODE_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    node_type: _build_model(f"{node_type}_params", NODE_PARAMS_SCHEMAS[node_type])
    for node_type in NODE_PARAMS_SCHEMAS
}


def params_model(verb: str) -> type[BaseModel] | None:
    """取某动词（或 ``input`` / ``output``）派生出的 params 模型；未知则 None。"""
    return TRANSFORM_PARAMS_MODELS.get(verb) or NODE_PARAMS_MODELS.get(verb)


# ---------------------------------------------------------------------------
# 节点 / 边 / 文档模型
# ---------------------------------------------------------------------------


class EdgeCondition(Strict):
    """边条件：条件不满足则后继节点不入队（动态展开）。"""

    field: str
    op: Literal[CONDITION_OPS]
    # 与节点 value 同理：比较目标是用户数据，诚实为 Any。
    value: Any = None


class DslEdge(Strict):
    """一条有向边。``from`` 是 Python 关键字，用别名暴露。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_id: str = Field(alias="from")
    to: str
    condition: EdgeCondition | None = None


class DslNode(Strict):
    """一个节点。``params`` 的具体约束按 ``type``（+ ``verb``）另做派生校验。"""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal[NODE_TYPES]
    verb: str | None = None
    params: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 诊断码
# ---------------------------------------------------------------------------

_ERROR_CODE_MAP: dict[str, str] = {
    "missing": "missing_field",
    "extra_forbidden": "extra_field",
    "enum": "invalid_enum",
    "literal_error": "invalid_enum",
    "string_type": "invalid_type",
    "string_too_short": "empty_string",
    "string_pattern_mismatch": "invalid_node_id",
    "string_too_long": "invalid_node_id",
    "int_type": "invalid_type",
    "int_parsing": "invalid_type",
    "float_type": "invalid_type",
    "float_parsing": "invalid_type",
    "bool_type": "invalid_type",
    "bool_parsing": "invalid_type",
    "model_attributes_type": "invalid_type",
    "dict_type": "invalid_type",
}


def _code_for(error_type: str) -> str:
    return _ERROR_CODE_MAP.get(error_type, "invalid_value")


def _pydantic_diags(exc: ValidationError, *, node_id: str, prefix: str) -> list[Diagnostic]:
    """把 Pydantic 的 ``ValidationError`` 逐条翻译为带字段路径的 :class:`Diagnostic`。"""
    out: list[Diagnostic] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        if loc:
            field_path = f"{prefix}{loc}" if prefix else loc
        else:
            field_path = prefix.rstrip(".")
        out.append(Diagnostic(
            node_id=node_id,
            field_path=field_path,
            code=_code_for(err["type"]),
            message=err["msg"],
        ))
    return out


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _validate_node_params(node: DslNode, diags: list[Diagnostic]) -> None:
    """按节点类型派生出的模型校验 ``params``（含跨字段约束）。"""
    if node.type == "transform":
        verb = node.verb
        if verb is None:
            diags.append(Diagnostic(node.id, "verb", "missing_verb",
                                    "transform 节点必须声明 verb"))
            return
        if verb not in VERB_REGISTRY:
            diags.append(Diagnostic(
                node.id, "verb", "unknown_verb",
                f"transform 节点 verb 必须是 {TRANSFORM_VERBS}，实际是 {verb!r}"))
            return
        if node.params is None:
            diags.append(Diagnostic(node.id, "params", "invalid_type",
                                    "transform 节点 params 必须是对象"))
            return
        model = TRANSFORM_PARAMS_MODELS[verb]
        try:
            model.model_validate(node.params)
        except ValidationError as exc:
            diags.extend(_pydantic_diags(exc, node_id=node.id, prefix="params."))
            return
        # 跨字段约束：复用注册表里的 check（唯一真源，不另写一套）。
        check = VERB_REGISTRY[verb].check
        if check is not None:
            try:
                check(node.id, node.params)
            except DslValidationError as exc:
                # 带字段路径的编译错误（如 approval.op 白名单）精确落到该字段；
                # 其余跨字段错误（如 aggregate.op=sum 需要 field）回落到 params。
                field = getattr(exc, "field_path", "") or "params"
                diags.append(Diagnostic(node.id, field, "cross_field", str(exc)))
        return

    # input / output：params 可省略或为空 dict。
    params = node.params or {}
    model = NODE_PARAMS_MODELS[node.type]
    try:
        model.model_validate(params)
    except ValidationError as exc:
        diags.extend(_pydantic_diags(exc, node_id=node.id, prefix="params."))


def validate_ir(doc: Any) -> list[Diagnostic]:
    """校验 DSL 文档，返回**全部**类型诊断（合法时为空列表）。

    与 :func:`~find_yourself.services.dsl_canvas.validate_dsl`（遇错即抛）不同，
    本函数**收集**诊断，供调用方一次性拿到「哪个节点的哪个字段错在哪」，从而
    在**执行之前**给出可定位的反馈。全确定性，无副作用，无 LLM。
    """
    diags: list[Diagnostic] = []
    if not isinstance(doc, dict):
        return [Diagnostic("", "", "document_not_object", "DSL 文档必须是 JSON 对象")]

    # 文档级封闭：多余顶层字段（如视图态 layout）明确报错。
    for key in doc:
        if key not in DOCUMENT_KEYS:
            diags.append(Diagnostic(
                "", str(key), "extra_field",
                f"文档不支持的顶层字段 {key!r}，允许 {tuple(sorted(DOCUMENT_KEYS))}"))

    if doc.get("version") != "1":
        diags.append(Diagnostic("", "version", "invalid_version",
                                'version 必须为 "1"'))

    # A-画布搭建器-05：可选 flow_type 的枚举校验（缺省即 workflow，向后兼容）。
    doc_flow_type = doc.get("flow_type")
    if doc_flow_type is not None and doc_flow_type not in FLOW_TYPES:
        diags.append(Diagnostic(
            "", "flow_type", "invalid_enum",
            f"flow_type 必须是 {FLOW_TYPES}，实际是 {doc_flow_type!r}"))

    raw_nodes = doc.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        diags.append(Diagnostic("", "nodes", "invalid_type", "nodes 必须是非空数组"))
        raw_nodes = raw_nodes if isinstance(raw_nodes, list) else []

    raw_edges = doc.get("edges")
    if not isinstance(raw_edges, list):
        diags.append(Diagnostic("", "edges", "invalid_type", "edges 必须是数组"))
        raw_edges = []

    known_ids: set[str] = set()
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            diags.append(Diagnostic(f"<node#{index}>", "", "invalid_type",
                                    "节点必须是对象"))
            continue
        raw_id = raw.get("id")
        node_id = raw_id if isinstance(raw_id, str) and raw_id else f"<node#{index}>"
        try:
            node = DslNode.model_validate(raw)
        except ValidationError as exc:
            diags.extend(_pydantic_diags(exc, node_id=node_id, prefix=""))
            continue
        if node.id in known_ids:
            diags.append(Diagnostic(node.id, "id", "duplicate_node_id",
                                    f"节点 id 重复: {node.id}"))
            continue
        known_ids.add(node.id)
        _validate_node_params(node, diags)

    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            diags.append(Diagnostic(f"<edge#{index}>", "", "invalid_type",
                                    "边必须是对象"))
            continue
        raw_from = raw.get("from")
        edge_id = raw_from if isinstance(raw_from, str) else ""
        try:
            edge = DslEdge.model_validate(raw)
        except ValidationError as exc:
            diags.extend(_pydantic_diags(exc, node_id=edge_id, prefix=""))
            continue
        if edge.from_id not in known_ids:
            diags.append(Diagnostic(edge.from_id, "from", "dangling_edge",
                                    f"边 {edge.from_id!r}->{edge.to!r} 引用了未定义节点"))
        if edge.to not in known_ids:
            diags.append(Diagnostic(edge.from_id, "to", "dangling_edge",
                                    f"边 {edge.from_id!r}->{edge.to!r} 引用了未定义节点"))

    # 确定性：按 (node_id, field_path, code, message) 稳定排序。
    diags.sort(key=lambda d: (d.node_id, d.field_path, d.code, d.message))
    return diags


def format_diagnostics(diags: list[Diagnostic]) -> str:
    """把诊断汇总成单条可读消息（供编译路径抛 ``DslValidationError``）。"""
    parts = "；".join(str(d) for d in diags)
    return f"DSL 类型校验发现 {len(diags)} 处错误：{parts}"


def assert_ir_valid(doc: Any) -> dict[str, Any]:
    """类型校验闸门：有诊断即明确抛 :class:`DslValidationError`，绝不静默降级。"""
    diags = validate_ir(doc)
    if diags:
        raise DslValidationError(format_diagnostics(diags))
    return doc
