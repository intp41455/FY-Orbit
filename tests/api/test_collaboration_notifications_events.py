"""API tests: /api/collaboration/notifications/events (A5 端点层测试).

覆盖：
* 鉴权：未登录身份请求一律被拦截（401/403）；
* 响应头：text/event-stream 与 Cache-Control: no-store；
* Last-Event-ID 头与 query 参数解析：合法整数续传、非法字符串与负数安全降级为 0 且绝不 500；
* 断点续传：带 Last-Event-ID 只收后续事件，不重复下发已消费事件。
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

import httpx
import pytest
import uvicorn

from find_yourself.runtime.sse import bus as sse_bus
from find_yourself.services.audit import AuditService
from find_yourself.services.auth import AuthService
from find_yourself.services.collaboration import notification_channel


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def sse_server(app) -> Iterator[tuple[str, dict[str, str]]]:
    """启动本地真实端口 uvicorn 服务用于测试无限流式 SSE。"""
    s = app.state.session_maker()
    auth = AuthService(s, AuditService(s), environment="test", local_token="dev-token")
    sess = auth.create_owner_session("owner")
    s.commit()
    s.close()

    config = uvicorn.Config(
        _force_loopback(app), host="127.0.0.1", port=0, log_level="warning"
    )
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.01)

    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    cookies = {"fy_session": sess.id}

    try:
        yield base, cookies
    finally:
        server.should_exit = True
        th.join(timeout=5)


def test_unauthenticated_notifications_events_rejected(sse_server):
    """未登录用户请求通知事件流一律拒绝 (401/403)。"""
    base, _ = sse_server
    with httpx.Client(trust_env=False, timeout=5.0) as http:
        r = http.get(f"{base}/api/collaboration/notifications/events")
        assert r.status_code in (401, 403), f"期望 401/403，实际得到 {r.status_code}: {r.text}"


def test_notifications_events_stream_and_headers(sse_server):
    """已登录用户正常建连，响应头符合 SSE 规范。"""
    base, cookies = sse_server
    ch = notification_channel("owner")
    sse_bus._hist[ch].clear()
    sse_bus.publish(ch, "notification", {"unread_count": 1, "kind": "mention"})

    with httpx.Client(trust_env=False, timeout=5.0) as http:
        with http.stream("GET", f"{base}/api/collaboration/notifications/events", cookies=cookies) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert "no-store" in resp.headers.get("cache-control", "")
            lines = []
            for line in resp.iter_lines():
                if line:
                    lines.append(line)
                if len(lines) >= 3:
                    break
            assert any(l.startswith("id:") for l in lines)
            assert any(l.startswith("event:") for l in lines)
            assert any(l.startswith("data:") for l in lines)


def test_notifications_events_handles_invalid_last_event_id(sse_server):
    """非法 Last-Event-ID（非整数、负数、空串）安全降级为 0，绝不 500 崩溃。"""
    base, cookies = sse_server
    invalid_cases = [
        {"Last-Event-ID": "invalid-string"},
        {"Last-Event-ID": "NaN"},
        {"Last-Event-ID": "-10"},
        {"Last-Event-ID": "9999999999999999999999999999999999999999999999999999999999999"},
    ]
    with httpx.Client(trust_env=False, timeout=5.0) as http:
        for case in invalid_cases:
            with http.stream("GET", f"{base}/api/collaboration/notifications/events", headers=case, cookies=cookies) as resp:
                assert resp.status_code == 200, f"Last-Event-ID={case} 导致了异常响应 {resp.status_code}"

        # query 参数 last_event_id 同样安全
        with http.stream("GET", f"{base}/api/collaboration/notifications/events?last_event_id=not-a-number", cookies=cookies) as resp:
            assert resp.status_code == 200


def test_notifications_events_resumes_from_last_event_id(sse_server):
    """带有效 Last-Event-ID 时，只重放 seq > Last-Event-ID 的事件。"""
    base, cookies = sse_server
    ch = notification_channel("owner")
    sse_bus._hist[ch].clear()
    ev1 = sse_bus.publish(ch, "notification", {"n": 1})
    ev2 = sse_bus.publish(ch, "notification", {"n": 2})

    with httpx.Client(trust_env=False, timeout=5.0) as http:
        headers = {"Last-Event-ID": str(ev1.seq)}
        with http.stream("GET", f"{base}/api/collaboration/notifications/events", headers=headers, cookies=cookies) as resp:
            assert resp.status_code == 200
            received_lines = []
            for line in resp.iter_lines():
                if line:
                    received_lines.append(line)
                if any(l.startswith(f"id: {ev2.seq}") for l in received_lines) and any(l.startswith("data:") for l in received_lines):
                    break
            assert any(l == f"id: {ev2.seq}" for l in received_lines)
            assert not any(l == f"id: {ev1.seq}" for l in received_lines)
