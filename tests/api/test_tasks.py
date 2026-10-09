"""Task idempotency, cancel propagation and SSE streaming (§5.2, T01/T05)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from helpers import login_owner


def test_task_idempotency_same_key_same_payload(client: TestClient):
    headers = login_owner(client)
    body = {"goal": "write a note", "domain": "personal", "idempotency_key": "key-abc-1234"}
    r1 = client.post("/api/tasks", json=body, headers=headers)
    r2 = client.post("/api/tasks", json=body, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


def test_task_idempotency_same_key_different_payload_rejected(client: TestClient):
    headers = login_owner(client)
    client.post("/api/tasks", json={"goal": "first", "idempotency_key": "key-def-1234"}, headers=headers)
    r = client.post("/api/tasks", json={"goal": "second", "idempotency_key": "key-def-1234"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_mismatch"


def test_task_cancel_marks_cancelled(client: TestClient):
    headers = login_owner(client)
    t = client.post("/api/tasks", json={"goal": "cancel me", "idempotency_key": "key-cancel-1"}, headers=headers).json()
    r = client.post(f"/api/tasks/{t['id']}/cancel", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    got = client.get(f"/api/tasks/{t['id']}")
    assert got.json()["status"] == "cancelled"


def test_task_unauthorized_read_returns_404(client: TestClient, session_maker):
    headers = login_owner(client)
    t = client.post("/api/tasks", json={"goal": "hidden", "idempotency_key": "key-hidden-1"}, headers=headers).json()
    # A fresh client (no session) cannot read it.
    anon = TestClient(client.app)
    r = anon.get(f"/api/tasks/{t['id']}")
    assert r.status_code == 401


def test_sse_event_stream_real_listening_port(app):
    """Real ASGI listening socket: SSE auth + stage event delivery (§5.2)."""
    import threading

    import httpx
    import uvicorn

    def _force_loopback(asgi_app):
        async def wrapper(scope, receive, send):
            if scope.get("type") in ("http", "websocket"):
                scope["client"] = ("127.0.0.1", 12345)
            return await asgi_app(scope, receive, send)
        return wrapper

    s = app.state.session_maker()
    from find_yourself.db.models import Task
    from find_yourself.db.types import utcnow
    from find_yourself.services.audit import AuditService
    from find_yourself.services.auth import AuthService
    auth = AuthService(s, AuditService(s), environment="test", local_token="x")
    sess = auth.create_owner_session("owner")
    s.commit()
    # Create a task row directly.
    from uuid import uuid4
    task = Task(id=uuid4().hex, owner_id="owner", goal="sse", status="queued",
                deadline=utcnow(), idempotency_key="sse-key-1")
    s.add(task); s.commit(); s.close()

    config = uvicorn.Config(_force_loopback(app), host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(50):
        if getattr(server, "servers", None):
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(trust_env=False, timeout=10.0) as http:
            # Unauthenticated SSE rejected.
            r = http.get(f"{base}/api/tasks/{task.id}/events")
            assert r.status_code == 401
            # Authenticated: connect with session cookie.
            cookies = {"fy_session": sess.id}
            from find_yourself.runtime.sse import bus
            bus.publish(task.id, "queued", {"task_id": task.id})
            with http.stream("GET", f"{base}/api/tasks/{task.id}/events", cookies=cookies) as resp:
                assert resp.status_code == 200
                line = next(resp.iter_lines())
                assert line.startswith("id:")
    finally:
        server.should_exit = True
        th.join(timeout=5)
