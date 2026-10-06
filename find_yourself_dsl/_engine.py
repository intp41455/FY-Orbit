"""确定性执行引擎（编译 + 动词执行 + 调度），与平台 ``dsl_canvas.run_dsl``
同语义的独立镜像。

三块职责：

1. :func:`compile_document` —— 拓扑排序（Kahn）+ 环检测。
2. :func:`execute_node` —— 受限动词集的确定性实现（纯函数，无 LLM、无 IO）；
   ``agent`` 未注入解析器时诚实失败；``confirm`` / ``approval`` 无裁决读取器
   时抛 :class:`~find_yourself_dsl._errors.DslSuspended`（挂起 ≠ 失败）。
3. :func:`run` —— 调度器：入度 0 先入队，节点成功后按出边条件动态入队，
   条件不满足的后继按「跳过传播」记 skipped（含 requeue），全确定性。

与平台的差异（如实注明，仅两处，均不影响输出语义）：
* 运行日志的时间戳取本机时钟（平台同）；除此之外无任何非确定性。
* ``agent`` / ``confirm`` / ``approval`` 三个需要平台注入点（解析器、裁决
  读取器）的动词：独立运行库没有注入层——前两者按「诚实失败/挂起」处理，
  ``agent`` 可经 :func:`run` 的 ``agent_resolver`` 注入（与平台同名同签名）。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ._errors import DslSuspended, DslValidationError
from ._graph import dsl_digest, validate_document
from ._schema import INPUT_KINDS, OUTPUT_FORMATS, TRANSFORM_VERBS

__all__ = ["CompiledPlan", "NodeLog", "FlowResult", "compile_document",
           "execute_node", "run", "AgentResolver"]

#: 注入 Agent 解析器的签名：``(agent_name, payload, params) -> Any``（与平台同）。
AgentResolver = Callable[[str, Any, dict[str, Any]], Any]


# ---------------------------------------------------------------------------
# 编译：拓扑排序 + 环检测
# ---------------------------------------------------------------------------


@dataclass
class CompiledPlan:
    """编译产物：拓扑序 + 邻接表 + 原始入度（调度器按出边条件动态入队）。"""

    order: list[str]
    adjacency: dict[str, list[dict[str, Any]]]
    indegree: dict[str, int]
    nodes: dict[str, dict[str, Any]]


def compile_document(doc: dict[str, Any]) -> CompiledPlan:
    """校验 + Kahn 拓扑排序 + 环检测（与平台 ``compile_dsl`` 同算法）。"""
    validate_document(doc)
    nodes = {n["id"]: n for n in doc["nodes"]}
    adjacency: dict[str, list[dict[str, Any]]] = {nid: [] for nid in nodes}
    indegree = {nid: 0 for nid in nodes}
    for e in doc["edges"]:
        adjacency[e["from"]].append(e)
        indegree[e["to"]] += 1
    pristine = dict(indegree)

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
                        indegree=pristine, nodes=nodes)


# ---------------------------------------------------------------------------
# 确定性动词实现（与平台同语义的纯函数）
# ---------------------------------------------------------------------------


def _as_items(payload: Any) -> list[Any]:
    """列表载荷原样返回；标量载荷包成单元素列表。"""
    return payload if isinstance(payload, list) else [payload]


_TEMPLATE_RE = re.compile(r"\{([A-Za-z0-9_.\[\]]+)\}")


def _interpolate(template: str, item: Any) -> str:
    def repl(m: "re.Match[str]") -> str:
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


def _exec_map(params: dict[str, Any], payload: Any) -> Any:
    out = []
    for it in _as_items(payload):
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


def _exec_filter(params: dict[str, Any], payload: Any) -> Any:
    return [it for it in _as_items(payload)
            if _compare(_lookup(it, params["field"]), params["op"],
                        params.get("value"))]


def _exec_template(params: dict[str, Any], payload: Any) -> Any:
    tpl = params["template"]
    if isinstance(payload, list):
        return [_interpolate(tpl, it) for it in payload]
    return _interpolate(tpl, payload)


def _exec_branch(params: dict[str, Any], payload: Any) -> Any:
    matched = _compare(_lookup(payload, params["field"]), params["op"],
                       params.get("value"))
    return {"branch": params["then_label"] if matched else params["else_label"],
            "value": payload}


def _exec_aggregate(node_id: str, params: dict[str, Any], payload: Any) -> Any:
    op = params["op"]
    items = _as_items(payload)
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
                f"节点 {node_id} aggregate.op={op} 需要数值，收到 {v!r}")
        nums.append(v)
    if not nums:
        raise DslValidationError(f"节点 {node_id} aggregate.op={op} 需要至少一个数值")
    if op == "sum":
        return sum(nums)
    if op == "min":
        return min(nums)
    if op == "max":
        return max(nums)
    return sum(nums) / len(nums)


def _exec_merge(params: dict[str, Any], payload: Any,
                upstreams: list[Any]) -> Any:
    mode = params["mode"]
    streams = list(upstreams) if upstreams else _as_items(payload)
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


def _exec_artifact(params: dict[str, Any], payload: Any) -> Any:
    return {"artifact": params["name"],
            "kind": params.get("kind", "generic"),
            "content": payload}


def execute_node(node: dict[str, Any], payload: Any, *,
                 upstreams: list[Any] | None = None,
                 agent_resolver: AgentResolver | None = None,
                 execution_id: str | None = None,
                 dsl_digest_value: str = "") -> Any:
    """执行单个节点（与平台 ``execute_node`` 同语义）。payload 为入边合并输入。"""
    ntype = node["type"]
    params = node.get("params", {}) or {}
    node_id = node["id"]

    if ntype == "input":
        kind = params.get("kind", "literal")
        if kind == "text_lines":
            text = params.get("value", "")
            if not isinstance(text, str):
                raise DslValidationError(
                    f"input 节点 {node_id} text_lines.value 必须是字符串")
            return [line for line in text.splitlines() if line.strip()]
        if kind == "literal":
            return params.get("value")
        raise DslValidationError(
            f"input 节点 {node_id} kind 必须是 {INPUT_KINDS}")

    if ntype == "transform":
        verb = node["verb"]
        if verb not in TRANSFORM_VERBS:
            raise DslValidationError(
                f"transform 节点 {node_id} 的 verb 不在受限动词集内: {verb!r}")
        if payload is None:
            raise DslValidationError(f"transform 节点 {node_id} 无上游输入")
        if verb == "map":
            return _exec_map(params, payload)
        if verb == "filter":
            return _exec_filter(params, payload)
        if verb == "template":
            return _exec_template(params, payload)
        if verb == "branch":
            return _exec_branch(params, payload)
        if verb == "aggregate":
            return _exec_aggregate(node_id, params, payload)
        if verb == "merge":
            return _exec_merge(params, payload, list(upstreams or []))
        if verb == "artifact":
            return _exec_artifact(params, payload)
        if verb == "agent":
            if agent_resolver is None:
                raise DslValidationError(
                    f"节点 {node_id} 的 agent 动词需要注入 Agent 解析器"
                    "（find_yourself_dsl.run(doc, agent_resolver=...)）；"
                    "未注入时本运行库不执行 Agent 调用")
            return agent_resolver(params["agent"], payload, dict(params))
        # confirm / approval：独立运行库无裁决读取器 → 必定挂起（控制流信号）。
        if verb == "confirm":
            raise DslSuspended(
                checkpoint=f"{execution_id or '-'}#{node_id}",
                dsl_digest=dsl_digest_value,
                context={"prompt": params["prompt"],
                         "role": params.get("role", ""), "node_id": node_id},
                options=[{"value": "approve", "label": "批准"},
                         {"value": "reject", "label": "驳回"}],
                node_id=node_id,
            )
        if verb == "approval":
            raise DslSuspended(
                checkpoint=f"{execution_id or '-'}#{node_id}",
                dsl_digest=dsl_digest_value,
                context={
                    "approval": {
                        "operation": params["op"],
                        "target_id": params.get("target_id"),
                        "reason": params.get("reason", ""),
                        "rollback": params.get("rollback", ""),
                        "payload": payload,
                    },
                    "node_id": node_id,
                },
                options=[{"value": "approve", "label": "批准"},
                         {"value": "reject", "label": "驳回"}],
                node_id=node_id,
            )
        raise DslValidationError(
            f"transform 节点 {node_id} 的 verb 未接实现: {verb!r}")

    if ntype == "output":
        fmt = params.get("format", "json")
        if fmt == "text":
            if isinstance(payload, list):
                return "\n".join(str(x) for x in payload)
            return "" if payload is None else str(payload)
        if fmt == "json":
            return payload
        raise DslValidationError(
            f"output 节点 {node_id} format 必须是 {OUTPUT_FORMATS}")

    raise DslValidationError(f"未知节点类型: {ntype}")


# ---------------------------------------------------------------------------
# 调度器：与平台 run_dsl 同语义（拓扑波次 + 按边条件入队的动态展开）
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
            "node_id": self.node_id, "node_type": self.node_type,
            "verb": self.verb, "status": self.status, "input": self.input,
            "output": self.output, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


@dataclass
class FlowResult:
    run_id: str
    status: str  # succeeded | failed
    doc: dict[str, Any]
    logs: list[NodeLog] = field(default_factory=list)
    output: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "status": self.status, "dsl": self.doc,
                "output": self.output, "error": self.error,
                "logs": [log.to_dict() for log in self.logs]}


def _latest_log(logs: list[NodeLog], node_id: str) -> NodeLog | None:
    for log in reversed(logs):
        if log.node_id == node_id:
            return log
    return None


def run(doc: dict[str, Any] | None = None, *, run_id: str | None = None,
        agent_resolver: AgentResolver | None = None) -> FlowResult:
    """执行一份 DSL 文档（缺省执行**当前构造目标**的图）。

    调度与平台 ``run_dsl`` 同语义：入度 0 的节点先入队；节点成功后逐条评估
    出边条件，满足者使其后继待入队计数减一，减到 0 即入队；全部入边条件都
    不满足的后继记 skipped（含跳过传播与 requeue）。

    :raises DslSuspended: 走到 ``confirm`` / ``approval`` 时原样穿透
        （控制流信号，绝不降级成 failed）。
    """
    if doc is None:
        from ._graph import to_document
        doc = to_document()
    plan = compile_document(doc)
    rid = run_id or f"dsl-{uuid.uuid4().hex[:12]}"
    result = FlowResult(run_id=rid, status="succeeded", doc=doc)
    digest = dsl_digest(doc)

    outputs: dict[str, Any] = {}
    pending = dict(plan.indegree)
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
                queue.sort(key=lambda nid: plan.order.index(nid))
                nid = queue.pop(0)
                node = plan.nodes[nid]
                log = NodeLog(node_id=nid, node_type=node["type"],
                              verb=node.get("verb"), status="succeeded",
                              started_at=now())
                result.logs.append(log)
                incoming = [e["from"] for e in doc["edges"] if e["to"] == nid]
                payload = None
                upstreams: list[Any] = []
                for src in incoming:
                    up = _latest_log(result.logs, src)
                    if up and up.status == "succeeded":
                        upstreams.append(outputs.get(src))
                        if payload is None and outputs.get(src) is not None:
                            payload = outputs[src]
                log.input = payload
                try:
                    out = execute_node(node, payload, upstreams=upstreams,
                                       agent_resolver=agent_resolver,
                                       execution_id=rid,
                                       dsl_digest_value=digest)
                    outputs[nid] = out
                    log.output = out
                    log.finished_at = now()
                except DslSuspended:
                    log.status = "suspended"
                    log.finished_at = now()
                    result.status = "suspended"
                    result.error = None
                    raise
                except Exception as exc:
                    log.status = "failed"
                    log.error = str(exc)
                    log.finished_at = now()
                    result.status = "failed"
                    result.error = f"节点 {nid} 执行失败: {exc}"
                    continue
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
            # 跳过传播 + requeue（与平台同语义）
            requeued = False
            for nid in plan.order:
                if _latest_log(result.logs, nid) is not None:
                    continue
                node = plan.nodes[nid]
                result.logs.append(NodeLog(
                    node_id=nid, node_type=node["type"], verb=node.get("verb"),
                    status="skipped", started_at=now(), finished_at=now()))
                for edge in plan.adjacency[nid]:
                    dst = edge["to"]
                    pending[dst] -= 1
                    if pending[dst] == 0 and triggered[dst] and \
                            _latest_log(result.logs, dst) is None:
                        queue.append(dst)
                        requeued = True
            if not requeued:
                break
        if result.status == "succeeded":
            outs = [outputs[n["id"]] for n in doc["nodes"]
                    if n["type"] == "output" and n["id"] in outputs]
            result.output = outs[-1] if outs else outputs.get(plan.order[-1])
    except DslSuspended:
        raise
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
    return result
