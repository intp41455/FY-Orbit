"""A-画布搭建器-05/06：flow_type 双形态 + 草稿/发布版本化测试（补齐包5）。

复用顶层 conftest 的内存 SQLite（``session``/``owner`` fixture）；ORM 模型在
``services/dsl_canvas.py``，import 本测试模块即注册进 Base.metadata，
create_all 建出的约束与迁移 0038 一致。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    FLOW_TYPES,
    DslFlowStore,
    DslValidationError,
    make_flow_runner,
)
from find_yourself.services.errors import NotFound, ValidationFailed


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
