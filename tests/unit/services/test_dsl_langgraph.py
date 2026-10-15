"""N1 统一编排：iteration/loop/parallel 降级为 LangGraph Send/reducer 子图。

验收形态（差分）：
* 同一逻辑工作流分别跑 DSL（经降级路径）与本文件手建的 LangGraph 参考图
  （parallel = Send 扇出 + reducer 汇聚 + 索引排序；
   iteration = 串行环；loop = 条件环），输出必须等价；
* 分发必须走降级模块（spy 断言路由）；
* 错误必须原样上抛（DslValidationError 不被包装、不被吞掉）。
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from find_yourself.services import dsl_langgraph
from find_yourself.services.dsl_canvas import (
    DslValidationError,
    execute_node,
    run_dsl,
)

# --------------------------------------------------------------------------- #
# 固件（与 test_node_library.py 同形）
# --------------------------------------------------------------------------- #

def _flow(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"version": "1", "nodes": nodes,
            "edges": edges or ([{"from": nodes[i]["id"], "to": nodes[i + 1]["id"]}
                                for i in range(len(nodes) - 1)] if len(nodes) > 1 else [])}


def _literal(value) -> dict:
    return {"id": "in1", "type": "input", "params": {"kind": "literal", "value": value}}


def _output() -> dict:
    return {"id": "out1", "type": "output"}


_UPPER_SUBFLOW = {
    "version": "1",
    "nodes": [
        {"id": "si", "type": "input"},
        {"id": "st", "type": "transform", "verb": "map", "params": {"op": "upper"}},
        {"id": "so", "type": "output"},
    ],
    "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}],
}


def _echo_subflow(tag: str) -> dict:
    return {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "st", "type": "transform", "verb": "map",
             "params": {"op": "set", "field": "tag", "value": tag}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}],
    }


# --------------------------------------------------------------------------- #
# 1. 降级模块存在 + 三个入口
# --------------------------------------------------------------------------- #

def test_lowering_module_exposes_three_runners() -> None:
    assert callable(dsl_langgraph.run_iteration_lowered)
    assert callable(dsl_langgraph.run_loop_lowered)
    assert callable(dsl_langgraph.run_parallel_lowered)


# --------------------------------------------------------------------------- #
# 2. execute_node 把三类拓扑路由进降级模块（spy 断言）
# --------------------------------------------------------------------------- #

def test_execute_node_routes_iteration_through_lowering(monkeypatch) -> None:
    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return ["SPY"]
    monkeypatch.setattr(dsl_langgraph, "run_iteration_lowered", spy)
    node = {"id": "it1", "type": "iteration", "params": {"subflow": _UPPER_SUBFLOW}}
    out = execute_node(node, ["ab", "cd"])
    assert out == ["SPY"]
    assert len(calls) == 1
    assert calls[0]["values"] == ["ab", "cd"]
    assert calls[0]["node_id"] == "it1"


def test_execute_node_routes_loop_through_lowering(monkeypatch) -> None:
    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return {"iterations": 1, "output": "x", "reason": "until_met"}
    monkeypatch.setattr(dsl_langgraph, "run_loop_lowered", spy)
    node = {"id": "lp1", "type": "loop",
            "params": {"subflow": _UPPER_SUBFLOW, "max_iterations": 3}}
    out = execute_node(node, "go")
    assert out["reason"] == "until_met"
    assert len(calls) == 1
    assert calls[0]["initial"] == "go"
    assert calls[0]["max_iterations"] == 3


def test_execute_node_routes_parallel_through_lowering(monkeypatch) -> None:
    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return ["SPY-A", "SPY-B"]
    monkeypatch.setattr(dsl_langgraph, "run_parallel_lowered", spy)
    node = {"id": "pl1", "type": "parallel", "params": {
        "branches": [{"name": "a", "inline": _echo_subflow("a")},
                     {"name": "b", "inline": _echo_subflow("b")}]}}
    out = execute_node(node, "x")
    assert out == ["SPY-A", "SPY-B"]
    assert len(calls) == 1
    assert [name for name, _doc in calls[0]["branches"]] == ["a", "b"]
    assert calls[0]["payload"] == "x"


# --------------------------------------------------------------------------- #
# 3. 差分：DSL（降级路径） vs 手建 LangGraph 参考图
# --------------------------------------------------------------------------- #

class _SeqState(TypedDict, total=False):
    values: list
    index: int
    acc: Annotated[list, operator.add]


def _reference_sequential_map(values: list, body) -> list:
    """参考实现：串行环 + 累积（iteration 的语义对照）。"""
    def worker(state: _SeqState) -> dict:
        return {"acc": [body(state["values"][state["index"]])],
                "index": state["index"] + 1}

    def router(state: _SeqState) -> str:
        return "worker" if state["index"] < len(state["values"]) else "done"

    g = StateGraph(_SeqState)
    g.add_node("worker", worker)
    g.add_edge(START, "worker")
    g.add_conditional_edges("worker", router, {"worker": "worker", "done": END})
    app = g.compile()
    out = app.invoke({"values": list(values), "index": 0, "acc": []},
                     config={"recursion_limit": 2 * len(values) + 10})
    return out["acc"]


def test_iteration_equivalent_to_reference_cycle() -> None:
    items = ["ab", "cd", "ef"]
    doc = _flow([_literal(items),
                 {"id": "it1", "type": "iteration",
                  "params": {"subflow": _UPPER_SUBFLOW}},
                 _output()])
    r = run_dsl(doc)
    assert r.status == "succeeded"
    expected = _reference_sequential_map(items, lambda v: [v.upper()])
    assert r.output == expected == [["AB"], ["CD"], ["EF"]]


def test_iteration_with_item_field_equivalent_to_reference() -> None:
    items = [{"word": "ab", "junk": 1}, {"word": "cd", "junk": 2}]
    doc = _flow([_literal(items),
                 {"id": "it1", "type": "iteration",
                  "params": {"subflow": _UPPER_SUBFLOW, "item_field": "word"}},
                 _output()])
    r = run_dsl(doc)
    assert r.status == "succeeded"
    expected = _reference_sequential_map(
        ["ab", "cd"], lambda v: [v.upper()])
    assert r.output == expected


class _ParState(TypedDict, total=False):
    payload: Any
    jobs: list
    results: Annotated[list, operator.add]
    ordered: list


def _reference_send_fanout(payload: Any, bodies: list, max_workers: int) -> list:
    """参考实现：Send 扇出 + reducer 汇聚 + 索引排序（parallel 的语义对照）。"""
    def worker(arg: dict) -> dict:
        idx, fn = arg["index"], arg["fn"]
        return {"results": [(idx, fn(payload))]}

    def fanout(state: _ParState):
        return [Send("worker", {"index": i, "fn": fn}) for i, fn in enumerate(state["jobs"])]

    def gather(state: _ParState) -> dict:
        return {"ordered": [o for _, o in sorted(state["results"], key=lambda t: t[0])]}

    g = StateGraph(_ParState)
    g.add_node("fanout", lambda s: {})
    g.add_node("worker", worker)
    g.add_node("gather", gather)
    g.add_edge(START, "fanout")
    g.add_conditional_edges("fanout", fanout, ["worker"])
    g.add_edge("worker", "gather")
    g.add_edge("gather", END)
    app = g.compile()
    out = app.invoke({"payload": payload, "jobs": list(bodies), "results": []},
                     config={"max_concurrency": max_workers})
    return out["ordered"]


def test_parallel_equivalent_to_reference_send_fanout() -> None:
    delays = {"slow": 0.30, "fast": 0.0}

    def _llm(model: str, prompt: str, params):  # noqa: ANN001 - 测试替身
        time.sleep(delays.get(params["system"], 0.0))
        return {"text": params["system"], "usage": {}}

    branches = []
    for which in ("slow", "fast"):
        sub = _echo_subflow(which)
        sub["nodes"].insert(2, {"id": "sl", "type": "llm",
                                "params": {"model": "mock", "prompt": "p",
                                           "system": which}})
        sub["edges"] = [{"from": "si", "to": "st"},
                        {"from": "st", "to": "sl"},
                        {"from": "sl", "to": "so"}]
        branches.append({"name": which, "inline": sub})
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {"branches": branches}},
                 _output()])
    r = run_dsl(doc, llm_resolver=_llm)
    assert r.status == "succeeded"
    # 参考图跑同样分支体（DSL 子流程的等价纯函数版）：声明顺序必须一致。
    bodies = [lambda p, _w=which: {"text": _w} for which in ("slow", "fast")]
    expected = _reference_send_fanout("x", bodies, max_workers=2)
    assert [b["text"] for b in r.output] == [b["text"] for b in expected] == ["slow", "fast"]


class _LoopState(TypedDict, total=False):
    current: Any
    last: Any
    iterations: int


def _reference_bounded_cycle(initial: Any, max_iter: int, body, should_stop) -> dict:
    """参考实现：条件环 + 有界计数（loop 的语义对照）。"""
    def step(state: _LoopState) -> dict:
        last = body(state["current"])
        return {"last": last, "current": last, "iterations": state["iterations"] + 1}

    def router(state: _LoopState) -> str:
        if should_stop(state["last"]):
            return "done"
        if state["iterations"] >= max_iter:
            return "done"
        return "step"

    g = StateGraph(_LoopState)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_conditional_edges("step", router, {"step": "step", "done": END})
    app = g.compile()
    out = app.invoke({"current": initial, "last": None, "iterations": 0},
                     config={"recursion_limit": 2 * max_iter + 10})
    reason = "until_met" if should_stop(out["last"]) else "max_iterations"
    return {"iterations": out["iterations"], "output": out["last"], "reason": reason}


def test_loop_equivalent_to_reference_cycle() -> None:
    # map-set 把 {"n": "0"} 逐轮拼成 01 / 011 / 0111；until 读 "0.n"（首元素 n 字段）。
    subflow = {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "st", "type": "transform", "verb": "map",
             "params": {"op": "set", "field": "n", "value": "{n}1"}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}],
    }
    doc = _flow([_literal({"n": "0"}),
                 {"id": "lp1", "type": "loop",
                  "params": {"subflow": subflow, "max_iterations": 10,
                             "until_field": "0.n", "until_op": "eq",
                             "until_value": "0111"}},
                 _output()])
    r = run_dsl(doc)
    assert r.status == "succeeded"

    def body(last):
        return [{"n": last[0]["n"] + "1"}]

    def stop(last):
        return last[0]["n"] == "0111"

    expected = _reference_bounded_cycle([{"n": "0"}], 10, body, stop)
    assert r.output == expected
    assert r.output["iterations"] == 3
    assert r.output["reason"] == "until_met"


def test_loop_max_iterations_reason_matches_reference() -> None:
    doc = _flow([_literal({"n": 0}),
                 {"id": "lp1", "type": "loop",
                  "params": {"subflow": {
                      "version": "1",
                      "nodes": [
                          {"id": "si", "type": "input"},
                          {"id": "st", "type": "transform", "verb": "map",
                           "params": {"op": "set", "field": "n", "value": "{n}1"}},
                          {"id": "so", "type": "output"}],
                      "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}]},
                      "max_iterations": 3}},
                 _output()])
    r = run_dsl(doc)
    assert r.status == "succeeded"
    assert r.output["iterations"] == 3
    assert r.output["reason"] == "max_iterations"


# --------------------------------------------------------------------------- #
# 4. 错误原样上抛：分支失败必须是 DslValidationError 本体
# --------------------------------------------------------------------------- #

def test_lowered_parallel_propagates_original_error_type() -> None:
    good = _echo_subflow("ok")
    broken = {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "sx", "type": "http_request",
             "params": {"url": "ftp://x/y", "method": "GET"}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "sx"},
                  {"from": "sx", "to": "so"}]}
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {
                     "branches": [{"name": "ok", "inline": good},
                                  {"name": "broken", "inline": broken}]}},
                 _output()])
    r = run_dsl(doc)
    assert r.status == "failed"
    # 外层 run_dsl 把节点异常记成 failed；直接调 execute_node 时必须是原类型。
    with pytest.raises(DslValidationError):
        execute_node(doc["nodes"][1], "x")


# --------------------------------------------------------------------------- #
# B4 流式钩子：降级核 on_event（对标 run_dsl 的节点事件模式）
# --------------------------------------------------------------------------- #

def test_lowered_runners_emit_lifecycle_events() -> None:
    from find_yourself.services import dsl_langgraph

    events: list[tuple[str, dict]] = []

    def _runner(value, label):
        return f"{value}!"

    dsl_langgraph.run_iteration_lowered(
        node_id="it1", values=["a", "b"], runner=_runner,
        on_event=lambda kind, payload: events.append((kind, payload)),
    )
    kinds = [k for k, _ in events]
    # 启动 + 每个 item 的开始/结束 + 完成。
    assert kinds[0] == "iteration_started"
    assert kinds.count("item_started") == 2
    assert kinds.count("item_finished") == 2
    assert kinds[-1] == "iteration_finished"
    assert events[-1][1]["outputs"] == ["a!", "b!"]


def test_lowered_parallel_emits_branch_events_including_failures() -> None:
    from find_yourself.services import dsl_langgraph

    events: list[tuple[str, dict]] = []

    def _runner(doc, value, label):
        if doc.get("boom"):
            raise ValueError("branch boom")
        return f"ok:{value}"

    with pytest.raises(ValueError, match="branch boom"):
        dsl_langgraph.run_parallel_lowered(
            node_id="pl1",
            branches=[("ok", {}), ("bad", {"boom": True})],
            payload="x", max_workers=2, runner=_runner,
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
    kinds = [k for k, _ in events]
    assert "parallel_started" in kinds
    assert "branch_failed" in kinds
    assert "parallel_finished" not in kinds  # 失败即停，无完成事件（不造假完成）


def test_lowered_loop_emits_per_round_events() -> None:
    from find_yourself.services import dsl_langgraph

    events: list[tuple[str, dict]] = []

    def _should_stop(last):
        return last >= 2

    def _runner(value, label):
        return value + 1

    dsl_langgraph.run_loop_lowered(
        node_id="lp1", initial=0, max_iterations=3,
        should_stop=_should_stop, runner=_runner,
        on_event=lambda kind, payload: events.append((kind, payload)),
    )
    rounds = [p["round"] for k, p in events if k == "round_finished"]
    assert rounds == [1, 2]
    assert events[-1][0] == "loop_finished"
    assert events[-1][1]["reason"] == "until_met"
