"""B5 运行时首切片：CoordinationSpec 统一执行（N8 地基）。

同一份 spec 驱动执行——拓扑即数据，不是注释：

* ``sequential``：按 members 顺序串行，前者输出进后者输入；
* ``parallel``：ThreadPool 全员并发，结果按 members **声明序**汇聚
  （与 DSL ``parallel`` 同一语义）；
* ``pipeline``：按 members 顺序流经（每成员即一站；stages 数量语义
  由校验层保证 ≥2，本层按成员序执行）；
* ``map``：输入 list 按成员轮转扇出（真并发），按输入序汇聚；
* ``delegation``：沿 parent_links 自顶向下委派（同层并发），子拿父输出；
* ``market``：全员并行出价 + ``select`` 回调裁决（无 select 直接拒绝）；
* 其他形态暂不支持，抛 ``UnsupportedTopology``（不静默降级成别的拓扑跑——
  那是假执行）。

handler 签名 ``(member, data) -> output``；异常原样上抛（调用方审计）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .coordination import CoordinationSpec, CoordinationValidationError

Handler = Callable[[str, Any], Any]
#: market 裁决回调：``bids: list[{"member","output"}] -> 命中的 bid``（必须是其中之一）。
SelectBid = Callable[[list[dict[str, Any]]], dict[str, Any]]


class UnsupportedTopology(Exception):
    """该分解形态暂无执行器：spec 合法，但运行时还没接。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "unsupported_topology"


def _sequential(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    current = initial
    for member in spec.members:
        current = handler(member, current)
        results.append({"member": member, "output": current})
    return {"final": current, "results": results}


def _parallel(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    members = list(spec.members)
    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        futures = [pool.submit(handler, member, initial) for member in members]
        outputs = [fut.result() for fut in futures]
    # 声明序汇聚（完成先后不影响顺序）。
    results = [{"member": member, "output": out}
               for member, out in zip(members, outputs)]
    return {"final": [r["output"] for r in results], "results": results}


def _pipeline(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    return _sequential(spec, handler, initial)


def _map(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    """输入 list 按成员轮转扇出，真并发，按输入序汇聚。"""
    if not isinstance(initial, list):
        raise CoordinationValidationError(
            "map 分解的输入必须是 items 列表（轮转扇出无列表扇什么）")
    members = list(spec.members)
    if not members:
        raise CoordinationValidationError("map 分解需要至少一个成员")
    items = list(initial)
    assignments = [(items[i], members[i % len(members)]) for i in range(len(items))]
    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        futures = [pool.submit(handler, member, item) for item, member in assignments]
        outputs = [fut.result() for fut in futures]
    results = [{"member": member, "item": item, "output": out}
               for (item, member), out in zip(assignments, outputs)]
    return {"final": [r["output"] for r in results], "results": results}


def _delegation(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    """沿 parent_links 自顶向下委派：同层并发，子拿父输出。"""
    links = dict(spec.parent_links or {})
    children: dict[str, list[str]] = {}
    for child, parent in links.items():
        children.setdefault(parent, []).append(child)
    roots = [m for m in spec.members if m not in links]
    outputs: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    frontier: list[tuple[str, Any]] = [(root, initial) for root in roots]
    while frontier:
        with ThreadPoolExecutor(max_workers=max(1, len(frontier))) as pool:
            futures = [(member, pool.submit(handler, member, data))
                       for member, data in frontier]
            level_out = [(member, fut.result()) for member, fut in futures]
        nxt: list[tuple[str, Any]] = []
        for member, out in level_out:
            outputs[member] = out
            results.append({"member": member, "output": out})
            for child in children.get(member, []):
                nxt.append((child, out))
        frontier = nxt
    return {"final": dict(outputs), "results": results}


def _market(spec: CoordinationSpec, handler: Handler, initial: Any,
            select: SelectBid | None) -> dict[str, Any]:
    """全员并行出价 + select 裁决。无 select 直接拒绝（不静默内定冠军）。"""
    if select is None:
        raise ValueError("market 分解必须传入 select 回调（bids -> 命中的 bid）")
    members = list(spec.members)
    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        futures = [pool.submit(handler, member, initial) for member in members]
        outputs = [fut.result() for fut in futures]
    bids = [{"member": member, "output": out}
            for member, out in zip(members, outputs)]
    winner = select(bids)
    if not isinstance(winner, dict) or winner not in bids:
        raise ValueError("select 必须返回 bids 中的一条（不能凭空造冠军）")
    return {"winner": winner, "bids": bids, "results": bids, "final": winner["output"]}


_EXECUTORS = {
    "sequential": _sequential,
    "parallel": _parallel,
    "pipeline": _pipeline,
    "map": _map,
    "delegation": _delegation,
    "market": _market,
}


def run_spec(spec: CoordinationSpec, handler: Handler, initial: Any,
             select: SelectBid | None = None) -> dict[str, Any]:
    """执行一份已校验的 spec，返回 ``{"final", "results"}``（market 另带 winner/bids）."""
    mode = spec.decomposition.mode
    executor = _EXECUTORS.get(mode)
    if executor is None:
        raise UnsupportedTopology(
            f"分解形态 {mode!r} 暂无执行器（已有：{sorted(_EXECUTORS)}）")
    if mode == "market":
        return _market(spec, handler, initial, select)
    return executor(spec, handler, initial)
