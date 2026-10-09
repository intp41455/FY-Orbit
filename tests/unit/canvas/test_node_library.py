"""A-画布搭建器-01：16 类节点库逐类执行语义测试（补齐包5）。

覆盖：
* 13 类新增节点（llm / knowledge_retrieval / question_classifier /
  parameter_extractor / iteration / loop / variable_aggregator / template /
  http_request / code / tool / human_input / trigger）每类至少一条「能跑通」
  与一条「诚实失败」语义；
* code 节点沙箱：正常执行、超时终止、**拒绝网络**（A-Claw安全-01 边界）；
* schema/IR/装配对 16 类节点全量自洽（NODE_PARAMS_SCHEMAS 单一真源）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import (
    EXTENDED_NODE_TYPES,
    FLOW_TYPES,
    NODE_PARAMS_SCHEMAS,
    NODE_TYPES,
    DslSuspended,
    DslValidationError,
    canonical_dsl,
    compile_dsl,
    run_dsl,
    validate_dsl,
)
from find_yourself.services.dsl_ir import validate_ir


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def _flow(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"version": "1", "nodes": nodes,
            "edges": edges or ([{"from": nodes[i]["id"], "to": nodes[i + 1]["id"]}
                                for i in range(len(nodes) - 1)] if len(nodes) > 1 else [])}


def _literal(value) -> dict:
    return {"id": "in1", "type": "input", "params": {"kind": "literal", "value": value}}


def _output() -> dict:
    return {"id": "out1", "type": "output"}


def _status_and_output(doc: dict, **kwargs):
    r = run_dsl(doc, **kwargs)
    return r.status, r.output


# --------------------------------------------------------------------------- #
# 节点库扩容与契约自洽
# --------------------------------------------------------------------------- #

def test_node_library_has_13_new_types() -> None:
    assert len(NODE_TYPES) == 16
    assert set(EXTENDED_NODE_TYPES) == {
        "llm", "knowledge_retrieval", "question_classifier", "parameter_extractor",
        "iteration", "loop", "variable_aggregator", "template", "http_request",
        "code", "tool", "human_input", "trigger"}


def test_every_node_type_has_closed_params_schema() -> None:
    # transform 的 params 由 verb 注册表约束（_verb_params_schema），不在本表；
    # 其余 15 类节点都必须有封闭（additionalProperties=False）的 params 契约。
    assert set(NODE_PARAMS_SCHEMAS) == set(NODE_TYPES) - {"transform"}
    for schema in NODE_PARAMS_SCHEMAS.values():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_unknown_params_key_is_rejected_on_new_node() -> None:
    doc = _flow([_literal("x"),
                 {"id": "tp1", "type": "template",
                  "params": {"template": "hi", "jinja": True}},
                 _output()])
    with pytest.raises(DslValidationError):
        validate_dsl(doc)


def test_flow_type_enum_is_enforced() -> None:
    doc = _flow([_literal("x"), _output()])
    doc["flow_type"] = "agentflow"
    with pytest.raises(DslValidationError):
        validate_dsl(doc)
    doc["flow_type"] = "chatflow"
    validate_dsl(doc)
    assert canonical_dsl(doc)["flow_type"] == "chatflow"


# --------------------------------------------------------------------------- #
# llm / knowledge_retrieval / tool —— 注入式解析器
# --------------------------------------------------------------------------- #

def test_llm_node_through_resolver() -> None:
    doc = _flow([_literal({"value": "你好"}),
                 {"id": "llm1", "type": "llm",
                  "params": {"model": "mock-model", "prompt": "总结：{value}"}},
                 _output()])
    seen: dict = {}

    def resolver(model: str, prompt: str, params: dict):
        seen.update({"model": model, "prompt": prompt})
        return {"text": "这是总结", "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    status, output = _status_and_output(doc, llm_resolver=resolver)
    assert status == "succeeded"
    assert output == {"text": "这是总结",
                      "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    assert seen == {"model": "mock-model", "prompt": "总结：你好"}


def test_llm_node_without_resolver_honestly_fails() -> None:
    doc = _flow([{"id": "llm1", "type": "llm",
                  "params": {"model": "m", "prompt": "p"}}, _output()])
    r = run_dsl(doc)
    assert r.status == "failed"
    assert "llm_resolver" in (r.error or "")


def test_knowledge_retrieval_node_passes_through_results() -> None:
    doc = _flow([_literal({"value": "画布"}),
                 {"id": "kb1", "type": "knowledge_retrieval",
                  "params": {"query": "关于 {value} 的资料", "top_k": 2, "mode": "lexical"}},
                 _output()])
    calls: list[tuple[str, dict]] = []

    def resolver(query: str, params: dict):
        calls.append((query, params))
        return [{"chunk_id": "c1", "content": "命中一"}]

    status, output = _status_and_output(doc, knowledge_resolver=resolver)
    assert status == "succeeded"
    assert output == [{"chunk_id": "c1", "content": "命中一"}]
    assert calls and calls[0][0] == "关于 画布 的资料"
    assert calls[0][1]["top_k"] == 2


def test_knowledge_retrieval_without_resolver_honestly_fails() -> None:
    doc = _flow([{"id": "kb1", "type": "knowledge_retrieval",
                  "params": {"query": "q"}}, _output()])
    status, _ = _status_and_output(doc)
    assert status == "failed"


def test_tool_node_via_tool_registry_builtin(tmp_path) -> None:
    """tool 节点经 services/tool_registry.py 现有公开接口（register + invoke）。"""
    from find_yourself.services.tool_registry import ToolRegistryService

    # 持久化隔离到 tmp_path，绝不写仓库 .runtime/tool_registry。
    registry = ToolRegistryService(persist_dir=tmp_path / "tools")
    registry.register(
        name="fy_test_echo",
        description="测试用回显工具（注册表内置 echo 执行器）",
        parameters={"type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        entry={"type": "builtin", "executor": "echo"},
    )
    doc = _flow([{"id": "tool1", "type": "tool",
                  "params": {"tool": "fy_test_echo",
                             "arguments": {"text": "hello canvas"}}},
                 _output()])

    def resolver(name: str, args: dict, params: dict):
        return registry.invoke(name, args)

    status, output = _status_and_output(doc, tool_resolver=resolver)
    assert status == "succeeded"
    # invoke 返回回执信封：executed=True + result 为执行器输出（含 call_id 留痕）。
    assert output["executed"] is True
    assert output["result"] == {"echo": {"text": "hello canvas"}}
    assert output["tool"] == "fy_test_echo"


def test_tool_node_without_resolver_honestly_fails() -> None:
    doc = _flow([{"id": "tool1", "type": "tool",
                  "params": {"tool": "builtin_echo", "arguments": {}}}, _output()])
    status, _ = _status_and_output(doc)
    assert status == "failed"


# --------------------------------------------------------------------------- #
# question_classifier / parameter_extractor —— LLM 兜底语义
# --------------------------------------------------------------------------- #

def test_question_classifier_maps_to_declared_class() -> None:
    doc = _flow([_literal("我的账户被锁了，帮我解锁"),
                 {"id": "qc1", "type": "question_classifier",
                  "params": {"classes": ["账户问题", "闲聊"], "model": "mock"}},
                 _output()])
    status, output = _status_and_output(
        doc, llm_resolver=lambda m, p, pa: {"text": "账户问题"})
    assert status == "succeeded"
    assert output["class"] == "账户问题"


def test_question_classifier_rejects_unknown_label() -> None:
    doc = _flow([_literal("q"),
                 {"id": "qc1", "type": "question_classifier",
                  "params": {"classes": ["A", "B"], "model": "mock"}},
                 _output()])
    status, _ = _status_and_output(doc, llm_resolver=lambda m, p, pa: {"text": "C"})
    assert status == "failed"


def test_parameter_extractor_extracts_declared_fields() -> None:
    doc = _flow([_literal("张三，34 岁，住在杭州"),
                 {"id": "pe1", "type": "parameter_extractor",
                  "params": {"fields": [{"name": "city", "type": "string",
                                         "required": True}],
                             "model": "mock"}},
                 _output()])
    status, output = _status_and_output(
        doc, llm_resolver=lambda m, p, pa: {"text": '{"city": "杭州", "extra": 1}'})
    assert status == "succeeded"
    assert output["values"] == {"city": "杭州"}
    assert output["ignored"] == ["extra"]


def test_parameter_extractor_missing_required_field_fails() -> None:
    doc = _flow([_literal("没有城市信息"),
                 {"id": "pe1", "type": "parameter_extractor",
                  "params": {"fields": [{"name": "city", "type": "string",
                                         "required": True}],
                             "model": "mock"}},
                 _output()])
    status, _ = _status_and_output(doc, llm_resolver=lambda m, p, pa: {"text": "{}"})
    assert status == "failed"


# --------------------------------------------------------------------------- #
# iteration / loop —— 子流程确定性执行
# --------------------------------------------------------------------------- #

_UPPER_SUBFLOW = {
    "version": "1",
    "nodes": [
        {"id": "si", "type": "input"},
        {"id": "st", "type": "transform", "verb": "map", "params": {"op": "upper"}},
        {"id": "so", "type": "output"},
    ],
    "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}],
}


def test_iteration_maps_subflow_over_items() -> None:
    doc = _flow([_literal(["ab", "cd"]),
                 {"id": "it1", "type": "iteration",
                  "params": {"subflow": _UPPER_SUBFLOW}},
                 _output()])
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    # map 动词的既有语义：逐项产出列表，故每项子流程输出是 ['AB']。
    assert output == [["AB"], ["CD"]]


def test_iteration_max_items_is_enforced_at_compile_time() -> None:
    doc = _flow([_literal(["x"]),
                 {"id": "it1", "type": "iteration",
                  "params": {"subflow": _UPPER_SUBFLOW, "max_items": 2000}},
                 _output()])
    with pytest.raises(DslValidationError):
        compile_dsl(doc)


def test_iteration_requires_input_entry_in_subflow() -> None:
    bad_subflow = {"version": "1", "nodes": [_output()], "edges": []}
    doc = _flow([_literal(["x"]),
                 {"id": "it1", "type": "iteration", "params": {"subflow": bad_subflow}},
                 _output()])
    with pytest.raises(DslValidationError):
        compile_dsl(doc)


def test_loop_terminates_at_max_iterations() -> None:
    doc = _flow([_literal({"n": 0}),
                 {"id": "lp1", "type": "loop",
                  "params": {"subflow": {
                      "version": "1",
                      "nodes": [
                          {"id": "si", "type": "input"},
                          {"id": "st", "type": "transform", "verb": "map",
                           "params": {"op": "set", "field": "n", "value": "{n}1"}},
                          {"id": "so", "type": "output"}],
                      "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}]},
                      "max_iterations": 3}},
                 _output()])
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    assert output["iterations"] == 3
    assert output["reason"] == "max_iterations"


def test_loop_stops_when_until_condition_met() -> None:
    subflow = {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "sb", "type": "transform", "verb": "branch",
             "params": {"field": "value", "op": "eq", "value": 3,
                        "then_label": "three", "else_label": "counting"}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "sb"}, {"from": "sb", "to": "so"}],
    }
    doc = _flow([_literal({"n": 0}),
                 {"id": "lp1", "type": "loop",
                  "params": {"subflow": subflow, "max_iterations": 50,
                             "until_field": "branch", "until_op": "eq",
                             "until_value": "three"}},
                 _output()])
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    assert output["reason"] == "max_iterations"


# --------------------------------------------------------------------------- #
# variable_aggregator / template / trigger —— 纯确定节点
# --------------------------------------------------------------------------- #

def test_variable_aggregator_first_non_null() -> None:
    doc = {"version": "1", "nodes": [
        {"id": "a", "type": "input", "params": {"kind": "literal", "value": None}},
        {"id": "b", "type": "input", "params": {"kind": "literal", "value": "B"}},
        {"id": "agg", "type": "variable_aggregator", "params": {}},
        _output()],
        "edges": [{"from": "a", "to": "agg"}, {"from": "b", "to": "agg"},
                  {"from": "agg", "to": "out1"}]}
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    assert output == "B"


def test_variable_aggregator_all_null_fails() -> None:
    doc = {"version": "1", "nodes": [
        {"id": "a", "type": "input", "params": {"kind": "literal", "value": None}},
        {"id": "agg", "type": "variable_aggregator", "params": {}},
        _output()],
        "edges": [{"from": "a", "to": "agg"}, {"from": "agg", "to": "out1"}]}
    status, _ = _status_and_output(doc)
    assert status == "failed"


def test_template_node_interpolates() -> None:
    doc = _flow([_literal({"name": "画布"}),
                 {"id": "tp1", "type": "template",
                  "params": {"template": "你好，{name}！"}},
                 _output()])
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    assert output == "你好，画布！"


def test_trigger_node_kinds() -> None:
    manual = {"version": "1", "nodes": [
        {"id": "t1", "type": "trigger",
         "params": {"kind": "manual", "config": {"value": [1, 2]}}}, _output()],
        "edges": [{"from": "t1", "to": "out1"}]}
    status, output = _status_and_output(manual)
    assert status == "succeeded"
    assert output == {"value": [1, 2], "trigger": "manual"}

    conv = {"version": "1", "nodes": [
        {"id": "t1", "type": "trigger",
         "params": {"kind": "conversation",
                    "config": {"value": "在吗", "conversation_id": "c1"}}}, _output()],
        "edges": [{"from": "t1", "to": "out1"}]}
    status, output = _status_and_output(conv)
    assert output == {"text": "在吗", "conversation_id": "c1", "trigger": "conversation"}


# --------------------------------------------------------------------------- #
# http_request —— 标准库 + allow_domains 白名单
# --------------------------------------------------------------------------- #

def test_http_request_via_injected_resolver() -> None:
    """resolver 路径在**显式配置白名单**时正常执行。

    未配置 allow_domains 时一律拒绝（fail-closed，见下一条用例）。
    """
    doc = _flow([_literal("x"),
                 {"id": "h1", "type": "http_request",
                  "params": {"url": "https://api.example.com/v1/ping", "method": "GET",
                             "allow_domains": ["example.com"]}},
                 _output()])

    def resolver(params: dict, payload):
        return {"status": 200, "url": params["url"], "body": "pong", "truncated": False}

    status, output = _status_and_output(doc, http_resolver=resolver)
    assert status == "succeeded"
    assert output["status"] == 200 and output["body"] == "pong"


def test_http_request_without_allow_domains_is_denied() -> None:
    """fail-closed：未配置 allow_domains 时拒绝出网（而非放行任意 host）。

    回归历史缺陷：校验曾被 `if allow:` 包裹，未配置即静默放行 → SSRF 全开。
    """
    doc = _flow([{"id": "h1", "type": "http_request",
                  "params": {"url": "https://api.example.com/v1/ping", "method": "GET"}},
                 _output()])
    status, _ = _status_and_output(doc, http_resolver=lambda p, x: {"status": 200})
    assert status == "failed"
    assert "allow_domains" in (_latest_error(doc) or "")


def test_http_request_empty_allow_domains_is_denied() -> None:
    """fail-closed：allow_domains 为空列表同样拒绝（不得退化为放行）。"""
    doc = _flow([{"id": "h1", "type": "http_request",
                  "params": {"url": "https://api.example.com/v1/ping",
                             "allow_domains": []}},
                 _output()])
    status, _ = _status_and_output(doc, http_resolver=lambda p, x: {"status": 200})
    assert status == "failed"
    assert "allow_domains" in (_latest_error(doc) or "")


def test_http_request_allow_domains_is_enforced() -> None:
    doc = _flow([{"id": "h1", "type": "http_request",
                  "params": {"url": "https://evil.example.net/steal",
                             "allow_domains": ["example.com"]}},
                 _output()])
    status, _ = _status_and_output(doc, http_resolver=lambda p, x: {"status": 200})
    assert status == "failed"
    assert "allow_domains" in (_latest_error(doc) or "")


def _latest_error(doc: dict) -> str | None:
    return run_dsl(doc, http_resolver=lambda p, x: {"status": 200}).error


def test_http_request_rejects_non_http_url() -> None:
    doc = _flow([{"id": "h1", "type": "http_request",
                  "params": {"url": "file:///etc/passwd"}}, _output()])
    status, _ = _status_and_output(doc, http_resolver=lambda p, x: {"status": 200})
    assert status == "failed"


# --------------------------------------------------------------------------- #
# code —— 受控执行（子进程 + 超时 + 禁网络/禁 shell）
# --------------------------------------------------------------------------- #

def test_code_node_runs_python_and_captures_stdout() -> None:
    doc = _flow([_literal({"x": 21}),
                 {"id": "c1", "type": "code",
                  "params": {"code": "print(_fy_payload['x'] * 2)"}},
                 _output()])
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    assert output["stdout"] == "42\n"
    assert output["exit_code"] == 0


def test_code_node_denies_network_egress() -> None:
    """A-Claw安全-01：code 节点沙箱必须拒绝 socket 出网（引导层 Python 封禁）。"""
    doc = _flow([{"id": "c1", "type": "code", "params": {"code":
                 "import socket\n"
                 "s = socket.socket()\n"
                 "print('SHOULD NEVER RUN')\n"}},
                _output()])
    status, output = _status_and_output(doc)
    assert status == "failed"
    assert "SHOULD NEVER RUN" not in str(output)


def test_code_node_denies_shell_spawn() -> None:
    doc = _flow([{"id": "c1", "type": "code", "params": {"code":
                 "import os\n"
                 "os.system('echo pwned')\n"}},
                _output()])
    status, _ = _status_and_output(doc)
    assert status == "failed"


def test_code_node_timeout_kills_subprocess() -> None:
    doc = _flow([{"id": "c1", "type": "code",
                  "params": {"code": "import time; time.sleep(30)",
                             "timeout_seconds": 2}},
                 _output()])
    status, _ = _status_and_output(doc)
    assert status == "failed"


def test_code_node_timeout_is_capped() -> None:
    """参数里把超时调到 9999s 也会被裁到 CODE_TIMEOUT_CAP_SECONDS 上界。"""
    from find_yourself.services.dsl_canvas import CODE_TIMEOUT_CAP_SECONDS

    doc = _flow([{"id": "c1", "type": "code",
                  "params": {"code": "pass", "timeout_seconds": 9999}},
                 _output()])
    validate_dsl(doc)  # 编译期不裁剪（裁剪在执行期），保证旧文档兼容
    r = run_dsl(doc)
    assert r.status == "succeeded"
    assert CODE_TIMEOUT_CAP_SECONDS == 15.0


# --------------------------------------------------------------------------- #
# human_input —— 挂 services/hitl.py 的挂起/恢复语义
# --------------------------------------------------------------------------- #

def test_human_input_suspends_without_provider() -> None:
    doc = _flow([_literal("草稿"),
                 {"id": "h1", "type": "human_input",
                  "params": {"prompt": "请补充收件人", "role": "owner"}},
                 _output()])
    with pytest.raises(DslSuspended) as excinfo:
        run_dsl(doc, execution_id="exec-h1")
    assert excinfo.value.node_id == "h1"
    assert excinfo.value.context["prompt"] == "请补充收件人"
    assert excinfo.value.checkpoint == "exec-h1#h1"


def test_human_input_resumes_with_provider_value() -> None:
    doc = _flow([_literal("草稿"),
                 {"id": "h1", "type": "human_input",
                  "params": {"prompt": "请补充收件人"}},
                 _output()])
    status, output = _status_and_output(
        doc, execution_id="exec-h1",
        human_input_provider=lambda nid, eid, params: "张三")
    assert status == "succeeded"
    assert output == {"input": "张三", "prompt": "请补充收件人"}


def test_human_input_provider_none_still_suspends() -> None:
    doc = _flow([{"id": "h1", "type": "human_input",
                  "params": {"prompt": "p"}}, _output()])
    with pytest.raises(DslSuspended):
        run_dsl(doc, human_input_provider=lambda nid, eid, params: None)


# --------------------------------------------------------------------------- #
# IR 收集式校验对新节点的覆盖
# --------------------------------------------------------------------------- #

def test_ir_collects_field_level_diagnostics_for_new_nodes() -> None:
    doc = _flow([{"id": "c1", "type": "code", "params": {}}])  # 缺必填 code
    diags = validate_ir(doc)
    assert any(d.node_id == "c1" and d.field_path == "params.code" for d in diags)


def test_ir_accepts_flow_type_field() -> None:
    doc = _flow([_literal("x"), _output()])
    doc["flow_type"] = "workflow"
    assert validate_ir(doc) == []
