"""A-三重模式-02 / A-代码SDK-01 · 代码优先 SDK 单测。

覆盖：StateSchema（声明/校验/JSON Schema）、Reducer 注册、GraphBuilder
代码定义图（强类型 IR 同源闸门）、状态折叠、三重模式注册框架，以及
「画布 ↔ 代码同源」：SDK 装配的文档与画布执行结果一致，且可经
``export_dsl_code`` 导出后由 ``parse_dsl_code`` 无损解析回同一张画布。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import run_dsl
from find_yourself.services.dsl_code_export import export_dsl_code, parse_dsl_code
from find_yourself.services.dsl_ir import Diagnostic
from find_yourself.services.dsl_sdk import (
    AuthoringMode,
    CodeWorkflow,
    DslSdkError,
    GraphBuilder,
    REDUCER_REGISTRY,
    StateField,
    StateSchema,
    get_reducer,
    mode_entries,
    mode_overview,
    register_mode_entry,
    register_reducer,
)

from _dsl_docs import doc_of, lit, out, xf


# =========================================================================== #
# StateSchema
# =========================================================================== #
class TestStateSchema:
    def _schema(self) -> StateSchema:
        return StateSchema([
            StateField("topic", "string", required=True, default="未命名"),
            StateField("scores", "array", default=[]),
            StateField("total", "number", default=0),
        ])

    def test_initial_state_uses_defaults(self):
        schema = self._schema()
        assert schema.initial_state() == {"topic": "未命名", "scores": [],
                                          "total": 0}
        assert schema.validate(schema.initial_state()) == []

    def test_rejects_duplicate_names(self):
        with pytest.raises(DslSdkError, match="重复"):
            StateSchema([StateField("a", "string"), StateField("a", "string")])

    def test_rejects_unknown_type(self):
        with pytest.raises(DslSdkError, match="类型"):
            StateSchema([StateField("a", "dataframe")])

    def test_required_field_must_have_default(self):
        with pytest.raises(DslSdkError, match="required"):
            StateSchema([StateField("a", "string", required=True)])

    def test_default_must_match_type(self):
        with pytest.raises(DslSdkError, match="不匹配"):
            StateSchema([StateField("n", "number", default="三")])

    def test_validate_collects_all_diagnostics(self):
        diags = self._schema().validate({"topic": 123, "total": "零",
                                         "ghost": True})
        codes = {(d.field_path, d.code) for d in diags}
        assert ("state.topic", "invalid_type") in codes
        assert ("state.total", "invalid_type") in codes
        assert ("state.ghost", "extra_field") in codes

    def test_validate_missing_required(self):
        diags = self._schema().validate({"scores": []})
        assert [(d.code, d.message) for d in diags] == [
            ("missing_field", "状态缺少必填字段 topic")]

    def test_validate_non_object_state(self):
        diags = self._schema().validate([1, 2])
        assert diags[0].code == "invalid_type"

    def test_diagnostics_reuse_dsl_ir_model(self):
        """诊断复用 dsl_ir.Diagnostic（同一模型，前端一套渲染）。"""
        diags = self._schema().validate({"ghost": 1})
        assert all(isinstance(d, Diagnostic) for d in diags)

    def test_to_json_schema(self):
        schema = self._schema().to_json_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["topic"]
        assert schema["properties"]["scores"]["type"] == "array"


# =========================================================================== #
# Reducer
# =========================================================================== #
class TestReducer:
    def test_register_and_get(self):
        def collect(state, output):
            return {**state, "last": output}
        register_reducer("test_collect", collect)
        assert get_reducer("test_collect") is collect

    def test_duplicate_registration_rejected(self):
        def fn(state, output):
            return state
        register_reducer("test_dup", fn)
        with pytest.raises(DslSdkError, match="已注册"):
            register_reducer("test_dup", fn)

    def test_signature_must_take_state_and_output(self):
        with pytest.raises(DslSdkError, match="两个位置参数"):
            register_reducer("test_bad_sig", lambda state: state)

    def test_unknown_reducer_lookup_fails_loudly(self):
        with pytest.raises(DslSdkError, match="未注册"):
            get_reducer("no_such_reducer_xyz")


# =========================================================================== #
# GraphBuilder 代码定义图 + 同源一致性
# =========================================================================== #
def _builder_doc() -> dict:
    g = GraphBuilder()
    g.input_node("src", kind="literal",
                 value=[{"name": "张三", "score": 90}, {"name": "李四", "score": 40}])
    g.transform_node("hi", "filter", field="score", op="gt", value=60)
    g.transform_node("tag", "map", op="set", field="grade", value="优:{name}")
    g.output_node("out")
    g.edge("src", "hi")
    g.edge("hi", "tag")
    g.edge("tag", "out")
    return g


class TestGraphBuilderSameSource:
    def test_code_defined_doc_is_valid_platform_dsl(self):
        doc = _builder_doc().to_document()
        assert doc["version"] == "1"
        assert {n["id"] for n in doc["nodes"]} == {"src", "hi", "tag", "out"}

    def test_sdk_run_matches_canvas_execution(self):
        """同源：SDK 装配的图与同一张画布文档在 run_dsl 下结果一致。"""
        doc = _builder_doc().to_document()
        wf_result = CodeWorkflow(doc).run()
        canvas_result = run_dsl(doc)
        assert wf_result.status == canvas_result.status == "succeeded"
        assert wf_result.output == canvas_result.output

    def test_canvas_to_code_to_canvas_roundtrip(self):
        """画布 DSL → SDK 执行 == 导出代码 → 解析回画布 → 执行（双向同源）。"""
        doc = _builder_doc().to_document()
        via_sdk = CodeWorkflow(doc).run().output
        round_tripped = parse_dsl_code(export_dsl_code(doc)["code"])
        assert round_tripped == doc
        assert run_dsl(round_tripped).output == via_sdk

    def test_ir_gate_rejects_extra_top_level_field(self):
        g = _builder_doc()
        g._nodes.append({"layout": {}})  # 视图状态混入 DSL → 违约
        with pytest.raises(Exception, match="layout"):
            g.to_document()

    def test_ir_gate_rejects_unknown_verb(self):
        g = GraphBuilder()
        g.input_node("src", kind="literal", value=1)
        g.transform_node("t", "shell")
        g.output_node("out")
        g.edge("src", "t")
        g.edge("t", "out")
        with pytest.raises(Exception, match="verb"):
            g.to_document()

    def test_duplicate_node_id_rejected(self):
        g = GraphBuilder()
        g.input_node("src", kind="literal", value=1)
        with pytest.raises(DslSdkError, match="重复"):
            g.input_node("src", kind="literal", value=2)


# =========================================================================== #
# 状态折叠（reducer 消费执行日志）
# =========================================================================== #
class TestStateFolding:
    def test_reducer_folds_succeeded_outputs_in_log_order(self):
        def collect(state, output):
            return {**state, "collected": [*state["collected"], output]}
        register_reducer("test_collect_outputs", collect)

        g = GraphBuilder()
        g.input_node("src", kind="literal", value=[1, 2, 3])
        g.transform_node("cnt", "aggregate", op="count")
        g.output_node("out", format="text")
        g.edge("src", "cnt")
        g.edge("cnt", "out")
        wf = g.build(state_schema=StateSchema([
            StateField("collected", "array", default=[])]),
            node_reducers={"cnt": "test_collect_outputs",
                           "out": "test_collect_outputs"})
        result = wf.run()
        assert result.status == "succeeded"
        # 日志序折叠：cnt 输出 3，out（text）输出 "3"
        assert result.state["collected"] == [3, "3"]
        assert result.state_diagnostics == []

    def test_skipped_node_is_not_folded(self):
        def mark(state, output):
            return {**state, "seen": [*state["seen"], output]}
        register_reducer("test_mark", mark)
        doc = doc_of(
            [lit({"mode": "fast"}),
             xf("br", "branch", field="mode", op="eq", value="fast",
                then_label="fast", else_label="slow"),
             xf("slow", "template", template="慢:{value.mode}"),
             out()],
            [{"from": "src", "to": "br"},
             {"from": "br", "to": "slow",
              "condition": {"field": "branch", "op": "eq", "value": "slow"}},
             {"from": "slow", "to": "out"}])
        wf = CodeWorkflow(doc, state_schema=StateSchema([
            StateField("seen", "array", default=[])]),
            node_reducers={"slow": "test_mark", "out": "test_mark"})
        result = wf.run()
        assert result.status == "succeeded"
        # slow 被条件边跳过（其下游 out 也随之 skipped）→ 两者都不折叠
        statuses = {log.node_id: log.status for log in result.result.logs}
        assert statuses["slow"] == "skipped"
        assert statuses["out"] == "skipped"
        assert result.state["seen"] == []

    def test_invalid_final_state_returns_diagnostics(self):
        def corrupt(state, output):
            return {**state, "count": "不是数字"}
        register_reducer("test_corrupt", corrupt)
        g = GraphBuilder()
        g.input_node("src", kind="literal", value=[1])
        g.transform_node("cnt", "aggregate", op="count")
        g.output_node("out")
        g.edge("src", "cnt")
        g.edge("cnt", "out")
        wf = g.build(state_schema=StateSchema([
            StateField("count", "number", default=0)]),
            node_reducers={"cnt": "test_corrupt"})
        result = wf.run()
        assert result.status == "succeeded"  # 执行本身没失败
        assert [(d.code, d.field_path) for d in result.state_diagnostics] == \
            [("invalid_type", "state.count")]

    def test_node_reducers_reference_unknown_node_rejected(self):
        g = _builder_doc()
        with pytest.raises(DslSdkError, match="未定义节点"):
            g.build(node_reducers={"ghost": "anything"})


# =========================================================================== #
# 三重模式注册框架
# =========================================================================== #
class TestAuthoringModes:
    def test_enum_has_three_modes(self):
        assert {m.value for m in AuthoringMode} == \
            {"beginner", "technical", "enterprise"}

    def test_overview_seeds_three_builtin_entries(self):
        overview = mode_overview()
        assert [m["mode"] for m in overview] == \
            ["beginner", "technical", "enterprise"]
        for mode in overview:
            assert mode["entries"] and mode["entries"][0]["builtin"] is True
            assert mode["label"]

    def test_register_entry_appears_in_overview(self):
        register_mode_entry(AuthoringMode.TECHNICAL, "test-entry",
                            label="测试入口", description="单测")
        entries = [e for e in mode_entries(AuthoringMode.TECHNICAL)
                   if e.entry_id == "test-entry"]
        assert len(entries) == 1
        assert entries[0].to_dict()["mode"] == "technical"

    def test_duplicate_entry_rejected(self):
        register_mode_entry(AuthoringMode.BEGINNER, "test-dup", label="x")
        with pytest.raises(DslSdkError, match="已注册"):
            register_mode_entry(AuthoringMode.BEGINNER, "test-dup", label="y")

    def test_bad_entry_id_rejected(self):
        with pytest.raises(DslSdkError, match="entry_id"):
            register_mode_entry(AuthoringMode.BEGINNER, "1 坏名字", label="x")

    def test_entries_stable_sorted(self):
        register_mode_entry(AuthoringMode.ENTERPRISE, "zzz", label="z")
        register_mode_entry(AuthoringMode.ENTERPRISE, "aaa", label="a")
        ids = [e.entry_id for e in mode_entries(AuthoringMode.ENTERPRISE)]
        assert ids == sorted(ids)
