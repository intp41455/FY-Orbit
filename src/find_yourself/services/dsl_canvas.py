"""受限 DSL 画布：模型、编译器与确定性执行引擎（工单 P1-18）。

实现蓝本（claw-dialogue-extraction §1.2/§1.3）：
* 模型/视图分离 —— DSL 文档（model）只包含节点/边/参数；坐标、层级等视图
  状态属于 layout，绝不进入 DSL。
* Compiler —— 拓扑排序 + 环检测；条件分支按「节点完成后按后继边条件入队」
  动态展开（queue 驱动的调度，而非一次性静态计划）。
* Executor —— 受限动词集（input / transform: map|filter|template / output），
  全部确定性执行，无外部 LLM 依赖；每个节点的输入输出都记入运行日志。

执行记录只落内存 + 归档目录（env ``FY_DSL_ARCHIVE_DIR``），不写数据库迁移。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 受限动词集定义
# ---------------------------------------------------------------------------

NODE_TYPES = ("input", "transform", "output")
TRANSFORM_VERBS = ("map", "filter", "template")
INPUT_KINDS = ("literal", "text_lines")
OUTPUT_FORMATS = ("json", "text")
MAP_OPS = ("set", "upper", "lower")
FILTER_OPS = ("eq", "ne", "gt", "lt", "contains")
CONDITION_OPS = FILTER_OPS

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
                    # 仅 transform 节点使用
                    "verb": {"enum": list(TRANSFORM_VERBS)},
                    "params": {"type": "object"},
                },
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


class DslValidationError(ValueError):
    """DSL 文档不合法（结构 / 拓扑 / 参数）。"""


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
            if n.get("verb") not in TRANSFORM_VERBS:
                raise DslValidationError(
                    f"transform 节点 {n['id']} verb 必须是 {TRANSFORM_VERBS}")
            params = n.get("params", {})
            if not isinstance(params, dict):
                raise DslValidationError(f"节点 {n['id']} params 必须是对象")
            _validate_transform_params(n["id"], n["verb"], params)
        elif n.get("params") is not None and not isinstance(n["params"], dict):
            raise DslValidationError(f"节点 {n['id']} params 必须是对象")

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


def _validate_transform_params(node_id: str, verb: str, params: dict[str, Any]) -> None:
    if verb == "map":
        op = params.get("op")
        if op not in MAP_OPS:
            raise DslValidationError(f"节点 {node_id} map.op 必须是 {MAP_OPS}")
        if op == "set" and not isinstance(params.get("field"), str):
            raise DslValidationError(f"节点 {node_id} map.set 需要 field")
    elif verb == "filter":
        if params.get("op") not in FILTER_OPS or not isinstance(params.get("field"), str):
            raise DslValidationError(
                f"节点 {node_id} filter 需要 field + op({FILTER_OPS})")
    elif verb == "template":
        if not isinstance(params.get("template"), str) or not params["template"]:
            raise DslValidationError(f"节点 {node_id} template 需要 template 字符串")


def compile_dsl(doc: Any) -> CompiledPlan:
    """校验 + 拓扑排序（Kahn）+ 环检测。"""
    validate_dsl(doc)
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


def execute_node(node: dict[str, Any], payload: Any) -> Any:
    """执行单个节点。payload 为所有入边数据的合并（None 表示无输入）。"""
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
        if payload is None:
            raise DslValidationError(f"transform 节点 {node['id']} 无上游输入")
        if verb == "map":
            items = payload if isinstance(payload, list) else [payload]
            op = params["op"]
            out = []
            for it in items:
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
        if verb == "filter":
            items = payload if isinstance(payload, list) else [payload]
            return [it for it in items
                    if _compare(_lookup(it, params["field"]), params["op"], params.get("value"))]
        if verb == "template":
            tpl = params["template"]
            if isinstance(payload, list):
                return [_interpolate(tpl, it) for it in payload]
            return _interpolate(tpl, payload)
        raise DslValidationError(f"未知的 transform verb: {verb}")

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
    status: str  # succeeded | skipped | failed
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
    status: str  # succeeded | failed
    doc: dict[str, Any]
    logs: list[NodeLog] = field(default_factory=list)
    output: Any = None
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "status": self.status, "dsl": self.doc,
            "output": self.output, "error": self.error,
            "created_at": self.created_at,
            "logs": [l.to_dict() for l in self.logs],
        }


def run_dsl(doc: dict[str, Any], *, run_id: str | None = None) -> RunResult:
    """编译并执行一份 DSL 文档，返回带逐步日志的运行结果。

    调度采用「节点完成后按后继边条件入队」的动态展开：
    入度为 0 的节点先入队；节点成功后逐条评估出边条件，满足者使其
    后继节点待入队计数减一，减到 0 即入队；全部入边条件都不满足的
    后继节点记为 skipped。
    """
    plan = compile_dsl(doc)
    rid = run_id or f"dsl-{uuid.uuid4().hex[:12]}"
    result = RunResult(run_id=rid, status="succeeded", doc=doc)

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
                # 合并所有入边上游输出（多入边取第一个非 None）
                incoming = [e["from"] for e in doc["edges"] if e["to"] == nid]
                payload = None
                for src in incoming:
                    up = _latest_log(result, src)
                    if up and up.status == "succeeded" and outputs.get(src) is not None:
                        payload = outputs[src]
                        break
                log.input = payload
                try:
                    out = execute_node(node, payload)
                    outputs[nid] = out
                    log.output = out
                    log.finished_at = now()
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
