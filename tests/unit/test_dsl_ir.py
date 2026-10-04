"""DSL 强类型 IR（ADR-02 第一切片）单测。

钉住四件事：

1. **每个动词的一进一出**——9 个动词各自「合法 params 通过 / 非法 params 产出
   带 ``node_id`` + ``field_path`` 的诊断」，证明 IR 真的强类型化而非摆设。
2. **``extra="forbid"`` 三层封闭**——文档级 / 节点级 / 参数级的多余字段一律被拒。
3. **错误精确可定位**——诊断必须落到「具体节点 + 具体字段」，不是笼统报错。
4. **既有 DSL 文档回归 0 破坏**——覆盖全部动词的 kitchen-sink 画布仍返回空诊断，
   且 :func:`compile_dsl` 对这种画布行为不变。

变异判据：若把 :func:`validate_ir` 改成恒返回 ``[]``，本文件所有
``assert diags`` / ``assert any(...)`` 断言必须变红。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    TRANSFORM_VERBS,
    VERB_REGISTRY,
    DslValidationError,
    compile_dsl,
    run_dsl,
    validate_dsl,
)
from find_yourself.services.dsl_ir import (
    NODE_PARAMS_MODELS,
    TRANSFORM_PARAMS_MODELS,
    Diagnostic,
    assert_ir_valid,
    format_diagnostics,
    params_model,
    validate_ir,
)


# --------------------------------------------------------------------------- #
# 构造器
# --------------------------------------------------------------------------- #
def _doc(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"version": "1", "nodes": nodes, "edges": edges or []}


def _xf(node_id: str, verb: str, **params) -> dict:
    return {"id": node_id, "type": "transform", "verb": verb, "params": params}


VALID_PARAMS: dict[str, dict] = {
    "map": {"op": "upper"},
    "filter": {"field": "score", "op": "gt", "value": 60},
    "template": {"template": "hi {name}"},
    "branch": {"field": "m", "op": "eq", "value": "fast",
               "then_label": "a", "else_label": "b"},
    "aggregate": {"op": "count"},
    "merge": {"mode": "concat"},
    "agent": {"agent": "reviewer"},
    "confirm": {"prompt": "确认？"},
    "artifact": {"name": "报表"},
}

# (verb, 非法 params, 期望字段路径, 期望错误码)
INVALID_PARAMS: list[tuple[str, dict, str, str]] = [
    ("map", {"op": "bogus"}, "params.op", "invalid_enum"),
    ("map", {"op": "set"}, "params", "cross_field"),
    ("filter", {"op": "gt"}, "params.field", "missing_field"),
    ("template", {"template": ""}, "params.template", "empty_string"),
    ("branch", {"field": "m", "op": "eq", "then_label": "a"},
     "params.else_label", "missing_field"),
    ("aggregate", {"op": "nope"}, "params.op", "invalid_enum"),
    ("aggregate", {"op": "sum"}, "params", "cross_field"),
    ("merge", {}, "params.mode", "missing_field"),
    ("agent", {"agent": ""}, "params.agent", "empty_string"),
    ("confirm", {"prompt": ""}, "params.prompt", "empty_string"),
    ("artifact", {"name": ""}, "params.name", "empty_string"),
]


def _find(diags: list[Diagnostic], node_id: str, field_path: str,
          code: str) -> Diagnostic | None:
    return next((d for d in diags
                 if d.node_id == node_id and d.field_path == field_path
                 and d.code == code), None)


# =========================================================================== #
# 1. 每个动词：合法 → 空诊断
# =========================================================================== #
class TestEveryVerbAcceptsValidParams:
    @pytest.mark.parametrize("verb", list(VALID_PARAMS))
    def test_valid_params_produce_no_diagnostic(self, verb: str):
        doc = _doc([_xf("t", verb, **VALID_PARAMS[verb])])
        assert validate_ir(doc) == []

    def test_input_and_output_valid_params(self):
        doc = _doc([
            {"id": "src", "type": "input",
             "params": {"kind": "literal", "value": {"a": 1}}},
            {"id": "out", "type": "output", "params": {"format": "text"}},
        ], [{"from": "src", "to": "out"}])
        assert validate_ir(doc) == []

    def test_input_output_may_omit_params(self):
        doc = _doc([{"id": "src", "type": "input"},
                    {"id": "out", "type": "output"}],
                   [{"from": "src", "to": "out"}])
        assert validate_ir(doc) == []


# =========================================================================== #
# 2. 每个动词：非法 → 带 node_id + field_path 的诊断
# =========================================================================== #
class TestEveryVerbRejectsInvalidParams:
    @pytest.mark.parametrize("verb,params,field,code", INVALID_PARAMS)
    def test_invalid_params_yield_precise_diagnostic(self, verb: str, params: dict,
                                                     field: str, code: str):
        doc = _doc([_xf("t", verb, **params)])
        diags = validate_ir(doc)
        # 变异判据：validate_ir 恒返回 [] 时此断言必红。
        assert diags, f"{verb} 的非法参数未产出任何诊断"
        hit = _find(diags, "t", field, code)
        assert hit is not None, [d.to_dict() for d in diags]
        assert hit.message  # 必须有人类可读说明

    @pytest.mark.parametrize("verb", list(TRANSFORM_VERBS))
    def test_every_registered_verb_has_a_derived_params_model(self, verb: str):
        assert verb in TRANSFORM_PARAMS_MODELS
        assert params_model(verb) is TRANSFORM_PARAMS_MODELS[verb]

    @pytest.mark.parametrize("verb,params,field,code", INVALID_PARAMS)
    def test_diagnostic_never_uses_a_bare_string_error(self, verb: str, params: dict,
                                                       field: str, code: str):
        """诊断是结构化的（node_id/field_path/code），不是笼统一句话。"""
        d = _find(validate_ir(_doc([_xf("t", verb, **params)])), "t", field, code)
        assert d is not None
        assert d.node_id == "t" and d.field_path.startswith("params")


# =========================================================================== #
# 3. 节点级 / 文档级封闭（extra="forbid"）
# =========================================================================== #
class TestExtraFieldsForbidden:
    def test_unknown_param_is_rejected(self):
        doc = _doc([_xf("t", "template", template="x", evil="y")])
        diags = validate_ir(doc)
        assert _find(diags, "t", "params.evil", "extra_field") is not None

    def test_unknown_node_level_field_is_rejected(self):
        """视图态（layout）不得混进 DSL —— 模型/视图分离。"""
        doc = _doc([{"id": "t", "type": "template", "verb": "template",
                     "params": {"template": "x"}, "layout": {"x": 1}}])
        diags = validate_ir(doc)
        assert _find(diags, "t", "layout", "extra_field") is not None

    def test_unknown_document_level_field_is_rejected(self):
        doc = _doc([_xf("t", "template", template="x")])
        doc["layout"] = {"t": {"x": 1}}
        diags = validate_ir(doc)
        assert _find(diags, "", "layout", "extra_field") is not None

    def test_unknown_edge_field_is_rejected(self):
        doc = _doc([{"id": "src", "type": "input"},
                    {"id": "out", "type": "output"}],
                   [{"from": "src", "to": "out", "weight": 3}])
        diags = validate_ir(doc)
        assert _find(diags, "src", "weight", "extra_field") is not None


# =========================================================================== #
# 4. 其它结构性诊断
# =========================================================================== #
class TestStructuralDiagnostics:
    def test_non_object_document(self):
        diags = validate_ir(["not", "a", "dict"])
        assert len(diags) == 1
        assert diags[0].code == "document_not_object"
        assert diags[0].node_id == ""

    def test_bad_version(self):
        doc = _doc([_xf("t", "map", op="upper")])
        doc["version"] = "2"
        assert _find(validate_ir(doc), "", "version", "invalid_version") is not None

    def test_empty_nodes(self):
        assert _find(validate_ir({"version": "1", "nodes": [], "edges": []}),
                     "", "nodes", "invalid_type") is not None

    def test_unknown_verb(self):
        doc = _doc([{"id": "t", "type": "transform", "verb": "translate",
                     "params": {}}])
        diags = validate_ir(doc)
        hit = _find(diags, "t", "verb", "unknown_verb")
        assert hit is not None
        assert "translate" in hit.message  # 回显实际值，便于自我修正

    def test_missing_verb(self):
        doc = _doc([{"id": "t", "type": "transform", "params": {"op": "upper"}}])
        assert _find(validate_ir(doc), "t", "verb", "missing_verb") is not None

    def test_transform_params_must_be_object(self):
        doc = _doc([{"id": "t", "type": "transform", "verb": "map", "params": 5}])
        assert _find(validate_ir(doc), "t", "params", "invalid_type") is not None

    def test_transform_params_may_not_be_absent(self):
        doc = _doc([{"id": "t", "type": "transform", "verb": "map"}])
        assert _find(validate_ir(doc), "t", "params", "invalid_type") is not None

    def test_unknown_node_type(self):
        doc = _doc([{"id": "t", "type": "loop", "params": {}}])
        assert _find(validate_ir(doc), "t", "type", "invalid_enum") is not None

    def test_invalid_node_id_is_located(self):
        doc = _doc([{"id": "a b", "type": "output"}])
        assert _find(validate_ir(doc), "a b", "id", "invalid_node_id") is not None

    def test_duplicate_node_id(self):
        doc = _doc([{"id": "x", "type": "output"}, {"id": "x", "type": "output"}])
        assert _find(validate_ir(doc), "x", "id", "duplicate_node_id") is not None

    def test_dangling_edge(self):
        doc = _doc([{"id": "a", "type": "output"}], [{"from": "a", "to": "ghost"}])
        assert _find(validate_ir(doc), "a", "to", "dangling_edge") is not None

    def test_edge_condition_bad_op_is_located(self):
        doc = _doc([{"id": "a", "type": "input"}, {"id": "b", "type": "output"}],
                   [{"from": "a", "to": "b",
                     "condition": {"field": "m", "op": "bad", "value": 1}}])
        assert _find(validate_ir(doc), "a", "condition.op",
                     "invalid_enum") is not None

    def test_input_kind_enum(self):
        doc = _doc([{"id": "src", "type": "input", "params": {"kind": "magic"}}])
        assert _find(validate_ir(doc), "src", "params.kind",
                     "invalid_enum") is not None

    def test_output_format_enum(self):
        doc = _doc([{"id": "out", "type": "output", "params": {"format": "xml"}}])
        assert _find(validate_ir(doc), "out", "params.format",
                     "invalid_enum") is not None


# =========================================================================== #
# 5. IR 直接从注册表派生：不可能漂移
# =========================================================================== #
class TestDerivationHasNoDrift:
    def test_transform_models_cover_the_registry_exactly(self):
        assert set(TRANSFORM_PARAMS_MODELS) == set(TRANSFORM_VERBS)
        assert set(NODE_PARAMS_MODELS) == {"input", "output"}

    @pytest.mark.parametrize("verb", list(TRANSFORM_VERBS))
    def test_derived_fields_match_registry_schema(self, verb: str):
        schema = VERB_REGISTRY[verb].params_schema
        model = TRANSFORM_PARAMS_MODELS[verb]
        assert set(model.model_fields) == set(schema["properties"])
        for key in schema.get("required", []):
            assert model.model_fields[key].is_required(), (verb, key)

    def test_derived_enum_matches_registry_enum(self):
        model = TRANSFORM_PARAMS_MODELS["merge"]
        annotation = model.model_fields["mode"].annotation
        assert set(getattr(annotation, "__args__", ())) >= set(
            VERB_REGISTRY["merge"].params_schema["properties"]["mode"]["enum"])

    def test_params_model_lookup_is_honest_for_unknown(self):
        assert params_model("does_not_exist") is None

    def test_extra_forbidden_is_on_every_model(self):
        for model in (*TRANSFORM_PARAMS_MODELS.values(), *NODE_PARAMS_MODELS.values()):
            assert model.model_config.get("extra") == "forbid"

    def test_cross_field_check_is_reused_from_registry(self):
        """aggregate.op=sum 需要 field：诊断来自注册表 check，而非另写一套。"""
        doc = _doc([_xf("t", "aggregate", op="sum")])
        hit = _find(validate_ir(doc), "t", "params", "cross_field")
        assert hit is not None
        assert VERB_REGISTRY["aggregate"].check is not None


# =========================================================================== #
# 6. 接线进编译路径：类型错误在执行前暴露
# =========================================================================== #
class TestCompilePathIsWired:
    def _doc_with_layout(self) -> dict:
        return _doc([
            {"id": "src", "type": "input",
             "params": {"kind": "literal", "value": "hi"}},
            {"id": "out", "type": "output", "params": {"format": "text"},
             "layout": {"x": 1, "y": 2}},  # 既有 validate_dsl 不拦，IR 拦
        ], [{"from": "src", "to": "out"}])

    def test_legacy_validator_accepts_but_ir_rejects(self):
        doc = self._doc_with_layout()
        validate_dsl(doc)  # 既有行为：不抛
        diags = validate_ir(doc)
        assert _find(diags, "out", "layout", "extra_field") is not None

    def test_compile_dsl_rejects_type_error(self):
        with pytest.raises(DslValidationError, match="DSL 类型校验"):
            compile_dsl(self._doc_with_layout())

    def test_run_dsl_never_executes_a_type_invalid_document(self):
        """IR 闸门在 run_dsl→compile_dsl 处触发，节点**一个都没跑**。"""
        with pytest.raises(DslValidationError):
            run_dsl(self._doc_with_layout())

    def test_assert_ir_valid_passes_for_clean_document(self):
        doc = _doc([{"id": "src", "type": "input"},
                    {"id": "out", "type": "output"}],
                   [{"from": "src", "to": "out"}])
        assert assert_ir_valid(doc) is doc

    def test_assert_ir_valid_message_lists_every_diagnostic(self):
        doc = self._doc_with_layout()
        with pytest.raises(DslValidationError) as exc:
            assert_ir_valid(doc)
        message = str(exc.value)
        assert "out" in message and "layout" in message


# =========================================================================== #
# 7. 既有 DSL 文档回归：0 破坏
# =========================================================================== #
def _kitchen_sink_doc() -> dict:
    """覆盖全部 9 个动词 + 条件边的画布（与 test_verb_set 同构）。"""
    return _doc(
        [
            {"id": "src", "type": "input",
             "params": {"kind": "literal",
                        "value": [{"name": "张三", "score": 90, "ok": True},
                                  {"name": "李四", "score": 40, "ok": False}]}},
            _xf("keep", "filter", field="score", op="gt", value=60),
            _xf("tag", "map", op="set", field="grade", value="优:{name}"),
            _xf("txt", "template", template="{name}={score}"),
            _xf("br", "branch", field="ok", op="eq", value=True,
                then_label="pass", else_label="fail"),
            _xf("agg", "aggregate", op="count", field="name"),
            _xf("agg2", "aggregate", op="join", field="name", sep="/"),
            _xf("mrg", "merge", mode="concat"),
            _xf("art", "artifact", name="报表", kind="table"),
            _xf("ag", "agent", agent="reviewer"),
            _xf("cf", "confirm", prompt="确认发布？", role="owner"),
            {"id": "out", "type": "output", "params": {"format": "json"}},
        ],
        [
            {"from": "src", "to": "keep"}, {"from": "keep", "to": "tag"},
            {"from": "tag", "to": "txt"}, {"from": "txt", "to": "br"},
            {"from": "br", "to": "agg"}, {"from": "br", "to": "agg2"},
            {"from": "agg", "to": "mrg"}, {"from": "agg2", "to": "mrg"},
            {"from": "mrg", "to": "art"}, {"from": "art", "to": "ag"},
            {"from": "ag", "to": "cf"}, {"from": "cf", "to": "out"},
        ],
    )


class TestExistingDocumentsRegress:
    def test_kitchen_sink_doc_produces_no_diagnostic(self):
        assert validate_ir(_kitchen_sink_doc()) == []

    def test_kitchen_sink_doc_still_compiles(self):
        plan = compile_dsl(_kitchen_sink_doc())
        assert plan.order[0] == "src"

    def test_three_node_template_doc_produces_no_diagnostic(self):
        doc = _doc([
            {"id": "src", "type": "input",
             "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]}},
            _xf("tpl", "template", template="你好，{name}！你今年 {age} 岁。"),
            {"id": "out", "type": "output", "params": {"format": "text"}},
        ], [{"from": "src", "to": "tpl"}, {"from": "tpl", "to": "out"}])
        assert validate_ir(doc) == []

    def test_conditional_edge_doc_produces_no_diagnostic(self):
        doc = _doc([
            {"id": "in", "type": "input",
             "params": {"kind": "literal", "value": {"mode": "fast"}}},
            _xf("fast", "template", template="快速路径"),
            _xf("slow", "template", template="慢速路径"),
            {"id": "out", "type": "output", "params": {"format": "text"}},
        ], [
            {"from": "in", "to": "fast",
             "condition": {"field": "mode", "op": "eq", "value": "fast"}},
            {"from": "in", "to": "slow",
             "condition": {"field": "mode", "op": "ne", "value": "fast"}},
            {"from": "fast", "to": "out"}, {"from": "slow", "to": "out"},
        ])
        assert validate_ir(doc) == []


# =========================================================================== #
# 8. Diagnostic 形态
# =========================================================================== #
class TestDiagnosticShape:
    def test_fields_and_to_dict(self):
        d = Diagnostic(node_id="t", field_path="params.op",
                       code="invalid_enum", message="bad")
        assert d.to_dict() == {"node_id": "t", "field_path": "params.op",
                               "code": "invalid_enum", "message": "bad"}

    def test_is_frozen_and_hashable(self):
        d = Diagnostic("t", "params.op", "invalid_enum", "bad")
        with pytest.raises(Exception):
            d.code = "other"  # type: ignore[misc]
        assert isinstance(hash(d), int)

    def test_str_is_locatable(self):
        text = str(Diagnostic("t", "params.op", "invalid_enum", "bad"))
        assert "t" in text and "params.op" in text and "invalid_enum" in text

    def test_format_diagnostics_counts_every_entry(self):
        diags = validate_ir(_doc([_xf("t", "map", op="bogus", evil=1)]))
        assert len(diags) >= 2
        text = format_diagnostics(diags)
        assert str(len(diags)) in text
        assert "t" in text and "params.op" in text
