"""N1 统一编排：iteration / loop / parallel 降级为 LangGraph 子图。

本模块是三类拓扑节点的**唯一执行路径**（``services/dsl_canvas.py`` 的
``execute_node`` 分发到此）。每类拓扑被降级为一张真实的 LangGraph
``StateGraph``，与手写的 ``Send`` 扇出 / reducer 汇聚 / 条件环语义对齐
（差分验收见 ``tests/unit/services/test_dsl_langgraph.py``）：

* ``iteration`` —— 串行环：逐项串行跑子流程，输出与输入同序。刻意**不**
  用 ``Send`` 并发：DSL 契约规定 iteration 是单线程串行 map（有序、可逐步
  调试），并发会改变可观测语义。
* ``loop`` —— 条件环：``until`` 满足或达到 ``max_iterations`` 即停，
  返回 ``{"iterations", "output", "reason"}``。
* ``parallel`` —— ``Send`` 扇出 + reducer 汇聚：各命名分支真并发，结果按
  **分支声明顺序**排序后返回；任一分支失败即本节点失败（异常原样上抛，
  绝不包装、绝不把失败分支混进汇聚结果）。

分层红线（N7/D3R）：本模块**只依赖标准库 + langgraph**，绝不 import
``dsl_canvas``（否则与调用方形成 L2 层内新环，门禁会红）。DSL 语义
（子流程执行、until 判定、校验错误类型）由调用方以回调形式注入：

* ``runner(value, label)`` —— 跑一轮子流程并返回输出；失败时抛出 DSL
  自身的异常类型（调用方保证），本模块只负责透传。
* ``should_stop(last_output)`` —— loop 的 until 判定（纯函数）。
* ``on_event(kind, payload)`` —— B4 流式钩子（可选）：worker 生命周期事件
  （started/finished + 输出快照），失败只发 ``*_failed`` 不发完成事件。
  注意 ``execute_node`` 暂不透传本钩子：子流程事件进入外层 run_dsl 需要
  命名空间扇入设计（否则内外事件混流），属后续项。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

#: 子流程单轮执行器：``(value, label) -> output``，失败抛 DSL 原生异常。
SubflowRunner = Callable[[Any, str], Any]

#: loop 终止判定：``(last_output) -> bool``，必须是纯函数。
StopPredicate = Callable[[Any], bool]

#: B4 流式钩子：``(kind, payload) -> None``，对标 run_dsl 的节点事件模式。
#: 回调内抛错不拦截执行（由调用方保证不抛；线程 worker 内抛错会污染汇聚）。
#: execute_node 暂不透传（子流程事件需命名空间扇入设计，见模块文档）。
OnEvent = Callable[[str, dict[str, Any]], None] | None


def _emit(on_event: OnEvent, kind: str, payload: dict[str, Any]) -> None:
    if on_event is not None:
        on_event(kind, payload)


# --------------------------------------------------------------------------- #
# iteration：串行环
# --------------------------------------------------------------------------- #

class _IterState(TypedDict, total=False):
    values: list
    index: int
    acc: Annotated[list, operator.add]


def run_iteration_lowered(
    *,
    node_id: str,
    values: list[Any],
    runner: SubflowRunner,
    on_event: OnEvent = None,
) -> list[Any]:
    """串行 map：``values[i]`` 依次跑子流程，输出同序列表。"""
    _emit(on_event, "iteration_started", {"node_id": node_id, "count": len(values)})
    if not values:
        _emit(on_event, "iteration_finished",
              {"node_id": node_id, "outputs": [], "rounds": 0})
        return []

    def worker(state: _IterState) -> dict:
        idx = state["index"]
        _emit(on_event, "item_started", {"node_id": node_id, "index": idx})
        out = runner(state["values"][idx], f"item{idx}")
        _emit(on_event, "item_finished",
              {"node_id": node_id, "index": idx, "output": out})
        return {"acc": [out], "index": idx + 1}

    def router(state: _IterState) -> str:
        return "worker" if state["index"] < len(state["values"]) else "done"

    graph = StateGraph(_IterState)
    graph.add_node("worker", worker)
    graph.add_edge(START, "worker")
    graph.add_conditional_edges("worker", router, {"worker": "worker", "done": END})
    app = graph.compile()
    final = app.invoke(
        {"values": list(values), "index": 0, "acc": []},
        config={"recursion_limit": 2 * len(values) + 10,
                "run_name": f"{node_id}:iteration"},
    )
    outputs = list(final["acc"])
    _emit(on_event, "iteration_finished",
          {"node_id": node_id, "outputs": outputs, "rounds": len(outputs)})
    return outputs


# --------------------------------------------------------------------------- #
# loop：条件环
# --------------------------------------------------------------------------- #

class _LoopState(TypedDict, total=False):
    current: Any
    last: Any
    iterations: int


def run_loop_lowered(
    *,
    node_id: str,
    initial: Any,
    max_iterations: int,
    should_stop: StopPredicate,
    runner: SubflowRunner,
    on_event: OnEvent = None,
) -> dict[str, Any]:
    """有界条件循环，返回 ``{"iterations", "output", "reason"}``。

    ``reason`` 按解释器语义判定：每轮跑完即判 ``until``，命中为
    ``"until_met"``，否则跑满 ``max_iterations``。``should_stop`` 是纯函数，
    故收尾时对终值再判一次与轮内判定等价。
    """
    if max_iterations < 1:
        raise ValueError(f"节点 {node_id} loop.max_iterations 至少为 1")

    _emit(on_event, "loop_started",
          {"node_id": node_id, "max_iterations": max_iterations})

    def step(state: _LoopState) -> dict:
        round_no = state["iterations"] + 1
        _emit(on_event, "round_started", {"node_id": node_id, "round": round_no})
        last = runner(state["current"], f"iter{state['iterations']}")
        _emit(on_event, "round_finished",
              {"node_id": node_id, "round": round_no, "output": last})
        return {"last": last, "current": last,
                "iterations": state["iterations"] + 1}

    def router(state: _LoopState) -> str:
        if should_stop(state["last"]):
            return "done"
        if state["iterations"] >= max_iterations:
            return "done"
        return "step"

    graph = StateGraph(_LoopState)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_conditional_edges("step", router, {"step": "step", "done": END})
    app = graph.compile()
    final = app.invoke(
        {"current": initial, "last": None, "iterations": 0},
        config={"recursion_limit": 2 * max_iterations + 10,
                "run_name": f"{node_id}:loop"},
    )
    reason = "until_met" if should_stop(final["last"]) else "max_iterations"
    _emit(on_event, "loop_finished",
          {"node_id": node_id, "iterations": final["iterations"],
           "reason": reason, "output": final["last"]})
    return {"iterations": final["iterations"], "output": final["last"],
            "reason": reason}


# --------------------------------------------------------------------------- #
# parallel：Send 扇出 + reducer 汇聚
# --------------------------------------------------------------------------- #

class _ParState(TypedDict, total=False):
    payload: Any
    jobs: list
    results: Annotated[list, operator.add]
    ordered: list


def run_parallel_lowered(
    *,
    node_id: str,
    branches: list[tuple[str, Any]],
    payload: Any,
    max_workers: int,
    runner: Callable[[Any, Any, str], Any],
    on_event: OnEvent = None,
) -> list[Any]:
    """``Send`` 扇出各命名分支，真并发；汇聚按声明顺序排序返回。

    ``runner(doc, value, label)`` 跑单个分支的子流程文档。分支异常原样上抛
    （LangGraph 不包装 worker 异常），调用方看到的仍是 DSL 原生错误。
    ``max_workers`` 映射为 ``max_concurrency``。
    """
    if not branches:
        _emit(on_event, "parallel_finished",
              {"node_id": node_id, "outputs": [], "branches": 0})
        return []

    def worker(arg: dict) -> dict:
        idx = arg["index"]
        _emit(on_event, "branch_started",
              {"node_id": node_id, "index": idx, "name": arg["name"]})
        try:
            out = runner(arg["doc"], payload, f"branch{idx}:{arg['name']}")
        except Exception as exc:
            _emit(on_event, "branch_failed",
                  {"node_id": node_id, "index": idx, "name": arg["name"],
                   "error": f"{type(exc).__name__}: {exc}"})
            raise
        _emit(on_event, "branch_finished",
              {"node_id": node_id, "index": idx, "name": arg["name"], "output": out})
        return {"results": [(idx, out)]}

    def fanout(state: _ParState) -> list[Send]:
        return [Send("worker", {"index": i, "name": name, "doc": doc})
                for i, (name, doc) in enumerate(state["jobs"])]

    def gather(state: _ParState) -> dict:
        return {"ordered": [o for _, o in sorted(state["results"],
                                                 key=lambda t: t[0])]}

    graph = StateGraph(_ParState)
    graph.add_node("fanout", lambda _s: {})
    graph.add_node("worker", worker)
    graph.add_node("gather", gather)
    graph.add_edge(START, "fanout")
    graph.add_conditional_edges("fanout", fanout, ["worker"])
    graph.add_edge("worker", "gather")
    graph.add_edge("gather", END)
    app = graph.compile()
    _emit(on_event, "parallel_started",
          {"node_id": node_id,
           "branches": [name for name, _doc in branches]})
    final = app.invoke(
        {"payload": payload, "jobs": list(branches), "results": []},
        config={"max_concurrency": max(1, max_workers),
                "run_name": f"{node_id}:parallel"},
    )
    ordered = list(final["ordered"])
    _emit(on_event, "parallel_finished",
          {"node_id": node_id, "outputs": ordered, "branches": len(branches)})
    return ordered
