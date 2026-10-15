"""G4-h 跨会话实时：运行列表带状态（附着订阅的前提）。

``list_runs(owner)`` 返回 ``[{run_id, status}]``（按运行顺序），
前端凭此渲染“运行记录”并附着到任意一条 run 的 SSE 流——
不再只有发起会话能看到实时进度。
"""

from __future__ import annotations

from find_yourself.services.dsl_canvas import DslRunStore, RunResult


def _doc(run_id: str) -> dict:
    return {
        "version": "1",
        "nodes": [{"id": "out1", "type": "output"}],
        "edges": [],
    }


def _result(run_id: str, status: str = "succeeded") -> RunResult:
    return RunResult(run_id=run_id, status=status, doc=_doc(run_id))


def test_list_runs_returns_ids_with_status_in_order() -> None:
    store = DslRunStore()
    store.save(_result("r1"), owner="owner-1")
    store.save(_result("r2", status="failed"), owner="owner-1")
    store.save(_result("r3"), owner="owner-2")
    rows = store.list_runs("owner-1")
    assert rows == [
        {"run_id": "r1", "status": "succeeded"},
        {"run_id": "r2", "status": "failed"},
    ]


def test_list_runs_empty_for_unknown_owner() -> None:
    store = DslRunStore()
    store.save(_result("r1"), owner="owner-1")
    assert store.list_runs("nobody") == []
