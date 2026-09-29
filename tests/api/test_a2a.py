"""A01: A2A Agent Card shape, JSON-RPC envelope, upstream-not-configured (no fake success)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from find_yourself.adapters.a2a import A2ADispatcher, build_agent_card

from helpers import login_owner


def test_agent_card_has_required_fields(client: TestClient):
    r = client.get("/.well-known/agent.json")
    assert r.status_code == 200
    card = r.json()
    for f in ("name", "description", "version", "url", "protocolVersion", "capabilities",
              "defaultInputModes", "defaultOutputModes", "skills"):
        assert f in card, f"missing {f}"
    assert card["protocolVersion"] == "0.3.0"
    assert card["url"].endswith("/a2a/v1/jsonrpc")


def test_a2a_health(client: TestClient):
    r = client.get("/a2a/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_message_send_without_upstream_is_structured_error(client: TestClient):
    headers = login_owner(client)
    r = client.post("/a2a/v1/jsonrpc", json={
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]}},
    }, headers=headers)
    body = r.json()
    assert body["error"]["code"] == -32001  # upstream_not_configured
    assert "result" not in body
    # No fabricated task object returned.


def test_unknown_method_and_bad_request(client: TestClient):
    headers = login_owner(client)
    r = client.post("/a2a/v1/jsonrpc", json={"jsonrpc": "2.0", "id": 2, "method": "nope/x"},
                    headers=headers)
    assert r.json()["error"]["code"] == -32601
    # Non-object body -> invalid request.
    r2 = client.post("/a2a/v1/jsonrpc", json=[1, 2], headers=headers)
    assert r2.status_code in (200, 400)


def test_tasks_get_and_cancel_local(client: TestClient):
    headers = login_owner(client)
    t = client.post("/api/tasks", json={"goal": "a2a task", "idempotency_key": "a2a-key-0001"},
                    headers=headers).json()
    r = client.post("/a2a/v1/jsonrpc", json={
        "jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": {"id": t["id"]}},
        headers=headers)
    assert r.json()["result"]["id"] == t["id"]
    c = client.post("/a2a/v1/jsonrpc", json={
        "jsonrpc": "2.0", "id": 4, "method": "tasks/cancel", "params": {"id": t["id"]}},
        headers=headers)
    assert c.json()["result"]["status"]["state"] == "canceled"


def test_draining_dispatcher_rejects_new_tasks():
    d = A2ADispatcher(upstream_configured=True, draining=True)
    out = d.dispatch({"jsonrpc": "2.0", "id": 1, "method": "message/send",
                      "params": {"message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]}}})
    assert out["error"]["code"] == -32002  # agent_draining
