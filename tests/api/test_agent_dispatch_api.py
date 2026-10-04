"""P1-20 子 Agent 派发验证 API 测试（独立挂载，不依赖数据库）。

三态覆盖：
1. 验收通过 —— 子 Agent 真实执行工具，独立验收器复核通过 → verified；
2. 验收拒绝 —— 工具执行失败，自报 failed → rejected（含经验回写）；
3. 伪造场景 —— 子 Agent 自报 success 但产出不满足 acceptance → 仍被
   独立验收器拒绝（不信任自报）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from find_yourself.api.routes import agent_dispatch as route
from find_yourself.services.agent_dispatch import AgentDispatchService
from find_yourself.services.tool_registry import ToolRegistryService


def _make_registry(tmp_path) -> ToolRegistryService:
    registry = ToolRegistryService(persist_dir=tmp_path / "tools")
    registry.register(
        name="echo",
        description="原样回显参数（内置确定性执行器）",
        parameters={"type": "object", "properties": {"message": {"type": "string"}}},
        entry={"type": "builtin", "executor": "echo"},
    )
    registry.register(
        name="add",
        description="数值求和（内置确定性执行器）",
        parameters={"type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"]},
        entry={"type": "builtin", "executor": "add"},
    )
    return registry


def _client(tmp_path, client: TestClient) -> tuple[TestClient, AgentDispatchService, dict]:
    """Inject a fresh dispatch store into the module singleton and return an
    authenticated client (the routes now require an identity; P0 fix)."""
    registry = _make_registry(tmp_path)
    svc = AgentDispatchService(archive_dir=tmp_path / "traces", registry=registry)
    route.reset_store_for_tests()
    route.agent_dispatch = svc
    from helpers import login_owner
    headers = login_owner(client)
    return client, svc, headers


def _echo_task(acceptance: dict[str, Any], message: str = "你好世界") -> dict:
    return {
        "task": {
            "capability": "文本回显",
            "payload": {"message": message},
            "acceptance": acceptance,
            "env_contract": {"runtime": "deterministic-worker"},
        },
        "tools": [{"name": "echo", "arguments": {"message": message}}],
    }


# --- 态 1：验收通过 ------------------------------------------------------------

def test_verified_dispatch(tmp_path, client):
    client, _svc, _h = _client(tmp_path, client)
    body = _echo_task({
        "type": "output_contains",
        "contains": ["你好世界"],
    })
    r = client.post("/api/agent-dispatch", headers=_h, json=body)
    assert r.status_code == 200
    parent = r.json()
    assert parent["status"] == "verified"
    assert parent["task"]["capability"] == "文本回显"
    assert parent["task"]["env_contract"] == {"runtime": "deterministic-worker"}

    child = parent["children"][0]
    assert child["self_report"]["status"] == "success"
    assert child["verification"]["verdict"] == "verified"
    assert child["verification"]["consistent"] is True
    assert child["status"] == "verified"
    # 工具真实执行：回执含 call_id
    assert child["tool_calls"][0]["ok"] is True
    assert child["tool_calls"][0]["call_id"]

    # GET 父/子 trace
    r2 = client.get(f"/api/agent-dispatch/{parent['parent_task_id']}", headers=_h)
    assert r2.status_code == 200
    fetched = r2.json()
    assert fetched["children"][0]["child_task_id"] == child["child_task_id"]

    # trace 真实落盘
    archived = list((tmp_path / "traces").glob("dispatch-*.json"))
    assert len(archived) == 1


def test_verified_by_tool_invoked_log(tmp_path, client):
    """tool_invoked：验收器重查调用日志（而非采信子 Agent 回传回执）。"""
    client, _svc, _h = _client(tmp_path, client)
    r = client.post("/api/agent-dispatch", headers=_h, json={
        "task": {
            "capability": "数值求和",
            "payload": {"a": 2, "b": 3},
            "acceptance": {"type": "tool_invoked", "tools": ["add"]},
        },
        "tools": [{"name": "add", "arguments": {"a": 2, "b": 3}}],
    })
    assert r.status_code == 200
    parent = r.json()
    assert parent["status"] == "verified"
    child = parent["children"][0]
    assert child["verification"]["checks"][0] == {
        "check": "tool_invoked", "target": "add", "passed": True}
    # 求和结果真实出现在产出文本
    assert '"sum"' in child["result_text"]


# --- 态 2：验收拒绝（工具执行失败，自报 failed）-----------------------------------

def test_rejected_on_tool_failure(tmp_path, client):
    client, _svc, _h = _client(tmp_path, client)
    r = client.post("/api/agent-dispatch", headers=_h, json={
        "task": {
            "capability": "数值求和",
            "payload": {"a": "not-a-number", "b": 3},
            "acceptance": {"type": "tool_invoked", "tools": ["add"]},
        },
        "tools": [{"name": "add", "arguments": {"a": "not-a-number", "b": 3}}],
    })
    assert r.status_code == 200
    parent = r.json()
    assert parent["status"] == "rejected"
    child = parent["children"][0]
    assert child["self_report"]["status"] == "failed"
    assert child["verification"]["verdict"] == "rejected"
    assert child["tool_calls"][0]["ok"] is False
    assert child["tool_calls"][0]["error"]
    # 经验回写（预留结构）
    assert child["experience"]["written_back"] is True
    assert child["experience"]["lesson"]


# --- 态 3：伪造场景（自报 success，验收拒绝）--------------------------------------

def test_forged_self_report_still_rejected(tmp_path, client):
    client, _svc, _h = _client(tmp_path, client)
    # 工具成功执行、子 Agent 自报 success，但产出不含验收要求的文本
    body = _echo_task({
        "type": "output_contains",
        "contains": ["这段文本不存在于产出中"],
    })
    r = client.post("/api/agent-dispatch", headers=_h, json=body)
    assert r.status_code == 200
    parent = r.json()
    assert parent["status"] == "rejected"
    child = parent["children"][0]
    assert child["self_report"]["status"] == "success"   # 自报成功
    assert child["verification"]["verdict"] == "rejected"  # 验收仍拒绝
    assert child["verification"]["consistent"] is False
    assert child["verification"]["self_report_status"] == "success"
    assert child["verification"]["reasons"]
    assert child["experience"]["written_back"] is True
    assert "这段文本不存在于产出中" in child["experience"]["lesson"]


# --- 协议校验与边界 ---------------------------------------------------------------

def test_invalid_task_422(tmp_path, client):
    client, _svc, _h = _client(tmp_path, client)
    # acceptance.type 非法
    r = client.post("/api/agent-dispatch", headers=_h,
                    json=_echo_task({"type": "vibe_check"}))
    assert r.status_code == 422
    # 未注册工具
    r = client.post("/api/agent-dispatch", headers=_h, json={
        "task": {"capability": "x",
                 "acceptance": {"type": "output_contains", "contains": ["a"]}},
        "tools": [{"name": "nope"}],
    })
    assert r.status_code == 422
    assert "未在 P1-05 注册中心登记" in r.json()["detail"]


def test_get_missing_404_and_list(tmp_path, client):
    client, _svc, _h = _client(tmp_path, client)
    r = client.get("/api/agent-dispatch/missing-id", headers=_h)
    assert r.status_code == 404
    r = client.post("/api/agent-dispatch", headers=_h,
                    json=_echo_task({"type": "output_contains", "contains": ["你好世界"]}))
    rid = r.json()["parent_task_id"]
    r = client.get("/api/agent-dispatch", headers=_h)
    assert r.json() == {"count": 1, "parent_task_ids": [rid]}
