"""图构造器与文档校验（``Flow`` / ``input_node`` / ``transform_node`` /
``output_node`` / ``edge``）。

这就是平台代码导出（``flow_restricted.py``）头部 import 的五个构造调用的
真实实现。导出文件在 import 期逐条调用它们装配出与画布语义一致的图；
随后调用 :func:`find_yourself_dsl.run` 即可执行。

语义对齐平台 ``services/dsl_canvas.py``：

* ``version`` 必须是 ``"1"``；节点 id / 动词白名单 / params 契约与平台
  ``validate_dsl`` 同闸门（见 ``_schema.py``）。
* :func:`canonical_dsl` / :func:`dsl_digest` 与平台同省略规则、同摘要算法
  （SHA-256 前 16 位）——语义等价的两份文档摘要相同，嵌入方可据此做
  「人所见即所批」的 TOCTOU 校验。
* ``Flow(version=...)`` 是**模块级当前构造目标**：导出文件一个文件一张图
  （平台解析器强制 ``Flow`` 只构造一次），先后 import 两个导出文件时后一个
  覆盖前一个的「当前图」——独立运行库按单图使用设计，如实注明。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ._errors import DslValidationError
from ._schema import (
    CONDITION_OPS,
    NODE_PARAMS_SCHEMAS,
    NODE_TYPES,
    TRANSFORM_PARAMS_SCHEMAS,
    TRANSFORM_VERBS,
    check_cross_field,
    validate_node_id,
    validate_params_against,
)

__all__ = [
    "Flow", "FlowBuilder", "input_node", "transform_node", "output_node",
    "edge", "to_document", "reset_current_flow", "validate_document",
    "canonical_dsl", "dsl_digest",
]


class FlowBuilder:
    """一张图的构造目标：收集节点与边，:meth:`document` 输出平台同构文档。"""

    def __init__(self, version: str = "1") -> None:
        if version != "1":
            raise DslValidationError('version 必须为 "1"')
        self.version = version
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    # -- 节点 ---------------------------------------------------------------

    def add_input(self, node_id: str, **params: Any) -> dict[str, Any]:
        return self._add_node(node_id, "input", None, params)

    def add_transform(self, node_id: str, verb: str,
                      **params: Any) -> dict[str, Any]:
        return self._add_node(node_id, "transform", verb, params)

    def add_output(self, node_id: str, **params: Any) -> dict[str, Any]:
        return self._add_node(node_id, "output", None, params)

    def _add_node(self, node_id: str, ntype: str, verb: str | None,
                  params: dict[str, Any]) -> dict[str, Any]:
        validate_node_id(node_id)
        if any(n["id"] == node_id for n in self.nodes):
            raise DslValidationError(f"节点 id 重复: {node_id}")
        node: dict[str, Any] = {"id": node_id, "type": ntype}
        if ntype == "transform":
            if verb not in TRANSFORM_VERBS:
                raise DslValidationError(
                    f"transform 节点 {node_id} verb 必须是 {TRANSFORM_VERBS}，"
                    f"实际是 {verb!r}")
            node["verb"] = verb
            if not isinstance(params, dict):
                raise DslValidationError(f"节点 {node_id} params 必须是对象")
            validate_params_against(node_id, TRANSFORM_PARAMS_SCHEMAS[verb], params)
            check_cross_field(node_id, verb, params)
        else:
            validate_params_against(node_id, NODE_PARAMS_SCHEMAS[ntype], params)
        if params:
            node["params"] = params
        self.nodes.append(node)
        return node

    # -- 边 -----------------------------------------------------------------

    def add_edge(self, src: str, dst: str,
                 condition: dict[str, Any] | None = None) -> dict[str, Any]:
        edge_entry: dict[str, Any] = {"from": src, "to": dst}
        if condition is not None:
            _validate_condition(src, dst, condition)
            edge_entry["condition"] = condition
        self.edges.append(edge_entry)
        return edge_entry

    def document(self) -> dict[str, Any]:
        """输出平台同构 DSL 文档（并整体复检一遍，绝不返回半成品）。"""
        doc = {"version": self.version, "nodes": list(self.nodes),
               "edges": list(self.edges)}
        return validate_document(doc)


def _validate_condition(src: str, dst: str, condition: Any) -> None:
    """边条件形状校验（与平台 ``validate_dsl`` 同口径）。"""
    if not isinstance(condition, dict) \
            or condition.get("op") not in CONDITION_OPS \
            or not isinstance(condition.get("field"), str) or not condition["field"]:
        raise DslValidationError(
            f"边 {src}->{dst} condition 需要 field/op/value 且 op 属于 {CONDITION_OPS}")


def validate_document(doc: Any) -> dict[str, Any]:
    """整图结构校验（节点/边/端点/环外交给 :func:`_engine.compile_document`）。"""
    if not isinstance(doc, dict):
        raise DslValidationError("DSL 文档必须是 JSON 对象")
    if doc.get("version") != "1":
        raise DslValidationError('version 必须为 "1"')
    nodes = doc.get("nodes")
    edges = doc.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise DslValidationError("nodes 必须是非空数组")
    if not isinstance(edges, list):
        raise DslValidationError("edges 必须是数组")

    ids: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            raise DslValidationError("节点必须是对象")
        validate_node_id(n.get("id"))
        if n["id"] in ids:
            raise DslValidationError(f"节点 id 重复: {n['id']}")
        ids.add(n["id"])
        if n.get("type") not in NODE_TYPES:
            raise DslValidationError(f"节点 {n['id']} type 必须是 {NODE_TYPES}")
        if n["type"] == "transform":
            verb = n.get("verb")
            if verb not in TRANSFORM_VERBS:
                raise DslValidationError(
                    f"transform 节点 {n['id']} verb 必须是 {TRANSFORM_VERBS}，"
                    f"实际是 {verb!r}")
            params = n.get("params", {})
            if not isinstance(params, dict):
                raise DslValidationError(f"节点 {n['id']} params 必须是对象")
            validate_params_against(n["id"], TRANSFORM_PARAMS_SCHEMAS[verb], params)
            check_cross_field(n["id"], verb, params)
        else:
            params = n.get("params", {}) or {}
            if not isinstance(params, dict):
                raise DslValidationError(f"节点 {n['id']} params 必须是对象")
            validate_params_against(n["id"], NODE_PARAMS_SCHEMAS[n["type"]], params)

    for e in edges:
        if not isinstance(e, dict):
            raise DslValidationError("边必须是对象")
        src, dst = e.get("from"), e.get("to")
        if src not in ids or dst not in ids:
            raise DslValidationError(f"边 {src!r}->{dst!r} 引用了未定义节点")
        if e.get("condition") is not None:
            _validate_condition(src, dst, e["condition"])
    return doc


# ---------------------------------------------------------------------------
# 模块级当前构造目标（导出文件按「Flow → 节点 → 边」的顺序裸调用）
# ---------------------------------------------------------------------------

_current: FlowBuilder | None = None


def _require_current() -> FlowBuilder:
    if _current is None:
        raise DslValidationError(
            "必须先构造 Flow(version=...) 再调用节点/边构造函数")
    return _current


def Flow(version: str = "1") -> FlowBuilder:
    """新建一张图并设为**当前构造目标**（导出文件的第一个调用）。"""
    global _current
    _current = FlowBuilder(version)
    return _current


def input_node(node_id: str, **params: Any) -> dict[str, Any]:
    """声明一个 input 节点（``kind`` / ``value`` 参数可选）。"""
    return _require_current().add_input(node_id, **params)


def transform_node(node_id: str, verb: str, **params: Any) -> dict[str, Any]:
    """声明一个 transform 节点（``verb`` 取自受限动词白名单）。"""
    return _require_current().add_transform(node_id, verb, **params)


def output_node(node_id: str, **params: Any) -> dict[str, Any]:
    """声明一个 output 节点（``format`` 参数可选，默认 json）。"""
    return _require_current().add_output(node_id, **params)


def edge(src: str, dst: str,
         condition: dict[str, Any] | None = None) -> dict[str, Any]:
    """声明一条有向边（``condition`` 可选；两端必须是已声明节点 id）。"""
    return _require_current().add_edge(src, dst, condition)


def to_document() -> dict[str, Any]:
    """当前图的平台同构 DSL 文档（校验通过才返回）。"""
    return _require_current().document()


def reset_current_flow() -> None:
    """清空当前构造目标（多次构建 / 测试隔离用）。"""
    global _current
    _current = None


# ---------------------------------------------------------------------------
# 规范化与摘要（与平台同算法，供「人所见即所批」校验复用）
# ---------------------------------------------------------------------------


def canonical_dsl(doc: dict[str, Any]) -> dict[str, Any]:
    """规范化（省略空 params / 非 transform 的 verb / 空 condition），与平台一致。"""
    validate_document(doc)
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


def dsl_digest(doc: dict[str, Any]) -> str:
    """规范化摘要（SHA-256 前 16 位），与平台 ``dsl_canvas.dsl_digest`` 同算法。"""
    canonical = canonical_dsl(doc)
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
