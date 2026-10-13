"""P1-5 收口：agent-dispatch 的 owner 隔离。

背景（2026-10-14 现场复核）：`api/routes/agent_dispatch.py` 已挂
`csrf_protected`/`get_actor`（鉴权在），但 `list_dispatches` /
`get_dispatch` **不做 owner 过滤** —— 任何已认证 owner 都能列出/读到
别人的派发记录。本文件钉死隔离语义：属主外 404（不泄漏「存在」），
列表只数自己的。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from find_yourself.api.routes import agent_dispatch as route
from find_yourself.services.agent_dispatch import AgentDispatchService
from find_yourself.services.tool_registry import ToolRegistryService


def _fresh_dispatch_service(tmp_path) -> AgentDispatchService:
    registry = ToolRegistryService(persist_dir=tmp_path / "tools")
    registry.register(
        name="echo",
        description="原样回显参数（内置确定性执行器）",
        parameters={"type": "object", "properties": {"message": {"type": "string"}}},
        entry={"type": "builtin", "executor": "echo"},
    )
    svc = AgentDispatchService(archive_dir=tmp_path / "traces", registry=registry)
    route.reset_store_for_tests()
    route.agent_dispatch = svc
    return svc


def _dispatch(client: TestClient, headers: dict, message: str) -> dict:
    body = {
        "task": {
            "capability": "文本回显",
            "payload": {"message": message},
            "acceptance": {"type": "output_contains", "contains": [message]},
        },
        "tools": [{"name": "echo", "arguments": {"message": message}}],
    }
    resp = client.post("/api/agent-dispatch", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_owner_only_sees_own_dispatches(tmp_path, client: TestClient):
    _fresh_dispatch_service(tmp_path)
    a = client.post("/auth/local/dev-token",
                    json={"token": "dev-token-secret"}).json()
    a_headers = {"X-CSRF-Token": a["csrf_token"]}

    mine = _dispatch(client, a_headers, "A 的任务")
    assert client.get("/api/agent-dispatch",
                      headers=a_headers).json()["count"] == 1

    # 第二个 owner（真实注册路径，owner_id 是 user.id 而非 dev 引导的 "owner"）
    reg = client.post("/auth/register", json={
        "email": "other-owner@example.com",
        "password": "a-long-enough-password",
        "consent_accepted": True,
    }).json()
    b_headers = {"X-CSRF-Token": reg["csrf_token"]}

    listed = client.get("/api/agent-dispatch", headers=b_headers).json()
    assert listed["count"] == 0, "不得跨 owner 列出别人的派发"
    assert listed["parent_task_ids"] == []

    # 属主外读单条：404 且不泄漏「存在但不属于你」
    resp = client.get(f"/api/agent-dispatch/{mine['parent_task_id']}",
                      headers=b_headers)
    assert resp.status_code == 404

    # 属主本人仍读得到（先把会话登回 A：register 覆盖了同一 TestClient 的
    # session cookie，不登回的话这一发其实是 B 的身份）
    again = client.post("/auth/local/dev-token",
                        json={"token": "dev-token-secret"}).json()
    a_headers_again = {"X-CSRF-Token": again["csrf_token"]}
    assert client.get(f"/api/agent-dispatch/{mine['parent_task_id']}",
                      headers=a_headers_again).status_code == 200
