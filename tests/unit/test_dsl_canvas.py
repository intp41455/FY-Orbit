"""P1-18 受限 DSL 画布：编译器与执行引擎单测。"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    DslValidationError,
    compile_dsl,
    run_dsl,
)


def _three_node_doc() -> dict:
    """input → transform(template) → output 三节点流。"""
    return {
        "version": "1",
        "nodes": [
            {"id": "src", "type": "input",
             "params": {"kind": "literal", "value": [
                 {"name": "张三", "age": 34},
                 {"name": "李四", "age": 27},
             ]}},
            {"id": "tpl", "type": "transform", "verb": "template",
             "params": {"template": "你好，{name}！你今年 {age} 岁。"}},
            {"id": "out", "type": "output", "params": {"format": "text"}},
        ],
        "edges": [
            {"from": "src", "to": "tpl"},
            {"from": "tpl", "to": "out"},
        ],
    }


class TestCompiler:
    def test_topological_order(self):
        plan = compile_dsl(_three_node_doc())
        assert plan.order == ["src", "tpl", "out"]

    def test_cycle_detection(self):
        doc = {
            "version": "1",
            "nodes": [{"id": c, "type": "output"} for c in "ab"],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        }
        with pytest.raises(DslValidationError, match="环"):
            compile_dsl(doc)

    def test_unknown_node_reference(self):
        doc = {
            "version": "1",
            "nodes": [{"id": "a", "type": "output"}],
            "edges": [{"from": "a", "to": "ghost"}],
        }
        with pytest.raises(DslValidationError, match="未定义节点"):
            compile_dsl(doc)

    def test_invalid_verb_rejected(self):
        doc = {
            "version": "1",
            "nodes": [{"id": "t", "type": "transform", "verb": "llm_magic"}],
            "edges": [],
        }
        with pytest.raises(DslValidationError):
            compile_dsl(doc)


class TestExecutor:
    def test_three_node_template_flow(self):
        """验收主线：input→transform(template)→output 真实执行输出符合模板预期。"""
        result = run_dsl(_three_node_doc(), run_id="t-main")
        assert result.status == "succeeded", result.error
        assert result.output == "你好，张三！你今年 34 岁。\n你好，李四！你今年 27 岁。"
        statuses = {l.node_id: l.status for l in result.logs}
        assert statuses == {"src": "succeeded", "tpl": "succeeded", "out": "succeeded"}
        # 逐步日志含输入输出
        tpl_log = next(l for l in result.logs if l.node_id == "tpl")
        assert tpl_log.input == [
            {"name": "张三", "age": 34}, {"name": "李四", "age": 27}]
        assert tpl_log.output[0] == "你好，张三！你今年 34 岁。"

    def test_filter_and_map(self):
        doc = {
            "version": "1",
            "nodes": [
                {"id": "in", "type": "input", "params": {"kind": "literal", "value": [
                    {"name": "a", "score": 90}, {"name": "b", "score": 40}]}},
                {"id": "hi", "type": "transform", "verb": "filter",
                 "params": {"field": "score", "op": "gt", "value": 60}},
                {"id": "tag", "type": "transform", "verb": "map",
                 "params": {"op": "set", "field": "grade", "value": "优秀:{name}"}},
                {"id": "out", "type": "output", "params": {"format": "json"}},
            ],
            "edges": [
                {"from": "in", "to": "hi"}, {"from": "hi", "to": "tag"},
                {"from": "tag", "to": "out"},
            ],
        }
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == [{"name": "a", "score": 90, "grade": "优秀:a"}]

    def test_conditional_branch_dynamic_expansion(self):
        """条件分支：不满足条件的后继节点被跳过（动态展开而非静态计划）。"""
        doc = {
            "version": "1",
            "nodes": [
                {"id": "in", "type": "input",
                 "params": {"kind": "literal", "value": {"mode": "fast"}}},
                {"id": "fast", "type": "transform", "verb": "template",
                 "params": {"template": "快速路径"}},
                {"id": "slow", "type": "transform", "verb": "template",
                 "params": {"template": "慢速路径"}},
                {"id": "out", "type": "output", "params": {"format": "text"}},
            ],
            "edges": [
                {"from": "in", "to": "fast",
                 "condition": {"field": "mode", "op": "eq", "value": "fast"}},
                {"from": "in", "to": "slow",
                 "condition": {"field": "mode", "op": "eq", "value": "slow"}},
                {"from": "fast", "to": "out"},
                {"from": "slow", "to": "out"},
            ],
        }
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == "快速路径"
        statuses = {l.node_id: l.status for l in result.logs}
        assert statuses["fast"] == "succeeded"
        assert statuses["slow"] == "skipped"

    def test_node_failure_recorded(self):
        """上游输出为 None 时 transform 失败，错误写入节点日志。"""
        doc = {
            "version": "1",
            "nodes": [
                {"id": "in", "type": "input", "params": {"kind": "literal"}},
                {"id": "m", "type": "transform", "verb": "template",
                 "params": {"template": "hi {name}"}},
            ],
            "edges": [{"from": "in", "to": "m"}],
        }
        result = run_dsl(doc)
        assert result.status == "failed"
        log = result.logs[0]
        assert log.status == "succeeded"  # input 成功
        log2 = result.logs[1]
        assert log2.status == "failed"
        assert "无上游输入" in (log2.error or "")
        assert "节点 m 执行失败" in (result.error or "")

    def test_run_id_and_dict_shape(self):
        result = run_dsl(_three_node_doc(), run_id="shape-1")
        payload = result.to_dict()
        assert payload["run_id"] == "shape-1"
        assert payload["status"] == "succeeded"
        assert payload["dsl"]["version"] == "1"
        assert len(payload["logs"]) == 3
        assert {"node_id", "node_type", "status", "input", "output"} <= set(
            payload["logs"][0].keys())
