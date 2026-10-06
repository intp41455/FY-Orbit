"""A-代码SDK-03 / A-画布搭建器-04 · 节点级调试 API 单测（契约供包5 前端消费）。

逐字契约：
* ``POST /api/dsl/runs``                          —— 草稿运行（只登记不执行）
* ``POST /api/dsl/runs/{run_id}/step``            —— 单步执行一节点
* ``POST /api/dsl/runs/{run_id}/node/{node_id}/run`` —— 单节点运行（自动取上游变量）
* ``GET  /api/dsl/runs/{run_id}/state``           —— 变量监视
* ``GET  /api/dsl/modes``                         —— 三重模式入口骨架

核心断言：逐步执行（step × N）的最终输出 == 一次性 ``run_dsl`` 的输出
（调试路径与整图执行同源）；画布跑过的 run 也能用调试端点重放节点。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from find_yourself.api.routes import dsl_canvas as canvas_routes
from find_yourself.services.dsl_canvas import run_dsl

from _dsl_docs import doc_of, lit, out, xf


def _pipeline_doc() -> dict:
    return doc_of(
        [lit([{"name": "张三", "score": 90}, {"name": "李四", "score": 40}]),
         xf("hi", "filter", field="score", op="gt", value=60),
         xf("tag", "map", op="set", field="grade", value="优:{name}"),
         out()],
        [{"from": "src", "to": "hi"}, {"from": "hi", "to": "tag"},
         {"from": "tag", "to": "out"}],
    )


def _create_draft(client: TestClient, headers: dict, doc: dict) -> dict:
    r = client.post("/api/dsl/runs", json={"dsl": doc}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# =========================================================================== #
# 草稿运行 + 单步执行
# =========================================================================== #
class TestStepExecution:
    def test_draft_run_is_registered_not_executed(self, client, headers):
        body = _create_draft(client, headers, _pipeline_doc())
        assert body["status"] == "draft"
        assert body["node_count"] == 4
        assert body["topological_order"][0] == "src"
        # 画布的 GET /runs/{id} 同样可读（共享存档）
        assert client.get(f"/api/dsl-canvas/runs/{body['run_id']}",
                          headers=headers).json()["status"] == "draft"

    def test_step_through_matches_one_shot_run(self, client, headers):
        """逐步执行到底 == 整图一次执行（同源）。"""
        doc = _pipeline_doc()
        run_id = _create_draft(client, headers, doc)["run_id"]
        seen: list[str] = []
        for _ in range(10):
            r = client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
            assert r.status_code == 200, r.text
            body = r.json()
            if body["status"] == "completed":
                break
            seen.append(body["node_id"])
            assert body["status"] == "succeeded", body
        assert seen == ["src", "hi", "tag", "out"]
        state = client.get(f"/api/dsl/runs/{run_id}/state",
                           headers=headers).json()
        assert state["run_status"] == "succeeded"
        assert state["final_output"] == run_dsl(doc).output

    def test_step_returns_input_and_output_for_debugging(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)  # src
        r = client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)  # hi
        body = r.json()
        assert body["node_id"] == "hi"
        assert body["input"] == [{"name": "张三", "score": 90},
                                 {"name": "李四", "score": 40}]
        assert body["output"] == [{"name": "张三", "score": 90}]
        assert body["progress"] == {"executed": 2, "total": 4}
        assert body["next_node_id"] == "tag"

    def test_step_with_explicit_node_id(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        r = client.post(f"/api/dsl/runs/{run_id}/step",
                        json={"node_id": "tag"}, headers=headers)
        body = r.json()
        # 显式指定但上游未跑 → transform 无上游输入 → 如实失败
        assert body["status"] == "failed"
        assert "无上游输入" in body["error"]

    def test_step_completed_when_nothing_left(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        for _ in range(5):
            r = client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
            if r.json().get("status") == "completed":
                break
        again = client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
        body = again.json()
        assert body["status"] == "completed"
        assert body["next_node_id"] is None

    def test_step_on_conditionally_skipped_node(self, client, headers):
        """入边条件全不通过 → 节点记 skipped（与调度器语义一致）。"""
        doc = doc_of(
            [lit({"mode": "slow"}),
             xf("br", "branch", field="mode", op="eq", value="slow",
                then_label="slow", else_label="fast"),
             xf("fast", "template", template="快速:{value.mode}"),
             out(fmt="text")],
            [{"from": "src", "to": "br"},
             {"from": "br", "to": "fast",
              "condition": {"field": "branch", "op": "eq", "value": "fast"}},
             {"from": "fast", "to": "out"}])
        run_id = _create_draft(client, headers, doc)["run_id"]
        statuses: dict[str, str] = {}
        for _ in range(6):
            body = client.post(f"/api/dsl/runs/{run_id}/step",
                               headers=headers).json()
            if body["status"] == "completed":
                break
            statuses[body["node_id"]] = body["status"]
        assert statuses["fast"] == "skipped"

    def test_step_hitting_confirm_returns_202_suspended(self, client, headers):
        doc = doc_of([lit({"amount": 100}),
                      xf("cf", "confirm", prompt="确认？", role="owner"),
                      out()],
                     [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        run_id = _create_draft(client, headers, doc)["run_id"]
        client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)  # src
        r = client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)  # cf
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "suspended"
        assert body["node_id"] == "cf"
        assert body["suspended"]["context"]["prompt"] == "确认？"
        # 存档里的 run 如实记录挂起（前端可刷新监视）
        state = client.get(f"/api/dsl/runs/{run_id}/state",
                           headers=headers).json()
        assert state["variables"]["cf"]["status"] == "suspended"

    def test_node_failure_is_reported_not_hidden(self, client, headers):
        doc = doc_of([lit([{"score": "高"}]),
                      xf("agg", "aggregate", op="sum", field="score"), out()],
                     [{"from": "src", "to": "agg"}, {"from": "agg", "to": "out"}])
        run_id = _create_draft(client, headers, doc)["run_id"]
        client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
        body = client.post(f"/api/dsl/runs/{run_id}/step",
                           headers=headers).json()
        assert body["status"] == "failed"
        assert "数值" in body["error"]
        state = client.get(f"/api/dsl/runs/{run_id}/state",
                           headers=headers).json()
        assert state["run_status"] == "failed"


# =========================================================================== #
# 单节点运行（自动取上游变量）
# =========================================================================== #
class TestSingleNodeRun:
    def test_auto_fetches_upstream_variables(self, client, headers):
        """跑完上游后单点运行下游：输入自动来自存档里的上游输出。"""
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        for _ in range(2):  # src, hi
            client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
        r = client.post(f"/api/dsl/runs/{run_id}/node/tag/run", headers=headers)
        body = r.json()
        assert body["status"] == "succeeded"
        assert body["input"] == [{"name": "张三", "score": 90}]
        # map.set 保留既有字段（与平台 _exec_map 同语义）
        assert body["output"] == [{"name": "张三", "score": 90,
                                   "grade": "优:张三"}]

    def test_node_run_is_repeatable_for_debugging(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        for _ in range(3):
            client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
        outs = [client.post(f"/api/dsl/runs/{run_id}/node/tag/run",
                            headers=headers).json()["output"] for _ in range(2)]
        assert outs[0] == outs[1]  # 确定性：重放同输出

    def test_replay_canvas_run_node(self, client, headers):
        """画布整图跑过的 run 也能用单节点端点重放（共享存档）。"""
        r = client.post("/api/dsl-canvas/runs", json={"dsl": _pipeline_doc()},
                        headers=headers)
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        body = client.post(f"/api/dsl/runs/{run_id}/node/hi/run",
                           headers=headers).json()
        assert body["status"] == "succeeded"
        assert body["output"] == [{"name": "张三", "score": 90}]

    def test_unknown_node_422(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        r = client.post(f"/api/dsl/runs/{run_id}/node/ghost/run",
                        headers=headers)
        assert r.status_code == 422
        assert "ghost" in r.json()["detail"]


# =========================================================================== #
# 变量监视
# =========================================================================== #
class TestStateEndpoint:
    def test_state_tracks_pending_then_succeeded(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        state = client.get(f"/api/dsl/runs/{run_id}/state",
                           headers=headers).json()
        assert all(v["status"] == "pending" for v in state["variables"].values())
        assert state["progress"] == {"executed": 0, "total": 4}
        for _ in range(4):
            client.post(f"/api/dsl/runs/{run_id}/step", headers=headers)
        state = client.get(f"/api/dsl/runs/{run_id}/state",
                           headers=headers).json()
        assert {nid: v["status"] for nid, v in state["variables"].items()} == \
            {"src": "succeeded", "hi": "succeeded", "tag": "succeeded",
             "out": "succeeded"}
        assert state["variables"]["tag"]["output"] == \
            [{"name": "张三", "score": 90, "grade": "优:张三"}]


# =========================================================================== #
# 错误面与鉴权
# =========================================================================== #
class TestErrorSurface:
    def test_unknown_run_404(self, client, headers):
        assert client.post("/api/dsl/runs/none/step",
                           headers=headers).status_code == 404
        assert client.get("/api/dsl/runs/none/state",
                          headers=headers).status_code == 404

    def test_cyclic_dsl_rejected_at_draft_creation(self, client, headers):
        cyclic = doc_of([lit(1), out()],
                        [{"from": "src", "to": "out"},
                         {"from": "out", "to": "src"}])
        r = client.post("/api/dsl/runs", json={"dsl": cyclic}, headers=headers)
        assert r.status_code == 422
        assert "环" in r.json()["detail"]

    def test_step_with_unknown_explicit_node_422(self, client, headers):
        run_id = _create_draft(client, headers, _pipeline_doc())["run_id"]
        r = client.post(f"/api/dsl/runs/{run_id}/step",
                        json={"node_id": "ghost"}, headers=headers)
        assert r.status_code == 422

    def test_endpoints_require_authentication(self, client):
        doc = _pipeline_doc()
        assert client.post("/api/dsl/runs", json={"dsl": doc}).status_code == 401
        assert client.get("/api/dsl/runs/none/state").status_code == 401


# =========================================================================== #
# 三重模式入口骨架
# =========================================================================== #
class TestModesEndpoint:
    def test_modes_lists_three_modes_with_entries(self, client, headers):
        r = client.get("/api/dsl/modes", headers=headers)
        assert r.status_code == 200
        modes = r.json()["modes"]
        assert [m["mode"] for m in modes] == \
            ["beginner", "technical", "enterprise"]
        for mode in modes:
            assert mode["label"] and mode["entries"]

    def test_modes_endpoint_requires_authentication(self, client):
        assert client.get("/api/dsl/modes").status_code == 401
