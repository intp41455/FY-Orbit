"""P1-19 Chat 调试预览 API tests: template + tools + SSE orchestration
against the real FastAPI app (isolated SQLite, stub SSE mode)."""

from __future__ import annotations

import json

import find_yourself.db.prompt_models  # noqa: F401  (register tables on Base.metadata)
from fastapi.testclient import TestClient

from helpers import login_owner
from test_streaming_sse import parse_sse

TEMPLATE = {
    "name": "chat.debug.researcher",
    "content": "你是 {{topic}} 领域的研究助手，回答要简洁。",
    "variables_schema": {"topic": {"type": "str", "required": True}},
    "scope": "platform",
    "description": "Chat 调试预览用模板",
}


def _login(client: TestClient) -> dict:
    return login_owner(client)


def _register_tool(client: TestClient, headers: dict, name: str) -> None:
    r = client.post("/api/tools/register", json={
        "name": name,
        "description": "两数相加" if name == "add" else "原样回显",
        "parameters": (
            {"type": "object",
             "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
             "required": ["a", "b"]}
            if name == "add" else
            {"type": "object", "properties": {"text": {"type": "string"}}}
        ),
        "entry": {"type": "builtin", "executor": name},
    }, headers=headers)
    assert r.status_code == 200, r.text


def _create_and_activate_template(client: TestClient, headers: dict) -> None:
    r = client.post("/api/prompts", json=TEMPLATE, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/prompts/{TEMPLATE['name']}/stage", json={
        "content": TEMPLATE["content"],
        "variables_schema": TEMPLATE["variables_schema"],
        "reason": "chat-debug 联调模板",
    }, headers=headers)
    assert r.status_code == 200, r.text
    proposal = r.json()
    r = client.post(f"/api/proposals/{proposal['proposal_id']}/decision",
                    json={"digest": proposal["digest"], "decision": "approve"},
                    headers=headers)
    assert r.status_code == 200, r.text
    r = client.put(f"/api/prompts/{TEMPLATE['name']}/activate", json={
        "version": 1, "reason": "chat-debug 启用",
    }, headers=headers)
    assert r.status_code == 200, r.text
    proposal = r.json()
    r = client.post(f"/api/proposals/{proposal['proposal_id']}/decision",
                    json={"digest": proposal["digest"], "decision": "approve"},
                    headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/prompts/{TEMPLATE['name']}/apply", json={
        "proposal_id": proposal["proposal_id"], "digest": proposal["digest"],
    }, headers=headers)
    assert r.status_code == 200, r.text


def test_stream_chat_requires_auth(client):
    r = client.post("/api/streaming/chat", json={"prompt": "x", "tools": ["add"]})
    assert r.status_code == 401


def test_unknown_bound_tool_is_404_envelope(client):
    headers = _login(client)
    r = client.post("/api/streaming/chat",
                    json={"prompt": "x", "tools": ["nope"]}, headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "tool_not_found"


def test_chat_debug_full_flow_template_tools_sse_trace(client):
    headers = _login(client)
    _register_tool(client, headers, "add")
    _create_and_activate_template(client, headers)

    # 模板渲染预览（与 Chat 页使用的同一 render 契约）
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/render",
                   params={"variables": json.dumps({"topic": "心理画像"})},
                   headers=headers)
    assert r.status_code == 200
    preview = r.json()
    assert "心理画像" in preview["text"]
    assert preview["variables_hash"]

    body = {
        "prompt": "调用 add 计算 3 和 4 的和",
        "task_id": "chat-debug-e2e",
        "template_name": TEMPLATE["name"],
        "template_version": 1,
        "variables": {"topic": "心理画像"},
        "tools": ["add"],
    }
    with client.stream("POST", "/api/streaming/chat", json=body,
                       headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())

    events = parse_sse(raw)
    names = [e["event"] for e in events]

    start = events[0]["data"]
    assert start["mode"] == "stub"
    assert start["tools"] == ["add"]
    # 模板徽标审计链：name/version/variables_hash 与 render 预览一致
    assert start["template"]["name"] == TEMPLATE["name"]
    assert start["template"]["version"] == 1
    assert start["template"]["variables_hash"] == preview["variables_hash"]

    assert "tool_call" in names and "tool_result" in names
    call_evt = next(e for e in events if e["event"] == "tool_call")["data"]
    assert call_evt["name"] == "add" and call_evt["arguments"] == {"a": 3, "b": 4}
    result_evt = next(e for e in events if e["event"] == "tool_result")["data"]
    assert result_evt["result"] == {"sum": 7}

    end = events[-1]["data"]
    assert end["finish_reason"] == "stop"
    assert end["tools_used"][0]["name"] == "add"
    assert end["tools_used"][0]["result"] == {"sum": 7}

    # 真实工具执行证据：/api/tools/calls 留有 receipt
    r = client.get("/api/tools/calls", headers=headers)
    assert r.status_code == 200
    receipts = [c for c in r.json()["calls"]
                if c["tool"] == "add" and c["arguments"] == {"a": 3, "b": 4}]
    assert receipts

    # 模板渲染审计：本次真实 Chat 用量写入 render log（scope=chat-debug）
    r = client.get(f"/api/prompts/{TEMPLATE['name']}/logs", headers=headers)
    assert r.status_code == 200
    assert any(log.get("scope") == "chat-debug" for log in r.json())


def test_plain_stream_without_template_or_tools_unchanged(client):
    headers = _login(client)
    with client.stream("POST", "/api/streaming/chat",
                       json={"prompt": "hello"}, headers=headers) as resp:
        raw = "".join(resp.iter_text())
    events = parse_sse(raw)
    names = [e["event"] for e in events]
    assert names[0] == "message_start" and names[-1] == "message_end"
    assert "tool_call" not in names
    assert "tools_used" not in events[-1]["data"]
