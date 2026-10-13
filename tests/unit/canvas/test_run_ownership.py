"""G2-e：DSL 画布 run 记录的归属校验。

``GET /api/dsl-canvas/runs/{run_id}`` 曾只按 ``run_id`` 查内存存档、**不做归属
校验**：任何已认证用户拿到别人的 ``run_id`` 就能读走该run 的DSL 全文与 output。
调试域（``/api/dsl/runs/{run_id}/state|step|node/{node_id}/run``）与画布 run 共用
同一个 :class:`DslRunStore`，因此同样需要归属校验。

范式对齐 ``tasks.get_task``（``owner_id`` 不符→404）与 ``DslFlowStore._own_flow``
（属主外一律 NotFound：不泄漏「存在但不属于你」）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from find_yourself.api.deps import csrf_protected, get_actor, get_services
from find_yourself.api.routes import dsl_canvas, dsl_debug
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
    """可切换的当前 actor —— 一个 client 内模拟「换一个人登录」。"""

    def __init__(self) -> None:
        self.actor = Actor.owner("owner-a")


class _MinimalServices:
    """只装配 ``POST /runs`` 真正摸到的字段（``svc.hitl``）。

    与 ``test_run_events.py`` 同一范式：真的 ``HitlInterruptService`` 挂内存库，
    不用 mock，免得把「读不到裁决」这条最关键的语义静默糊掉。
    """

    def __init__(self, session) -> None:
        self.session = session
        self.hitl = HitlInterruptService(session)


@pytest.fixture()
def canvas(session):
    """挂 dsl_canvas + dsl_debug 两个路由的最小 app + 可切换 actor + 干净的 run 存档。

    ``_store`` 是模块级全局单例，因此每例前后都``reset_store_for_tests()``，
    否则用例之间串味（后一例拿到前一例的 run_id）会掩盖越权。
    """
    dsl_canvas.reset_store_for_tests()
    box = _ActorBox()

    app = FastAPI()
    app.include_router(dsl_canvas.router)
    app.include_router(dsl_debug.router)
    app.dependency_overrides[get_actor] = lambda: box.actor
    app.dependency_overrides[csrf_protected] = lambda: box.actor
    app.dependency_overrides[get_services] = lambda: _MinimalServices(session)

    with TestClient(app) as c:
        yield c, box
    dsl_canvas.reset_store_for_tests()


def _submit(client: TestClient) -> str:
    r = client.post("/api/dsl-canvas/runs", json={"dsl": _DOC})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


# --------------------------------------------------------------------------- #
# 画布读端点
# --------------------------------------------------------------------------- #

def test_creator_can_read_own_run(canvas) -> None:
    """创建者本人取自己的 run → 200，且拿到完整 output。"""
    client, _box = canvas
    run_id = _submit(client)

    r = client.get(f"/api/dsl-canvas/runs/{run_id}")
    assert r.status_code == 200, r.text
    assert r.json()["output"] == "你好，张三！你今年 34 岁。"


def test_other_actor_cannot_read_run_404(canvas) -> None:
    """换个 actor 拿别人的 run_id → 404（与 tasks.py 范式一致：属主外当不存在）。"""
    client, box = canvas
    run_id = _submit(client)

    box.actor = Actor.owner("owner-b")
    r = client.get(f"/api/dsl-canvas/runs/{run_id}")
    assert r.status_code == 404, f"越权读取未被拒绝：{r.status_code} {r.text}"
    assert "nodes" not in r.text and "你好，张三" not in r.text, \
        "404 响应体里仍带着 DSL 节点 / output，说明数据泄漏没被挡住"


def test_other_actor_cannot_submit_against_others_run(canvas) -> None:
    """写端点同样受保护：owner-b不能往 owner-a 的 run 上追加单步日志。"""
    client, box = canvas
    run_id = _submit(client)

    box.actor = Actor.owner("owner-b")
    r = client.post(f"/api/dsl/runs/{run_id}/step")
    assert r.status_code == 404, r.text


def test_list_runs_only_returns_own_runs(canvas) -> None:
    """``GET /runs`` 不得把别人的 run_id  列出来（run_id 本身也是情报）。"""
    client, box = canvas
    run_id = _submit(client)

    box.actor = Actor.owner("owner-b")
    assert client.get("/api/dsl-canvas/runs").json()["items"] == []

    box.actor = Actor.owner("owner-a")
    assert [i["run_id"] for i in
            client.get("/api/dsl-canvas/runs").json()["items"]] == [run_id]


def test_other_actor_cannot_read_debug_state(canvas) -> None:
    """调试域与画布共用同一份存档：``GET /api/dsl/runs/{run_id}/state`` 同样要校验。"""
    client, box = canvas
    run_id = _submit(client)

    box.actor = Actor.owner("owner-b")
    r = client.get(f"/api/dsl/runs/{run_id}/state")
    assert r.status_code == 404, f"越权读取未被拒绝：{r.status_code} {r.text}"
    assert "你好，张三" not in r.text


def test_service_identity_cannot_read_owner_run(canvas) -> None:
    """service 身份不是 owner 的替身：绑定到别的身份的 service 也读不到。"""
    client, box = canvas
    run_id = _submit(client)

    box.actor = Actor.service("svc-1", "worker")
    assert client.get(f"/api/dsl-canvas/runs/{run_id}").status_code == 404


def test_store_get_owned_is_denied_for_other_owner(canvas) -> None:
    """store 层同样拦住（``DslRunStore.get_owned``），不只路由层。"""
    client, _box = canvas
    run_id = _submit(client)

    assert dsl_canvas._store.get_owned(run_id, "owner-a") is not None
    assert dsl_canvas._store.get_owned(run_id, "owner-b") is None
