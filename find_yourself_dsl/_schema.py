"""受限动词集的参数契约（平台 ``VERB_REGISTRY`` 的只读镜像）。

**唯一真源在平台** ``find_yourself.services.dsl_canvas.VERB_REGISTRY``；
本表是导出自包含运行库的**只读镜像**——独立运行库不能 import 平台（那会
拖进 SQLAlchemy/Pydantic 全家桶，违背「零第三方依赖」），因此契约在这里
有一份逐字镜像。防漂移靠平台侧测试
``tests/unit/dsl_sdk/test_runtime_parity.py``：逐一断言本表与注册表
的枚举/必填/类型完全一致，漂移即红。

镜像范围（与平台 :func:`validate_dsl` 的闸门一致）：
* 节点 id：``^[A-Za-z0-9_-]{1,64}$``；
* params JSON Schema 片段（type / required / enum / minLength，封闭）；
* 跨字段检查：``map.set`` 需要 ``field``、数值聚合算子需要 ``field``。
* **不镜像**：``approval.op`` 的治理白名单——它唯一真源是
  ``services/proposal.py``，运行库不重抄（重抄就是给治理开旁路）；
  治理白名单校验发生在平台编译期，导出物在平台内运行时照常受检。
"""

from __future__ import annotations

import re
from typing import Any

from ._errors import DslValidationError

__all__ = [
    "NODE_TYPES", "INPUT_KINDS", "OUTPUT_FORMATS", "MAP_OPS", "FILTER_OPS",
    "CONDITION_OPS", "AGGREGATE_OPS", "NUMERIC_AGGREGATE_OPS", "MERGE_OPS",
    "TRANSFORM_VERBS", "TRANSFORM_PARAMS_SCHEMAS", "NODE_PARAMS_SCHEMAS",
    "validate_node_id", "validate_params_against",
]

NODE_TYPES = ("input", "transform", "output")
INPUT_KINDS = ("literal", "text_lines")
OUTPUT_FORMATS = ("json", "text")
MAP_OPS = ("set", "upper", "lower")
FILTER_OPS = ("eq", "ne", "gt", "lt", "contains")
CONDITION_OPS = FILTER_OPS
AGGREGATE_OPS = ("count", "sum", "min", "max", "avg", "first", "last", "join", "unique")
NUMERIC_AGGREGATE_OPS = ("sum", "min", "max", "avg")
MERGE_OPS = ("concat", "first", "last")

#: 顺序即平台 ``TRANSFORM_VERBS`` 的顺序（注册表插入序）。
TRANSFORM_VERBS: tuple[str, ...] = (
    "map", "filter", "template", "branch", "aggregate", "merge",
    "agent", "confirm", "artifact", "approval",
)

_NODE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(required), "properties": properties}


#: 与平台 ``VERB_REGISTRY[...].params_schema`` 逐字镜像（顺序亦一致）。
TRANSFORM_PARAMS_SCHEMAS: dict[str, dict[str, Any]] = {
    "map": _obj({
        "op": {"enum": list(MAP_OPS)},
        "field": {"type": "string"},
        "value": {},
    }, required=("op",)),
    "filter": _obj({
        "field": {"type": "string"},
        "op": {"enum": list(FILTER_OPS)},
        "value": {},
    }, required=("field", "op")),
    "template": _obj({"template": {"type": "string", "minLength": 1}},
                     required=("template",)),
    "branch": _obj({
        "field": {"type": "string"},
        "op": {"enum": list(CONDITION_OPS)},
        "value": {},
        "then_label": {"type": "string", "minLength": 1},
        "else_label": {"type": "string", "minLength": 1},
    }, required=("field", "op", "then_label", "else_label")),
    "aggregate": _obj({
        "op": {"enum": list(AGGREGATE_OPS)},
        "field": {"type": "string"},
        "sep": {"type": "string"},
    }, required=("op",)),
    "merge": _obj({"mode": {"enum": list(MERGE_OPS)}}, required=("mode",)),
    "agent": _obj({"agent": {"type": "string", "minLength": 1}},
                  required=("agent",)),
    "confirm": _obj({
        "prompt": {"type": "string", "minLength": 1},
        "role": {"type": "string"},
    }, required=("prompt",)),
    "artifact": _obj({
        "name": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "minLength": 1},
    }, required=("name",)),
    "approval": _obj({
        "op": {"type": "string", "minLength": 1},
        "target_id": {"type": "string", "minLength": 1},
        "reason": {"type": "string"},
        "rollback": {"type": "string"},
    }, required=("op",)),
}

NODE_PARAMS_SCHEMAS: dict[str, dict[str, Any]] = {
    "input": _obj({"kind": {"enum": list(INPUT_KINDS)}, "value": {}}),
    "output": _obj({"format": {"enum": list(OUTPUT_FORMATS)}}),
}


def validate_node_id(node_id: Any) -> str:
    """节点 id 校验（与平台 ``validate_dsl`` 同一正则、同一报错口径）。"""
    if not isinstance(node_id, str) or not node_id:
        raise DslValidationError("每个节点必须有非空字符串 id")
    if not _NODE_ID_RE.fullmatch(node_id):
        raise DslValidationError(f"节点 id 不合法: {node_id!r}")
    return node_id


def validate_params_against(node_id: str, schema: dict[str, Any],
                            params: dict[str, Any]) -> None:
    """按 params 的 JSON Schema 片段校验（封闭：多余键即错）。

    与平台 ``_validate_params_against`` 逐字同语义；独立维护是因为运行库
    不能 import 平台。漂移护栏见模块 docstring。
    """
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


def check_cross_field(node_id: str, verb: str, params: dict[str, Any]) -> None:
    """编译期跨字段检查（map.set / 数值聚合需要 field），与平台 ``VerbSpec.check`` 对齐。"""
    if verb == "map" and params.get("op") == "set" \
            and not isinstance(params.get("field"), str):
        raise DslValidationError(f"节点 {node_id} map.set 需要 field")
    if verb == "aggregate" and params.get("op") in NUMERIC_AGGREGATE_OPS \
            and not isinstance(params.get("field"), str):
        raise DslValidationError(
            f"节点 {node_id} aggregate.op={params.get('op')} 需要 field")
