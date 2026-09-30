"""API tests for Creative and Daily Arrangement tools (Execution Manual F7, A11, A12)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from helpers import login_owner


def test_api_list_creative_tools(client: TestClient):
    """GET /api/creative/tools returns complete declarations."""
    headers = login_owner(client)
    res = client.get("/api/creative/tools", headers=headers)
    assert res.status_code == 200
    tools = res.json()["tools"]
    assert len(tools) >= 7
    ids = {t["tool_id"] for t in tools}
    assert "local_calendar_schedule" in ids
    assert "local_svg_artwork" in ids
    assert "dalle_image_generation" in ids
    assert "wechat_social_publish" in ids


def test_api_execute_local_creation_tool(client: TestClient):
    """POST /api/creative/execute completes local creation with verified personal artifact."""
    headers = login_owner(client)
    task = client.post(
        "/api/tasks",
        json={"goal": "Visual generation task", "idempotency_key": "api-task-svg-1"},
        headers=headers,
    ).json()

    res = client.post(
        "/api/creative/execute",
        json={
            "tool_id": "local_svg_artwork",
            "task_id": task["id"],
            "idempotency_key": "exec-svg-1",
            "params": {"topic": "核心生命愿景"},
            "approved": True,
        },
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "completed"
    assert body["media_type"] == "image/svg+xml"
    assert body["domain"] == "personal"
    assert body["artifact_id"].startswith("art-")


def test_api_execute_calendar_requires_individual_approval(client: TestClient):
    """POST /api/creative/execute requires individual approval for calendar modifications."""
    headers = login_owner(client)
    task = client.post(
        "/api/tasks",
        json={"goal": "Calendar task", "idempotency_key": "api-task-cal-1"},
        headers=headers,
    ).json()

    # Step 1: Without approval flag
    unapproved = client.post(
        "/api/creative/execute",
        json={
            "tool_id": "local_calendar_schedule",
            "task_id": task["id"],
            "idempotency_key": "exec-cal-1",
            "params": {"title": "月度习惯追踪"},
            "approved": False,
        },
        headers=headers,
    )
    assert unapproved.status_code == 200
    assert unapproved.json()["status"] == "approval_required"
    assert unapproved.json()["requires_individual_approval"] is True

    # Step 2: With approved flag
    approved = client.post(
        "/api/creative/execute",
        json={
            "tool_id": "local_calendar_schedule",
            "task_id": task["id"],
            "idempotency_key": "exec-cal-2",
            "params": {"title": "月度习惯追踪"},
            "approved": True,
        },
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"
    assert approved.json()["media_type"] == "text/calendar"


def test_api_execute_unautomatable_returns_manual_steps(client: TestClient):
    """POST /api/creative/execute returns manual guidance for App UI only tools."""
    headers = login_owner(client)
    task = client.post(
        "/api/tasks",
        json={"goal": "Social task", "idempotency_key": "api-task-social-1"},
        headers=headers,
    ).json()

    res = client.post(
        "/api/creative/execute",
        json={
            "tool_id": "wechat_social_publish",
            "task_id": task["id"],
            "idempotency_key": "exec-social-1",
            "params": {"message": "Hello"},
            "approved": True,
        },
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "manual_action_required"
    assert body["can_automate"] is False
    assert "微信客户端无官方开放个人自动化接口" in body["manual_steps"]
