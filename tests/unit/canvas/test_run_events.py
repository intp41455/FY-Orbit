"""G2 实时推送：``GET /runs/{run_id}/events`` 属主校验 + ``POST /runs`` 的
``stream:true`` → 202 后台执行分支。

用最小 app + 可切换 actor 的范式（对齐 ``test_run_ownership.py``），避免完整
OIDC 鉴权栈的噪声，并让「换一个人登录」成为确定性的单行操作。
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from find_yourself.api.deps import csrf_protected, get_actor, get_services
from find_yourself.api.routes import dsl_canvas
from find_yourself.services.actor import Actor
from find_yourself.services.hitl import HitlInterruptService

_DOC = {
    "version": "1",
    "nodes": [
        {"id": "src", "type": "input",
         "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]}},
        {"id": "tpl", "type": "transform", "verb": "template",
         "params": {"template": "你好，{name}！你今年 {age} 岁。"}},
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "src", "to": "tpl"}, {"from": "tpl", "to": "out"}],
}


class _ActorBox:
    def __init__(self) -> None:
        self.actor = Actor.owner("owner-a")


class _MinimalServices:
    """只装配 ``POST /runs`` 真正摸到的字段。

    ``submit_run`` 为回查人工裁决注入了 ``Depends(get_services)``；它只用
    ``svc.hitl``。刻意用**真的** :class:`HitlInterruptService`（挂 conftest 的
    内存库）而不是 ``None``/``MagicMock``——否则一旦路由开始读裁决，这个fixture
    会静默返回 ``None`` 把bug 藏起来，而这里最该守住的就是「读不到 ≠ 同意」。

    生产上 ``get_actor`` 本身就 ``Depends(get_services)``，本app 没有额外引入
    DB 依赖；之所以要显式覆盖，是因为这里把 ``get_actor``/``csrf_protected``
    整条替换掉了，``get_services`` 就成了唯一还会走 ``app.state`` 的入口。
    """

    def __init__(self, session) -> None:
        self.session = session
        self.hitl = HitlInterruptService(session)


@pytest.fixture()
def canvas(session):
    dsl_canvas.reset_store_for_tests()
    box = _ActorBox()
    app = FastAPI()
    app.include_router(dsl_canvas.router)
    app.dependency_overrides[get_actor] = lambda: box.actor
    app.dependency_overrides[csrf_protected] = lambda: box.actor
    app.dependency_overrides[get_services] = lambda: _MinimalServices(session)
    with TestClient(app) as c:
        yield c, box
    dsl_canvas.reset_store_for_tests()


@pytest.fixture()
def canvas_live(session) -> Iterator[tuple[str, _ActorBox]]:
    """起**真实监听端口**的最小 app，供SSE 长连接用例使用。

    ``TestClient`` 走 ``portal.call(self.app, scope, receive, send)``——同步阻塞
    跑完整个 ASGI app。``GET /runs/{run_id}/events`` 返回的是 ``StreamingResponse``
    + 无限生成器（``bus.subscribe`` 靠 ``heartbeat_interval`` 超时发心跳），app
    永不返回，TestClient 就永不返回，用例死等。因此 SSE 必须走真实端口 + httpx
    （与 ``tests/api/test_tasks.py::test_sse_event_stream_real_listening_port``
    同一范式）。SSE 的长连接语义保持不变：不消费 body，只断言响应头。

    ``session`` 跨线程共用是安全的：conftest 的内存库用 ``StaticPool`` +
    ``check_same_thread=False``，且本fixture 的用例都不带 ``execution_id``，
    ``_approval_signal`` 会直接返回 ``None``，根本不会去查这张表。
    """
    dsl_canvas.reset_store_for_tests()
    box = _ActorBox()
    app = FastAPI()
    app.include_router(dsl_canvas.router)
    app.dependency_overrides[get_actor] = lambda: box.actor
    app.dependency_overrides[csrf_protected] = lambda: box.actor
    app.dependency_overrides[get_services] = lambda: _MinimalServices(session)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    # 轮询等端口就绪，设上限避免启动异常时死等（宁可失败也不挂起）。
    for _ in range(100):
        if getattr(server, "servers", None):
            break
        time.sleep(0.1)
    assert getattr(server, "servers", None), "uvicorn 未能在 10s 内绑定端口"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", box
    finally:
        server.should_exit = True
        th.join(timeout=5)
        dsl_canvas.reset_store_for_tests()


def _submit_sync(client: TestClient) -> str:
    r = client.post("/api/dsl-canvas/runs", json={"dsl": _DOC})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


# --------------------------------------------------------------------------- #
# POST /runs · stream:true → 202 后台执行
# --------------------------------------------------------------------------- #

def test_stream_true_returns_202_running(canvas):
    client, _box = canvas
    r = client.post("/api/dsl-canvas/runs", json={"dsl": _DOC, "stream": True})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["run_id"]


def test_submit_without_stream_keeps_sync_200(canvas):
    """向后兼容：缺省 / ``stream:false`` 保持同步 200 行为。"""
    client, _box = canvas
    r = client.post("/api/dsl-canvas/runs", json={"dsl": _DOC})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"

    r = client.post("/api/dsl-canvas/runs", json={"dsl": _DOC, "stream": False})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"


# --------------------------------------------------------------------------- #
# GET /runs/{run_id}/events · 属主校验
# --------------------------------------------------------------------------- #

def test_events_owner_can_open_stream(canvas_live):
    """同步提交的 run：属主本人可开 SSE 流（真实端口，见 ``canvas_live``）。"""
    base, _box = canvas_live
    with httpx.Client(trust_env=False, timeout=10.0) as http:
        r = http.post(f"{base}/api/dsl-canvas/runs", json={"dsl": _DOC})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        # 不消费 body：响应体是无限流，只断言响应头后退出上下文关闭连接。
        with http.stream("GET", f"{base}/api/dsl-canvas/runs/{run_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")


def test_events_other_owner_404(canvas):
    client, box = canvas
    run_id = _submit_sync(client)
    box.actor = Actor.owner("owner-b")
    r = client.get(f"/api/dsl-canvas/runs/{run_id}/events")
    assert r.status_code == 404, f"越权订阅未被拒绝：{r.status_code} {r.text}"


def test_events_unknown_run_404(canvas):
    client, _box = canvas
    r = client.get("/api/dsl-canvas/runs/does-not-exist/events")
    assert r.status_code == 404


def test_events_authorized_for_in_flight_stream_run(canvas_live):
    """202 后台运行中（结果尚未落 store）也能靠进程内属主登记表校验通过。"""
    base, _box = canvas_live
    with httpx.Client(trust_env=False, timeout=10.0) as http:
        r = http.post(f"{base}/api/dsl-canvas/runs", json={"dsl": _DOC, "stream": True})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        # 同上：不消费无限 body，仅断言订阅被接受。
        with http.stream("GET", f"{base}/api/dsl-canvas/runs/{run_id}/events") as resp:
            assert resp.status_code == 200
