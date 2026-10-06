"""A-代码SDK-02 · 第三方自定义节点开发契约单测。

核心边界（契约文档 ``docs/dsl-自定义节点契约-2026-10-06.md``）：
* ``VERB_REGISTRY`` 是封闭白名单（ADR-003）——自定义节点**绝不注入**，
  平台动词名一律保留（注册即拒），注册前后注册表逐字节不变；
* 执行载体是 ``agent`` 动词节点（平台唯一注入点），agent 名为
  ``custom:<node_id>``，实例参数由 SDK 侧表携带；
* params 契约用与 ``VERB_REGISTRY`` 同一套 JSON Schema 片段约定，
  装配期即校验（不留到执行期）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import DslValidationError, VERB_REGISTRY
from find_yourself.services.dsl_sdk import (
    CUSTOM_NODE_REGISTRY,
    CUSTOM_AGENT_PREFIX,
    CodeWorkflow,
    DslSdkError,
    GraphBuilder,
    StateField,
    StateSchema,
    custom_node_catalog,
    register_custom_node,
)

from _dsl_docs import lit, out


@pytest.fixture()
def enrich_node():
    """一个示例第三方节点：把载荷里的名字补上问候语。"""
    return register_custom_node(
        "test_enrich_names",
        category="示例",
        summary="为每个名字补问候语",
        params_schema={
            "type": "object", "additionalProperties": False,
            "required": ["greeting"],
            "properties": {
                "greeting": {"type": "string", "minLength": 1},
                "shout": {"type": "boolean"},
            },
        },
        defaults={"shout": False},
        execute=lambda payload, params: [
            {**item, "hello": params["greeting"] + item["name"]}
            for item in payload
        ],
    )


def _registry_snapshot() -> dict:
    return dict(VERB_REGISTRY)


# =========================================================================== #
# 注册契约
# =========================================================================== #
class TestRegistrationContract:
    def test_register_and_catalog_merges_readonly(self, enrich_node):
        catalog = custom_node_catalog()
        platform = [e for e in catalog if e["origin"] == "platform"]
        custom = [e for e in catalog if e["origin"] == "custom"]
        assert [e["name"] for e in platform] == list(VERB_REGISTRY)  # 只读原样
        assert [e["name"] for e in custom] == [enrich_node.name]

    def test_platform_verb_name_reserved(self, enrich_node):
        """封闭性（ADR-003）：平台动词名一律保留，注册即拒，注册表不变。"""
        before = _registry_snapshot()
        for name in ("map", "filter", "approval", "agent"):
            with pytest.raises(DslSdkError, match="冲突"):
                register_custom_node(
                    name, category="x", summary="x",
                    params_schema={"type": "object",
                                   "additionalProperties": False,
                                   "properties": {}},
                    execute=lambda payload, params: payload)
        assert VERB_REGISTRY == before  # 只读消费：注册表逐项不变

    def test_duplicate_custom_node_rejected(self, enrich_node):
        with pytest.raises(DslSdkError, match="已注册"):
            register_custom_node(
                enrich_node.name, category="x", summary="x",
                params_schema={"type": "object", "additionalProperties": False,
                               "properties": {}},
                execute=lambda payload, params: payload)

    def test_params_schema_conventions_enforced(self):
        for bad in ({}, {"type": "object"},
                    {"type": "object", "additionalProperties": True,
                     "properties": {}}):
            with pytest.raises(DslSdkError, match="params_schema"):
                register_custom_node(
                    "test_bad_schema", category="x", summary="x",
                    params_schema=bad,
                    execute=lambda payload, params: payload)
        assert "test_bad_schema" not in CUSTOM_NODE_REGISTRY

    def test_execute_signature_enforced(self):
        with pytest.raises(DslSdkError, match="两个位置参数"):
            register_custom_node(
                "test_bad_exec", category="x", summary="x",
                params_schema={"type": "object", "additionalProperties": False,
                               "properties": {}},
                execute=lambda payload: payload)

    def test_defaults_are_partial_fragments(self):
        """defaults 是**片段**（实例再补齐），缺 required 不在注册期报错；
        但封闭键与类型约束照常生效。"""
        register_custom_node(
            "test_partial_defaults", category="x", summary="x",
            params_schema={"type": "object", "additionalProperties": False,
                           "required": ["a"], "properties": {
                               "a": {"type": "string"},
                               "b": {"type": "integer"}}},
            defaults={"b": 3},  # a 由实例传；b 类型正确
            execute=lambda payload, params: payload)
        with pytest.raises(DslValidationError, match="类型"):
            register_custom_node(
                "test_bad_default_type", category="x", summary="x",
                params_schema={"type": "object", "additionalProperties": False,
                               "properties": {"b": {"type": "integer"}}},
                defaults={"b": "不是整数"},
                execute=lambda payload, params: payload)

    def test_bad_name_rejected(self):
        with pytest.raises(DslSdkError, match="不合法"):
            register_custom_node(
                "1 Bad Name", category="x", summary="x",
                params_schema={"type": "object", "additionalProperties": False,
                               "properties": {}},
                execute=lambda payload, params: payload)


# =========================================================================== #
# 装配期校验（GraphBuilder.custom_node）
# =========================================================================== #
class TestBuilderAssembly:
    def test_unregistered_custom_node_rejected(self):
        g = GraphBuilder()
        with pytest.raises(DslSdkError, match="未注册"):
            g.custom_node("n1", "no_such_node_xyz")

    def test_instance_params_violating_contract_rejected_at_build(self,
                                                                  enrich_node):
        g = GraphBuilder()
        with pytest.raises(DslValidationError, match="缺少参数"):
            g.custom_node("n1", enrich_node.name)  # greeting 必填

    def test_extra_param_rejected_at_build(self, enrich_node):
        g = GraphBuilder()
        with pytest.raises(DslValidationError, match="不被支持"):
            g.custom_node("n1", enrich_node.name, greeting="你好",
                          evil="越界")

    def test_defaults_merged_with_instance_params(self, enrich_node):
        g = GraphBuilder()
        node = g.custom_node("n1", enrich_node.name, greeting="你好：")
        # 载荷是平台合法的 agent 动词节点（封闭 params：只有 agent 键）
        assert node["verb"] == "agent"
        assert node["params"] == {"agent": f"{CUSTOM_AGENT_PREFIX}n1"}
        spec, merged = g._custom[f"{CUSTOM_AGENT_PREFIX}n1"]
        assert merged == {"greeting": "你好：", "shout": False}  # defaults 合并


# =========================================================================== #
# 端到端执行
# =========================================================================== #
class TestCustomNodeExecution:
    def test_custom_node_runs_through_agent_injection_point(self, enrich_node):
        g = GraphBuilder()
        g.input_node("src", kind="literal",
                     value=[{"name": "张三"}, {"name": "李四"}])
        g.custom_node("enrich", enrich_node.name, greeting="你好：")
        g.output_node("out")
        g.edge("src", "enrich")
        g.edge("enrich", "out")
        result = g.build().run()
        assert result.status == "succeeded", result.result.error
        assert result.output == [
            {"name": "张三", "hello": "你好：张三"},
            {"name": "李四", "hello": "你好：李四"},
        ]

    def test_custom_node_coexists_with_plain_agent(self, enrich_node):
        """普通 agent 节点仍可经 fallback 解析器注入（同一注入点两用）。"""
        def fallback(name, payload, params):
            return {"agent": name, "echo": payload}
        g = GraphBuilder()
        g.input_node("src", kind="literal", value={"q": 1})
        g.transform_node("ag", "agent", agent="summarizer")
        g.output_node("out")
        g.edge("src", "ag")
        g.edge("ag", "out")
        result = g.build(agent_resolver=fallback).run()
        assert result.status == "succeeded"
        assert result.output == {"agent": "summarizer", "echo": {"q": 1}}

    def test_custom_node_without_state_schema_runs(self, enrich_node):
        g = GraphBuilder()
        g.input_node("src", kind="literal", value=[{"name": "张三"}])
        g.custom_node("e", enrich_node.name, greeting="嗨")
        g.output_node("out")
        g.edge("src", "e")
        g.edge("e", "out")
        result = g.build(state_schema=StateSchema([
            StateField("done", "boolean", default=False)])).run()
        assert result.status == "succeeded"
        assert result.state == {"done": False}

    def test_forged_agent_name_fails_honestly(self, enrich_node):
        """绕过 GraphBuilder 直塞未知 agent 名 → 明确失败，绝不静默。"""
        doc = {
            "version": "1",
            "nodes": [lit([{"name": "x"}]),
                      {"id": "e", "type": "transform", "verb": "agent",
                       "params": {"agent": f"{CUSTOM_AGENT_PREFIX}ghost"}},
                      out()],
            "edges": [{"from": "src", "to": "e"}, {"from": "e", "to": "out"}],
        }
        result = CodeWorkflow(doc).run()
        assert result.status == "failed"
        assert "不是本工作流声明的自定义节点" in (result.result.error or "")
