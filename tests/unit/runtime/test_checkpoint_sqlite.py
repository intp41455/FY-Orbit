"""T6-B 持久化检查点测试（补 G1）——验收剧本 V3 的服务级实现。

V3 剧本：进程崩溃（kill -9）→ 重启 → 从持久化检查点恢复；已完成节点
**不重跑**（不重复计费，红线 2）；产物与崩溃前一致。
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from find_yourself.runtime.checkpoint_sqlite import SqliteCheckpointer
from find_yourself.runtime.graph import compile_task_graph

CALLS: list[str] = []
FAIL = {"on": False}


class St(TypedDict, total=False):
    x: int


def _node_a(state: St) -> dict:
    CALLS.append("a")
    return {"x": state.get("x", 0) + 1}


def _node_b(state: St) -> dict:
    if FAIL["on"]:
        raise RuntimeError("boom-s4: simulated crash mid-run")
    CALLS.append("b")
    return {"x": state["x"] + 10}


def _build() -> StateGraph:
    g = StateGraph(St)
    g.add_node("a", _node_a)
    g.add_node("b", _node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g


@pytest.fixture(autouse=True)
def _reset_call_log():
    CALLS.clear()
    FAIL["on"] = False
    yield
    CALLS.clear()
    FAIL["on"] = False


def test_checkpoint_roundtrip_across_restart(tmp_path):
    """写入 → 「重启」（全新实例读同一文件）→ 最新检查点可读回。"""
    db = str(tmp_path / "ck.db")
    app1 = _build().compile(checkpointer=SqliteCheckpointer(db))
    cfg = {"configurable": {"thread_id": "th-rt"}}
    out = app1.invoke({"x": 0}, config=cfg)
    assert out["x"] == 11

    saver2 = SqliteCheckpointer(db)  # 「重启」后的新实例
    tup = saver2.get_tuple(cfg)
    assert tup is not None
    assert tup.checkpoint["channel_values"]["x"] == 11
    assert tup.config["configurable"]["thread_id"] == "th-rt"


def test_list_across_restart(tmp_path):
    db = str(tmp_path / "ck.db")
    cfg = {"configurable": {"thread_id": "th-list"}}
    app = _build().compile(checkpointer=SqliteCheckpointer(db))
    app.invoke({"x": 0}, config=cfg)
    saver2 = SqliteCheckpointer(db)
    tuples = list(saver2.list(cfg))
    assert len(tuples) >= 1
    # 最新一条（rowid 序）应包含终态 x=11
    assert tuples[0].checkpoint["channel_values"]["x"] == 11


def test_crash_resume_skips_completed_nodes(tmp_path):
    """V3：崩溃 → 重启 → 续跑；已完成节点不重跑（红线 2）。"""
    db = str(tmp_path / "ck.db")
    cfg = {"configurable": {"thread_id": "th-crash"}}
    FAIL["on"] = True
    app1 = _build().compile(checkpointer=SqliteCheckpointer(db))
    with pytest.raises(RuntimeError):
        app1.invoke({"x": 0}, config=cfg)
    assert CALLS == ["a"]  # a 已完成且已检查点化；b 崩了

    # 「重启」：新 saver + 新 app 实例，从盘上检查点续跑。
    # 语义验证：invoke(None) 只重跑**未完成**的 b（重试是应该的），
    # 已完成的 a 绝不重跑（不重复计费，红线 2）。
    FAIL["on"] = False
    app2 = _build().compile(checkpointer=SqliteCheckpointer(db))
    out = app2.invoke(None, config=cfg)
    assert out["x"] == 11                     # 产物与崩溃前意图一致
    assert CALLS.count("a") == 1              # 已完成节点不重跑 → 不重复计费
    assert CALLS[-1] == "b"                   # 只续了剩余步骤


def test_default_checkpointer_is_persistent(tmp_path, monkeypatch):
    """compile_task_graph() 默认必须落盘（G1 修复）：不再回退 MemorySaver。"""
    db = str(tmp_path / "default.db")
    monkeypatch.setenv("FY_CHECKPOINT_DB", db)
    app = compile_task_graph()
    state = {
        "task_id": "task-default-ck",
        "attempt": 1,
        "goal": "I feel very overwhelmed by work and need someone to listen",
        "route": "single_agent",
        "domain": "personal",
        "budget_balance": 1.0,
        "max_steps": 5,
        "history": [{"role": "user", "content": "I feel overwhelmed"}],
    }
    res = app.invoke(state, config={"configurable": {"thread_id": "th-default"}})
    assert res["status"] == "completed"
    assert (tmp_path / "default.db").is_file()  # 检查点真在盘上


def test_delete_thread(tmp_path):
    db = str(tmp_path / "ck.db")
    cfg = {"configurable": {"thread_id": "th-del"}}
    app = _build().compile(checkpointer=SqliteCheckpointer(db))
    app.invoke({"x": 0}, config=cfg)
    saver = SqliteCheckpointer(db)
    assert saver.get_tuple(cfg) is not None
    saver.delete_thread("th-del")
    assert saver.get_tuple(cfg) is None
