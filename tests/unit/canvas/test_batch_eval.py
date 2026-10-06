"""A-画布搭建器-06：CSV 批量评估 + UnifiedEvaluator 接线 + 生命周期 API 测试。

* ``parse_eval_csv`` / ``UnifiedEvaluator.evaluate_flow_batch``——评测器此前
  无人 import，本文件验证产品接线真实可用；
* 生命周期路由用依赖覆盖挂到最小 app 上（不依赖完整鉴权栈）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from find_yourself.api.deps import get_actor, get_services, get_settings, csrf_protected
from find_yourself.api.routes import dsl_lifecycle
from find_yourself.runtime.evaluation import BatchCase, UnifiedEvaluator, parse_eval_csv
from find_yourself.services.actor import Actor
from find_yourself.services.dsl_canvas import DslFlowStore, evaluate_flow_batch
from find_yourself.services.errors import ValidationFailed


CHATFLOW_DOC = {
    "version": "1",
    "flow_type": "chatflow",
    "nodes": [
        {"id": "t1", "type": "trigger",
         "params": {"kind": "conversation", "config": {"value": ""}}},
        {"id": "tp1", "type": "template", "params": {"template": "回声：{text}"}},
        {"id": "out1", "type": "output"},
    ],
    "edges": [{"from": "t1", "to": "tp1"}, {"from": "tp1", "to": "out1"}],
}


# --------------------------------------------------------------------------- #
# CSV 解析
# --------------------------------------------------------------------------- #

def test_parse_eval_csv_full_header() -> None:
    cases = parse_eval_csv(
        "case_id,input,expected,category\n"
        "c1,你好,回声：你好,greeting\n"
        "c2,再见,,\n")
    assert [c.case_id for c in cases] == ["c1", "c2"]
    assert cases[0].expected == "回声：你好"
    assert cases[1].expected == ""


def test_parse_eval_csv_requires_input_column() -> None:
    with pytest.raises(ValueError, match="input"):
        parse_eval_csv("case_id,question\nc1,hi\n")


def test_parse_eval_csv_missing_input_value_reports_line() -> None:
    with pytest.raises(ValueError, match="第 3 行"):
        parse_eval_csv("case_id,input\nc1,hi\nc2,\n")


def test_parse_eval_csv_skips_blank_lines_and_bom() -> None:
    cases = parse_eval_csv("\ufeffinput\n\n你好\n\n")
    assert [c.input for c in cases] == ["你好"]


# --------------------------------------------------------------------------- #
# UnifiedEvaluator 接线（此前无人 import 的评测器）
# --------------------------------------------------------------------------- #

def test_unified_evaluator_evaluate_flow_batch_scores_and_aggregates() -> None:
    cases = [
        BatchCase(case_id="c1", input="你好", expected="回声：你好"),
        BatchCase(case_id="c2", input="再见", expected="回声：再见"),
        BatchCase(case_id="c3", input="坏输入", expected="永远不会出现"),
    ]
    report = UnifiedEvaluator().evaluate_flow_batch(
        cases, make_runner := _make_echo_runner())
    assert report["total_cases"] == 3
    assert report["passed"] == 2
    assert report["success_rate"] == round(2 / 3, 4)
    assert [c["case_id"] for c in report["cases"]] == ["c1", "c2", "c3"]
    assert report["cases"][2]["success"] is False
    assert "expected" in report["cases"][2]["notes"]
    assert "category_summary" in report


def test_unified_evaluator_marks_runner_exception_as_failure() -> None:
    def boom(_prompt: str):
        raise RuntimeError("节点炸了")

    report = UnifiedEvaluator().evaluate_flow_batch(
        [BatchCase(case_id="x", input="i")], boom)
    assert report["passed"] == 0
    assert report["cases"][0]["notes"].startswith("exception:RuntimeError")


def test_evaluate_flow_batch_wires_flow_runner_to_published_doc() -> None:
    csv_text = "input,expected\n你好,回声：你好\n早上好,回声：早上好\n"
    report = evaluate_flow_batch(CHATFLOW_DOC, csv_text)
    assert report["total_cases"] == 2
    assert report["success_rate"] == 1.0
    assert "回声：" in report["cases"][0]["output_excerpt"]


def test_evaluate_flow_batch_empty_csv_is_validation_error() -> None:
    with pytest.raises(ValidationFailed):
        evaluate_flow_batch(CHATFLOW_DOC, "input\n")


def _make_echo_runner():
    from find_yourself.services.dsl_canvas import make_flow_runner

    return make_flow_runner(CHATFLOW_DOC)


# --------------------------------------------------------------------------- #
# 生命周期 HTTP 面（依赖覆盖，最小 app）
# --------------------------------------------------------------------------- #

from find_yourself.config import Settings


@pytest.fixture()
def client(session, owner) -> TestClient:
    app = FastAPI()
    app.include_router(dsl_lifecycle.router)
    svc = _FakeServices(session)

    app.dependency_overrides[get_actor] = lambda: owner
    app.dependency_overrides[csrf_protected] = lambda: owner
    app.dependency_overrides[get_services] = lambda: svc
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456")
    return TestClient(app)


class _FakeServices:
    """只暴露生命周期路由用到的字段（session/budget）。"""

    def __init__(self, session):
        self.session = session
        self.budget = None


def test_flow_lifecycle_http_roundtrip(client: TestClient) -> None:
    r = client.post("/api/dsl-lifecycle/flows",
                    json={"name": "客服回声", "flow_type": "chatflow"})
    assert r.status_code == 201, r.text
    flow_id = r.json()["id"]

    r = client.put(f"/api/dsl-lifecycle/flows/{flow_id}/draft",
                   json={"doc": CHATFLOW_DOC})
    assert r.status_code == 200

    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/publish", json={"note": "v1"})
    assert r.status_code == 200
    assert r.json()["published_version"] == 1

    r = client.get(f"/api/dsl-lifecycle/flows/{flow_id}/versions")
    assert r.status_code == 200
    assert [v["version"] for v in r.json()["items"]] == [1]

    r = client.get(f"/api/dsl-lifecycle/flows/{flow_id}/versions/1")
    assert r.status_code == 200
    assert r.json()["doc"]["flow_type"] == "chatflow"

    # 回滚到 v1 → 草稿切回
    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/rollback",
                    json={"version": 1})
    assert r.status_code == 200
    assert r.json()["rolled_back_to"] == 1


def test_batch_eval_http_endpoint(client: TestClient) -> None:
    flow_id = client.post(
        "/api/dsl-lifecycle/flows",
        json={"name": "批量评估", "flow_type": "chatflow"}).json()["id"]
    client.put(f"/api/dsl-lifecycle/flows/{flow_id}/draft",
               json={"doc": CHATFLOW_DOC})
    client.post(f"/api/dsl-lifecycle/flows/{flow_id}/publish", json={})

    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/evaluate-batch",
                    json={"csv": "input,expected\n你好,回声：你好\n再見,永不命中\n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_cases"] == 2
    assert body["passed"] == 1


def test_batch_eval_requires_published_version(client: TestClient) -> None:
    flow_id = client.post(
        "/api/dsl-lifecycle/flows",
        json={"name": "未发布", "flow_type": "chatflow"}).json()["id"]
    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/evaluate-batch",
                    json={"csv": "input\nhi\n"})
    assert r.status_code == 409


def test_validate_assembly_http_endpoint(client: TestClient) -> None:
    r = client.post("/api/dsl-lifecycle/validate-assembly",
                    json={"doc": CHATFLOW_DOC})
    assert r.status_code == 200
    assert r.json()["flow_type"] == "chatflow"

    r = client.post("/api/dsl-lifecycle/validate-assembly",
                    json={"doc": CHATFLOW_DOC, "flow_type": "workflow"})
    assert r.status_code == 422


def test_validate_assembly_reports_orphans(client: TestClient) -> None:
    doc = {"version": "1", "nodes": [
        {"id": "in1", "type": "input", "params": {"kind": "literal", "value": "x"}},
        {"id": "out1", "type": "output"},
        {"id": "lost", "type": "template", "params": {"template": "x"}}],
        "edges": [{"from": "in1", "to": "out1"}]}
    r = client.post("/api/dsl-lifecycle/validate-assembly", json={"doc": doc})
    assert r.status_code == 422
    assert "不可达" in r.json()["detail"]
