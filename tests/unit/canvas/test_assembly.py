"""A-画布搭建器-02：装配完整性 + 触发器/入口语义测试（补齐包5）。"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    FLOW_TYPES,
    DslValidationError,
    flow_type_of,
    validate_assembly,
)


def _conv_trigger(node_id: str = "t1", value: str = "hi") -> dict:
    return {"id": node_id, "type": "trigger",
            "params": {"kind": "conversation", "config": {"value": value}}}


def _manual_trigger(node_id: str = "t1") -> dict:
    return {"id": node_id, "type": "trigger",
            "params": {"kind": "manual", "config": {"value": "go"}}}


def _doc(nodes: list[dict], edges: list[dict], flow_type: str | None = None) -> dict:
    doc = {"version": "1", "nodes": nodes, "edges": edges}
    if flow_type:
        doc["flow_type"] = flow_type
    return doc


_OUT = {"id": "out1", "type": "output"}


def test_assembly_ok_for_minimal_workflow() -> None:
    doc = _doc([_manual_trigger(), _OUT], [{"from": "t1", "to": "out1"}], "workflow")
    report = validate_assembly(doc)
    assert report["flow_type"] == "workflow"
    assert report["entry_nodes"] == ["t1"]
    assert report["reachable"] == 2


def test_assembly_rejects_orphan_node() -> None:
    doc = _doc([_manual_trigger(), _OUT, {"id": "lost", "type": "template",
                                          "params": {"template": "x"}}],
               [{"from": "t1", "to": "out1"}], "workflow")
    with pytest.raises(DslValidationError, match="不可达"):
        validate_assembly(doc)


def test_assembly_requires_an_entry_node() -> None:
    doc = _doc([{"id": "mid", "type": "template", "params": {"template": "x"}}, _OUT],
               [{"from": "mid", "to": "out1"}])
    with pytest.raises(DslValidationError, match="入口节点"):
        validate_assembly(doc)


def test_trigger_node_must_not_have_incoming_edge() -> None:
    doc = _doc([{"id": "in1", "type": "input", "params": {"kind": "literal",
                                                          "value": "x"}},
                _manual_trigger(), _OUT],
               [{"from": "in1", "to": "t1"}, {"from": "t1", "to": "out1"}])
    with pytest.raises(DslValidationError, match="不允许有入边"):
        validate_assembly(doc)


def test_assembly_requires_an_output_node() -> None:
    doc = _doc([_manual_trigger(), {"id": "mid", "type": "template",
                                    "params": {"template": "x"}}],
               [{"from": "t1", "to": "mid"}])
    with pytest.raises(DslValidationError, match="output"):
        validate_assembly(doc)


def test_chatflow_requires_exactly_one_conversation_trigger() -> None:
    ok = _doc([_conv_trigger(), _OUT], [{"from": "t1", "to": "out1"}], "chatflow")
    assert validate_assembly(ok)["conversation_triggers"] == ["t1"]

    none_ = _doc([_manual_trigger(), _OUT], [{"from": "t1", "to": "out1"}], "chatflow")
    with pytest.raises(DslValidationError, match="conversation"):
        validate_assembly(none_)

    two = _doc([_conv_trigger("t1"), _conv_trigger("t2"), _OUT],
               [{"from": "t1", "to": "out1"}, {"from": "t2", "to": "out1"}],
               "chatflow")
    with pytest.raises(DslValidationError, match="conversation"):
        validate_assembly(two)


def test_workflow_rejects_conversation_trigger() -> None:
    doc = _doc([_conv_trigger(), _OUT], [{"from": "t1", "to": "out1"}], "workflow")
    with pytest.raises(DslValidationError, match="chatflow"):
        validate_assembly(doc)


def test_explicit_flow_type_argument_wins_over_doc_field() -> None:
    doc = _doc([_conv_trigger(), _OUT], [{"from": "t1", "to": "out1"}], "chatflow")
    # 文档字段是 chatflow → 直接校验通过。
    assert validate_assembly(doc)["flow_type"] == "chatflow"
    # 显式 workflow 参数优先于文档字段 + conversation 触发器 → 必须拒绝。
    with pytest.raises(DslValidationError):
        validate_assembly(doc, flow_type="workflow")


def test_input_node_counts_as_entry_for_chatflow_absent_trigger() -> None:
    doc = _doc([{"id": "in1", "type": "input",
                 "params": {"kind": "literal", "value": "x"}}, _OUT],
               [{"from": "in1", "to": "out1"}], "chatflow")
    # chatflow 没有 conversation 触发器 → 拒绝（哪怕有 input 入口）。
    with pytest.raises(DslValidationError):
        validate_assembly(doc)


def test_flow_type_of_defaults_to_workflow() -> None:
    assert flow_type_of({"version": "1", "nodes": [], "edges": []}) == "workflow"
    assert flow_type_of({"version": "1", "nodes": [], "edges": [],
                         "flow_type": "chatflow"}) == "chatflow"
    assert FLOW_TYPES == ("chatflow", "workflow")
