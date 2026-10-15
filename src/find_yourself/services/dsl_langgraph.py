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
) -> list[Any]:
    """串行 map：``values[i]`` 依次跑子流程，输出同序列表。"""
    if not values:
        return []

    def worker(state: _IterState) -> dict:
        idx = state["index"]
        out = runner(state["values"][idx], f"item{idx}")
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
    return list(final["acc"])


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
) -> dict[str, Any]:
    """有界条件循环，返回 ``{"iterations", "output", "reason"}``。

    ``reason`` 按解释器语义判定：每轮跑完即判 ``until``，命中为
    ``"until_met"``，否则跑满 ``max_iterations``。``should_stop`` 是纯函数，
    故收尾时对终值再判一次与轮内判定等价。
    """
    if max_iterations < 1:
        raise ValueError(f"节点 {node_id} loop.max_iterations 至少为 1")

    def step(state: _LoopState) -> dict:
        last = runner(state["current"], f"iter{state['iterations']}")
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
) -> list[Any]:
    """``Send`` 扇出各命名分支，真并发；汇聚按声明顺序排序返回。

    ``runner(doc, value, label)`` 跑单个分支的子流程文档。分支异常原样上抛
    （LangGraph 不包装 worker 异常），调用方看到的仍是 DSL 原生错误。
    ``max_workers`` 映射为 ``max_concurrency``。
    """
    if not branches:
        return []

    def worker(arg: dict) -> dict:
        idx = arg["index"]
        out = runner(arg["doc"], payload, f"branch{idx}:{arg['name']}")
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
    final = app.invoke(
        {"payload": payload, "jobs": list(branches), "results": []},
        config={"max_concurrency": max(1, max_workers),
                "run_name": f"{node_id}:parallel"},
    )
    return list(final["ordered"])
