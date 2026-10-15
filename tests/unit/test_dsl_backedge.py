"""B3 图级循环：显式回边（back_edge）+ 调度重入 + max_rounds 兜底。

语法：边可选 ``{"back_edge": true, "max_rounds": N}``（缺省 max_rounds=10）。
语义：
* 编译：回边不计入度（Kahn 只走前向边）；**未标记**的纯前向环仍拒绝；
* 运行：回边条件通过（无条件恒过）且轮数未超时重入目标节点，每轮一步；
  轮数耗尽 = 作者声明的上界（期望终止），正常收尾、不失败；
* 全局 recursion_limit / steps 闸门不变（系统级兜底）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    BACKEDGE_DEFAULT_MAX_ROUNDS,
    DslValidationError,
    compile_dsl,
    run_dsl,
)


def _counter_doc(max_rounds=None, cond=None) -> dict:
    """input("0") → 卷积节点（n 后追加 "1"）→ output；卷积→卷积回边。

    回边在 ``doc["edges"]`` 里排第一：payload 按“首个非 None 上游”合并，
    自环排第一时重入轮次吃上一轮输出（累加），首轮吃 input。顺序即语义。
    """
    edge = {"from": "t", "to": "t", "back_edge": True}
    if max_rounds is not None:
        edge["max_rounds"] = max_rounds
    if cond is not None:
        edge["condition"] = cond
    return {
        "version": "1",
        "nodes": [
            {"id": "in1", "type": "input",
             "params": {"kind": "literal", "value": {"n": "0"}}},
            {"id": "t", "type": "transform", "verb": "map",
             "params": {"op": "set", "field": "n", "value": "{n}1"}},
            {"id": "out1", "type": "output"},
        ],
        "edges": [
            edge,
            {"from": "in1", "to": "t"},
            {"from": "t", "to": "out1"},
        ],
    }


def _t_logs(result, nid="t"):
    return [log for log in result.logs if log.node_id == nid and log.status == "succeeded"]


def test_declared_back_edge_compiles() -> None:
    plan = compile_dsl(_counter_doc(max_rounds=3))
    assert plan.order == ["in1", "t", "out1"]


def test_unmarked_cycle_still_rejected() -> None:
    doc = _counter_doc()
    doc["edges"][0] = {"from": "t", "to": "t"}
    with pytest.raises(DslValidationError, match="环"):
        compile_dsl(doc)


def test_back_edge_rejects_bad_params() -> None:
    doc = _counter_doc()
    doc["edges"][0] = {"from": "t", "to": "t", "back_edge": "yes"}
    with pytest.raises(DslValidationError, match="back_edge"):
        compile_dsl(doc)
    doc2 = _counter_doc()
    doc2["edges"][0] = {"from": "t", "to": "t", "back_edge": True, "max_rounds": 0}
    with pytest.raises(DslValidationError, match="max_rounds"):
        compile_dsl(doc2)


def test_back_edge_reruns_until_max_rounds_then_exits_normally() -> None:
    r = run_dsl(_counter_doc(max_rounds=3))
    assert r.status == "succeeded", r.error
    # 初轮 + 3 轮重入 = 4 次执行；每轮追加一个 "1"："0" → "01111"。
    assert len(_t_logs(r)) == 4
    assert r.output == [{"n": "01111"}]


def test_back_edge_default_max_rounds() -> None:
    assert BACKEDGE_DEFAULT_MAX_ROUNDS == 10
    r = run_dsl(_counter_doc())
    assert r.status == "succeeded", r.error
    assert len(_t_logs(r)) == 1 + BACKEDGE_DEFAULT_MAX_ROUNDS


def test_back_edge_condition_gates_rerun() -> None:
    # 边条件语义与前向边一致：通过才触发。n != "011" 时重入：
    # 初轮 "01"（过）→ 第 2 轮 "011"（不过）→ 停，共 2 次执行。
    r = run_dsl(_counter_doc(
        max_rounds=10,
        cond={"field": "0.n", "op": "ne", "value": "011"},
    ))
    assert r.status == "succeeded", r.error
    assert len(_t_logs(r)) == 2
    assert r.output == [{"n": "011"}]
