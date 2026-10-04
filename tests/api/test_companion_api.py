"""API-level tests for Companion, Reflective Listening, Perspective Framing & Safety (U08-U10)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def test_companion_api_listen_mode(client: TestClient):
    headers = login_owner(client)
    c = client.post("/api/conversations", json={
        "title": "Evening Reflection",
        "domain": "personal",
        "mode": "listen",
    }, headers=headers).json()

    # User message
    client.post(f"/api/conversations/{c['id']}/messages", json={
        "role": "user",
        "content": "今天事情很多很乱，只想倾听，不用做测评或下结论。",
        "client_message_id": "u-msg-1",
    }, headers=headers)

    # Companion reply: no model provider configured in test settings -> the API
    # must fail honestly (503 model_not_configured), never fall back to templates.
    r = client.post(f"/api/conversations/{c['id']}/reply", headers=headers)
    assert r.status_code == 503
    data = r.json()
    assert data["error"]["code"] == "model_not_configured"
    assert "不会擅自为你下任何心理诊断" not in data["error"]["message"]


def test_companion_api_crisis_reality_hotlines(client: TestClient):
    headers = login_owner(client)
    c = client.post("/api/conversations", json={
        "title": "Crisis Check",
        "domain": "personal",
        "mode": "listen",
    }, headers=headers).json()

    r = client.post(f"/api/conversations/{c['id']}/reply", json={
        "message": "我活着太累太痛苦了，我不想活了，想自残放弃生命",
    }, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["metadata"]["is_crisis"] is True
    assert data["metadata"]["hotlines_provided"] is True
    assert data["metadata"]["unauthorized_third_party_contact"] is False
    assert "400-161-9995" in data["content"]


def test_companion_api_metaphysical_disclaimer(client: TestClient):
    headers = login_owner(client)
    c = client.post("/api/conversations", json={
        "title": "Dream Discussion",
        "domain": "personal",
        "mode": "explore",
    }, headers=headers).json()

    # Without a configured provider the reply path is an honest 503 — the
    # disclaimer-only template reply is retired.
    r = client.post(f"/api/conversations/{c['id']}/reply", json={
        "message": "我昨晚梦见在深海里潜意识下坠，这在荣格心理学里预示着什么宿命？",
    }, headers=headers)
    assert r.status_code == 503
    data = r.json()
    assert data["error"]["code"] == "model_not_configured"
