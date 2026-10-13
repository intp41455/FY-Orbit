"""A-画布搭建器-05/06：flow_type 双形态 + 草稿/发布版本化测试（补齐包5）。

复用顶层 conftest 的内存 SQLite（``session``/``owner`` fixture）；ORM 模型在
``services/dsl_canvas.py``，import 本测试模块即注册进 Base.metadata，
create_all 建出的约束与迁移 0038 一致。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    FLOW_TYPES,
    DslFlowRow,
    DslFlowStore,
    DslFlowVersionRow,
    DslValidationError,
    make_flow_runner,
)
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed


def _chatflow_doc() -> dict:
    return {
        "version": "1",
        "flow_type": "chatflow",
        "nodes": [
            {"id": "t1", "type": "trigger",
             "params": {"kind": "conversation", "config": {"value": ""}}},
            {"id": "tp1", "type": "template", "params": {"template": "回声：{text}"}},
            {"id": "out1", "type": "output"},
        ],
        "edges": [{"from": "t1", "to": "tp1"}, {"from": "tp1", "to": "out1"}],
    }


def _workflow_doc() -> dict:
    return {
        "version": "1",
        "flow_type": "workflow",
        "nodes": [
            {"id": "t1", "type": "trigger",
             "params": {"kind": "manual", "config": {"value": {"n": 1}}}},
            {"id": "out1", "type": "output"},
        ],
        "edges": [{"from": "t1", "to": "out1"}],
    }


# --------------------------------------------------------------------------- #
# 双形态
# --------------------------------------------------------------------------- #

def test_create_flow_accepts_both_flow_types(session, owner) -> None:
    store = DslFlowStore(session)
    for ft in FLOW_TYPES:
        view = store.create_flow(owner, name=f"流程-{ft}", flow_type=ft,
                                 doc=_workflow_doc() if ft == "workflow"
                                 else _chatflow_doc())
        assert view["flow_type"] == ft
        assert view["published_version"] == 0


def test_create_flow_rejects_unknown_flow_type(session, owner) -> None:
    store = DslFlowStore(session)
    with pytest.raises(ValidationFailed):
        store.create_flow(owner, name="x", flow_type="agentflow")


def test_save_draft_can_switch_flow_type(session, owner) -> None:
    """两形态可切换：workflow 流程切到 chatflow 并以 chatflow 语义发布。"""
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="切换演示", flow_type="workflow",
                             doc=_workflow_doc())
    store.save_draft(owner, flow["id"], _chatflow_doc(), flow_type="chatflow")
    published = store.publish(owner, flow["id"])
    assert published["flow_type"] == "chatflow"
    assert published["published_version"] == 1


def test_draft_doc_flow_type_must_match_record(session, owner) -> None:
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="一致性", flow_type="workflow",
                             doc=_workflow_doc())
    with pytest.raises(ValidationFailed, match="不一致"):
        store.save_draft(owner, flow["id"], _chatflow_doc())


# --------------------------------------------------------------------------- #
# 草稿/发布/回滚
# --------------------------------------------------------------------------- #

def test_publish_requires_assembly_and_versions_are_append_only(session, owner) -> None:
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="版本化", flow_type="chatflow")
    # 草稿为空 → 不能发布
    with pytest.raises(ValidationFailed, match="草稿为空"):
        store.publish(owner, flow["id"])

    store.save_draft(owner, flow["id"], _chatflow_doc())
    v1 = store.publish(owner, flow["id"], note="首发")
    assert v1["published_version"] == 1

    # 改草稿（改模板文案）再发布 → 版本 2，历史保留
    doc = _chatflow_doc()
    doc["nodes"][1]["params"]["template"] = "回声2：{text}"
    store.save_draft(owner, flow["id"], doc)
    v2 = store.publish(owner, flow["id"], note="二次发布")
    assert v2["published_version"] == 2

    versions = store.list_versions(owner, flow["id"])
    assert [v["version"] for v in versions] == [2, 1]
    assert {v["note"] for v in versions} == {"首发", "二次发布"}


def test_publish_rejects_assembly_incomplete_draft(session, owner) -> None:
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="装配门禁", flow_type="workflow")
    bad = _workflow_doc()
    bad["nodes"].append({"id": "lost", "type": "template",
                         "params": {"template": "孤儿"}})
    store.save_draft(owner, flow["id"], bad)  # 草稿允许半成品（只查 validate_dsl）
    with pytest.raises(DslValidationError, match="不可达"):
        store.publish(owner, flow["id"])


def test_rollback_restores_draft_and_history_stays(session, owner) -> None:
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="回滚", flow_type="chatflow",
                             doc=_chatflow_doc())
    store.publish(owner, flow["id"], note="v1")

    doc = _chatflow_doc()
    doc["nodes"][1]["params"]["template"] = "第二版：{text}"
    store.save_draft(owner, flow["id"], doc)
    store.publish(owner, flow["id"], note="v2")

    rolled = store.rollback(owner, flow["id"], 1)
    assert rolled["rolled_back_to"] == 1
    # 回滚后草稿回到 v1 内容，发布指针仍是 v2
    assert rolled["published_version"] == 2
    assert rolled["draft_doc"]["nodes"][1]["params"]["template"] == "回声：{text}"

    # 回滚再发布 → 生成 v3（版本线单调递增，历史不被改写）
    v3 = store.publish(owner, flow["id"], note="回滚后再发布")
    assert v3["published_version"] == 3
    assert [v["version"] for v in store.list_versions(owner, flow["id"])] == [3, 2, 1]


def test_rollback_unknown_version_is_not_found(session, owner) -> None:
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="x", flow_type="workflow",
                             doc=_workflow_doc())
    with pytest.raises(NotFound):
        store.rollback(owner, flow["id"], 99)


def test_other_owner_cannot_see_flow(session, owner) -> None:
    from find_yourself.services.actor import Actor

    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="私有", flow_type="workflow",
                             doc=_workflow_doc())
    stranger = Actor.service("svc-stranger", "bot")
    with pytest.raises(NotFound):
        store.get_flow(stranger, flow["id"])


# --------------------------------------------------------------------------- #
# make_flow_runner（批量评估的入口适配）
# --------------------------------------------------------------------------- #

def test_make_flow_runner_covers_conversation_entry() -> None:
    runner = make_flow_runner(_chatflow_doc())
    out = runner("你好")
    assert out["status"] == "succeeded"
    assert out["output"] == "回声：你好"


def test_make_flow_runner_requires_entry() -> None:
    doc = {"version": "1",
           "nodes": [{"id": "out1", "type": "output"}],
           "edges": []}
    with pytest.raises(DslValidationError, match="入口"):
        make_flow_runner(doc)


# --------------------------------------------------------------------------- #
# 草稿乐观锁（N3 状态同步）：expected_version 参与则校验，不传则向后兼容
# --------------------------------------------------------------------------- #

def test_save_draft_with_expected_version_increments_version(session, owner) -> None:
    """带上当前版本号保存成功，返回的 draft_version 递增。"""
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="乐观锁", flow_type="chatflow")
    assert flow["draft_version"] == 0

    v1 = store.save_draft(owner, flow["id"], _chatflow_doc(), expected_version=0)
    assert v1["draft_version"] == 1

    doc = _chatflow_doc()
    doc["nodes"][1]["params"]["template"] = "回声2：{text}"
    v2 = store.save_draft(owner, flow["id"], doc, expected_version=1)
    assert v2["draft_version"] == 2
    assert v2["draft_doc"]["nodes"][1]["params"]["template"] == "回声2：{text}"


def test_save_draft_with_stale_expected_version_conflicts_and_keeps_draft(
        session, owner) -> None:
    """过期版本号 → Conflict，且 draft_version 与 draft_doc 都**不被改动**。

    只断言抛异常是不够的：冲突路径必须保证「原草稿原样保留」，
    否则一次被拒的保存仍会悄悄覆盖别人的编辑。
    """
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="过期版本", flow_type="chatflow")
    store.save_draft(owner, flow["id"], _chatflow_doc(), expected_version=0)

    stale_doc = _chatflow_doc()
    stale_doc["nodes"][1]["params"]["template"] = "过期写入：{text}"
    with pytest.raises(Conflict, match="版本"):
        store.save_draft(owner, flow["id"], stale_doc, expected_version=0)

    after = store.get_flow(owner, flow["id"])
    assert after["draft_version"] == 1, "被拒的保存不得递增版本号"
    assert after["draft_doc"]["nodes"][1]["params"]["template"] == "回声：{text}", \
        "被拒的保存不得覆盖草稿内容"


def test_save_draft_without_expected_version_stays_backward_compatible(
        session, owner) -> None:
    """expected_version 缺省（None）= 不参与乐观锁：直接覆盖，版本照常递增。"""
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="兼容", flow_type="chatflow")
    a = store.save_draft(owner, flow["id"], _chatflow_doc())
    assert a["draft_version"] == 1
    b = store.save_draft(owner, flow["id"], _chatflow_doc())
    assert b["draft_version"] == 2


def test_rollback_increments_draft_version(session, owner) -> None:
    """回滚改写了 draft_doc，因此也必须递增 draft_version（否则回滚成后门）。"""
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="回滚版本", flow_type="chatflow",
                             doc=_chatflow_doc())
    store.publish(owner, flow["id"], note="v1")
    before = store.get_flow(owner, flow["id"])["draft_version"]

    rolled = store.rollback(owner, flow["id"], 1)
    assert rolled["draft_version"] == before + 1


def test_rollback_then_stale_expected_version_conflicts(session, owner) -> None:
    """回滚后旧版本号作废：拿着回滚前的版本号再存 → Conflict。"""
    store = DslFlowStore(session)
    flow = store.create_flow(owner, name="回滚后冲突", flow_type="chatflow",
                             doc=_chatflow_doc())
    store.publish(owner, flow["id"], note="v1")
    stale = store.get_flow(owner, flow["id"])["draft_version"]
    store.rollback(owner, flow["id"], 1)
    with pytest.raises(Conflict):
        store.save_draft(owner, flow["id"], _chatflow_doc(), expected_version=stale)


# --------------------------------------------------------------------------- #
# HTTP 面：草稿真的落库 + 冲突以 HTTP 409 出头
# --------------------------------------------------------------------------- #

class _RouteServices:
    """只暴露生命周期路由用到的字段。"""

    def __init__(self, session):
        self.session = session
        self.budget = None


@pytest.fixture()
def http_client(engine, owner):
    """每个请求一个**独立 session**、请求结束即 close —— 复刻 ``deps.get_db``。

    ``tests/unit/canvas/test_batch_eval.py`` 的 ``client`` fixture 直接复用测试
    自己的 session，因此永远看不见「路由没 commit」这个缺陷：同一个连接上
    未提交的改动照样读得到。这里必须换 session 才能证伪。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from find_yourself.api.deps import csrf_protected, get_actor, get_services, get_settings
    from find_yourself.api.routes import dsl_lifecycle
    from find_yourself.config import Settings

    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    app = FastAPI()
    app.include_router(dsl_lifecycle.router)
    app.dependency_overrides[get_actor] = lambda: owner
    app.dependency_overrides[csrf_protected] = lambda: owner
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456")

    def _per_request_services():
        # 与 deps.get_db 同形：独立 session + finally close。
        s = maker()
        try:
            yield _RouteServices(s)
        finally:
            s.close()

    app.dependency_overrides[get_services] = _per_request_services
    with TestClient(app) as c:
        yield c, maker


def test_save_draft_via_http_is_actually_committed(http_client, owner) -> None:
    """HTTP 存草稿后换一个 session 读，草稿必须真的在库里。

    路由只有 ``flush()`` 而不 ``commit()`` 时，请求结束 ``session.close()``
    会把这次写入回滚掉——前端却收到 200，表现为「保存成功但刷新就没了」。
    """
    client, maker = http_client

    setup = maker()
    flow_id = DslFlowStore(setup).create_flow(
        owner, name="落库验证", flow_type="chatflow")["id"]
    setup.commit()
    setup.close()

    doc = _chatflow_doc()
    doc["nodes"][1]["params"]["template"] = "HTTP 写入：{text}"
    r = client.put(f"/api/dsl-lifecycle/flows/{flow_id}/draft",
                   json={"doc": doc, "expected_version": 0})
    assert r.status_code == 200, r.text
    assert r.json()["draft_version"] == 1

    fresh = maker()
    try:
        row = fresh.get(DslFlowRow, flow_id)
        assert row is not None
        assert row.draft_doc["nodes"][1]["params"]["template"] == "HTTP 写入：{text}", \
            "HTTP 存草稿后另开 session 读不到内容：路由缺少 commit"
        assert row.draft_version == 1
    finally:
        fresh.close()


def test_save_draft_via_http_stale_version_returns_409(http_client, owner) -> None:
    """过期版本号在 HTTP 面表现为 409 Conflict（前端据此判断版本冲突）。"""
    client, maker = http_client

    setup = maker()
    flow_id = DslFlowStore(setup).create_flow(
        owner, name="HTTP 冲突", flow_type="chatflow")["id"]
    setup.commit()
    setup.close()

    assert client.put(f"/api/dsl-lifecycle/flows/{flow_id}/draft",
                      json={"doc": _chatflow_doc(), "expected_version": 0}
                      ).status_code == 200
    r = client.put(f"/api/dsl-lifecycle/flows/{flow_id}/draft",
                   json={"doc": _chatflow_doc(), "expected_version": 0})
    assert r.status_code == 409, r.text


def test_create_flow_via_http_is_actually_committed(http_client, owner) -> None:
    """HTTP 建流程后换一个 session 读，流程必须真的在库里。

    与 save_draft 同源缺陷：store 只 flush，路由不 commit 时请求结束 close()
    会回滚新建行——前端收到 201，刷新后流程却消失。
    """
    client, maker = http_client

    r = client.post("/api/dsl-lifecycle/flows",
                    json={"name": "HTTP 建流程", "flow_type": "chatflow"})
    assert r.status_code == 201, r.text
    flow_id = r.json()["id"]

    fresh = maker()
    try:
        row = fresh.get(DslFlowRow, flow_id)
        assert row is not None, "HTTP 建流程后另开 session 读不到：路由缺少 commit"
        assert row.name == "HTTP 建流程"
        assert row.owner_id == (owner.owner_id or owner.service_id)
    finally:
        fresh.close()


def test_publish_via_http_is_actually_committed(http_client, owner) -> None:
    """HTTP 发布后换 session 读：published_version 与版本快照行都必须落库。

    发布写两处（flow 行 + 版本快照表），少任何一处 commit 都是「200 但没发布」。
    """
    client, maker = http_client

    setup = maker()
    flow_id = DslFlowStore(setup).create_flow(
        owner, name="HTTP 发布", flow_type="chatflow",
        doc=_chatflow_doc())["id"]
    setup.commit()
    setup.close()

    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/publish",
                    json={"note": "v1"})
    assert r.status_code == 200, r.text
    assert r.json()["published_version"] == 1

    fresh = maker()
    try:
        row = fresh.get(DslFlowRow, flow_id)
        assert row.published_version == 1, "发布未落库：路由缺少 commit"
        assert row.published_doc is not None
        snap = (fresh.query(DslFlowVersionRow)
                .filter(DslFlowVersionRow.flow_id == flow_id,
                        DslFlowVersionRow.version == 1).one_or_none())
        assert snap is not None, "版本快照行未落库：路由缺少 commit"
        assert snap.note == "v1"
    finally:
        fresh.close()


def test_rollback_via_http_is_actually_committed(http_client, owner) -> None:
    """HTTP 回滚后换 session 读：draft_doc 必须真的回到历史版本。

    回滚只改 draft_doc/draft_version（不动发布历史），不 commit 时草稿看起来
    没回滚——比发布更隐蔽，因为响应里的 draft_version 是回滚后的新值。
    """
    client, maker = http_client

    setup = maker()
    store = DslFlowStore(setup)
    flow_id = store.create_flow(
        owner, name="HTTP 回滚", flow_type="chatflow",
        doc=_chatflow_doc())["id"]
    store.publish(owner, flow_id, note="v1")
    # 覆盖草稿成 v2 内容，回滚后应恢复成 v1 的「回声：{text}」
    v2_doc = _chatflow_doc()
    v2_doc["nodes"][1]["params"]["template"] = "v2：{text}"
    store.save_draft(owner, flow_id, v2_doc)
    setup.commit()
    setup.close()

    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/rollback",
                    json={"version": 1})
    assert r.status_code == 200, r.text

    fresh = maker()
    try:
        row = fresh.get(DslFlowRow, flow_id)
        assert row.draft_doc["nodes"][1]["params"]["template"] == "回声：{text}", \
            "HTTP 回滚后另开 session 读到的还是 v2：路由缺少 commit"
        assert row.draft_version == 2, "回滚必须递增 draft_version"
    finally:
        fresh.close()


def test_evaluate_batch_via_http_writes_nothing_to_db(http_client, owner) -> None:
    """批量评估是**纯读 + 内存计算**：库里不应新增/改动任何流程行。

    这条不是「也缺 commit」，而是把边界钉死：``evaluate_flow_batch`` 只在内存里
    跑 runner 并汇总指标（``UnifiedEvaluator`` 无 session），``get_flow`` 是读。
    因此本路由**不该**加 commit——加了也只是提交一个空事务。
    """
    client, maker = http_client

    setup = maker()
    flow_id = DslFlowStore(setup).create_flow(
        owner, name="HTTP 评估", flow_type="chatflow",
        doc=_chatflow_doc())["id"]
    DslFlowStore(setup).publish(owner, flow_id, note="v1")
    setup.commit()
    before = setup.get(DslFlowRow, flow_id)
    before_state = (before.published_version, before.draft_version,
                    before.draft_doc, before.published_doc)
    setup.close()

    r = client.post(f"/api/dsl-lifecycle/flows/{flow_id}/evaluate-batch",
                    json={"csv": "input,expected\n你好,回声：你好\n"})
    assert r.status_code == 200, r.text
    assert r.json()["cases"][0]["success"] is True

    fresh = maker()
    try:
        row = fresh.get(DslFlowRow, flow_id)
        assert (row.published_version, row.draft_version,
                row.draft_doc, row.published_doc) == before_state, \
            "批量评估不应改动流程行"
        assert fresh.query(DslFlowVersionRow).filter(
            DslFlowVersionRow.flow_id == flow_id).count() == 1, \
            "批量评估不应新增版本快照"
    finally:
        fresh.close()
