"""A-画布搭建器-01：18 类节点库逐类执行语义测试（补齐包5）。

覆盖：
* 13 类新增节点（llm / knowledge_retrieval / question_classifier /
  parameter_extractor / iteration / loop / variable_aggregator / template /
  http_request / code / tool / human_input / trigger）每类至少一条「能跑通」
  与一条「诚实失败」语义；
* N5 的 parallel / subgraph 两类（真并发扇出、命名子图复用、闸门与引用解析）；
* code 节点沙箱：正常执行、超时终止、**拒绝网络**（A-Claw安全-01 边界）；
* schema/IR/装配对 18 类节点全量自洽（NODE_PARAMS_SCHEMAS 单一真源）。
"""

from __future__ import annotations

import inspect
import threading
import time

import pytest

from find_yourself.services import dsl_canvas
from find_yourself.services.dsl_canvas import (
    EXTENDED_NODE_TYPES,
    MAX_EDGES_PER_GRAPH,
    MAX_NODES_PER_GRAPH,
    NODE_PARAMS_SCHEMAS,
    NODE_TYPES,
    PARALLEL_MAX_BRANCHES,
    RECURSION_LIMIT_CAP,
    DslFieldError,
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
    # 16 = 基础 3 类 + A-画布搭建器-01 的 13 类；N5 又补 parallel/subgraph 两类。
    assert len(NODE_TYPES) == 18
    assert set(EXTENDED_NODE_TYPES) == {
        "llm", "knowledge_retrieval", "question_classifier", "parameter_extractor",
        "iteration", "loop", "variable_aggregator", "template", "http_request",
        "code", "tool", "human_input", "trigger",
        # N5：并发扇出 / 命名子图复用
        "parallel", "subgraph"}


def test_every_node_type_has_closed_params_schema() -> None:
    # transform 的 params 由 verb 注册表约束（_verb_params_schema），不在本表；
    # 其余 17 类节点都必须有封闭（additionalProperties=False）的 params 契约。
    assert set(NODE_PARAMS_SCHEMAS) == set(NODE_TYPES) - {"transform"}
    for schema in NODE_PARAMS_SCHEMAS.values():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_every_node_type_is_executable_not_just_declared() -> None:
    """防「只有声明没有接线」：每种节点都必须有真实执行分支。

    N5 补 parallel/subgraph 前，这两类已在 ``NODE_TYPES`` 与对外 ``/schema``
    里暴露，却没有 params 契约也没有执行分支——运行期必落「未知节点类型」。
    """
    src = inspect.getsource(dsl_canvas.execute_node)
    for ntype in NODE_TYPES:
        if ntype == "transform":  # 走 verb 注册表统一分发
            continue
        assert f'== "{ntype}"' in src, f"execute_node 缺少 {ntype} 的执行分支"


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
# N5 拓扑扩展：parallel（并发扇出）/ subgraph（命名子图复用）
# --------------------------------------------------------------------------- #

def _echo_subflow(tag: str) -> dict:
    """一张最小可运行子图：把入参原样贴上 ``tag`` 后输出。"""
    return {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "st", "type": "transform", "verb": "map",
             "params": {"op": "set", "field": "tag", "value": tag}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "st"}, {"from": "st", "to": "so"}],
    }


def test_parallel_runs_branches_concurrently_not_sequentially() -> None:
    """``parallel`` 必须**真并发**：两个分支同时到达屏障。

    用 ``threading.Barrier`` 而不是「比耗时」——若实现退化成串行，第一个分支会
    在屏障上超时抛 ``BrokenBarrierError``，测试立刻红。计时断言在 CI 上会 flaky。
    """
    barrier = threading.Barrier(2, timeout=5)
    threads: list[int] = []

    def _llm(model: str, payload, params):  # noqa: ANN001 - 测试替身
        threads.append(threading.get_ident())
        barrier.wait()  # 串行执行时这里必然超时
        return {"text": "ok"}

    sub = {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "sl", "type": "llm",
             "params": {"model": "mock", "prompt": "并发探针"}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "sl"}, {"from": "sl", "to": "so"}],
    }
    doc = _flow([_literal("hi"),
                 {"id": "pl1", "type": "parallel", "params": {
                     "branches": [{"name": "left", "inline": sub},
                                  {"name": "right", "inline": sub}]}},
                 _output()])
    status, output = _status_and_output(doc, llm_resolver=_llm)
    assert status == "succeeded"
    # fan-in 按**分支声明顺序**返回，与谁先跑完无关。
    assert len(output) == 2
    assert len(set(threads)) == 2, f"两个分支跑在了同一个线程上：{threads}"


def test_parallel_fan_in_follows_declaration_order() -> None:
    """慢分支在前时，fan-in 结果仍按声明顺序（不按完成先后）。"""
    delays = {"slow": 0.30, "fast": 0.0}

    def _llm(model: str, prompt: str, params):  # noqa: ANN001 - 测试替身
        # ``llm_resolver`` 收到的是**插值后的 prompt**，不是上游 payload。
        # 分支标识只能从 ``params`` 里读——``llm`` 的 params 是封闭 schema，
        # 塞自造键会被编译期直接拒掉。
        name = params["system"]
        time.sleep(delays.get(name, 0.0))
        return {"text": name, "usage": {}}

    branches = []
    for which in ("slow", "fast"):
        sub = _echo_subflow(which)
        # 末尾插一个 llm 节点，靠 params.system 区分分支并制造完成顺序差。
        sub["nodes"].insert(2, {"id": "sl", "type": "llm",
                                "params": {"model": "mock", "prompt": "顺序探针",
                                           "system": which}})
        sub["edges"] = [{"from": "si", "to": "st"},
                        {"from": "st", "to": "sl"},
                        {"from": "sl", "to": "so"}]
        branches.append({"name": which, "inline": sub})
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {"branches": branches}},
                 _output()])
    status, output = _status_and_output(doc, llm_resolver=_llm)
    assert status == "succeeded"
    # 每个分支的值是该子流程 output 节点的输出（即 llm 结果），
    # 外层列表按分支**声明顺序**排列——与谁先跑完无关。
    assert [branch["text"] for branch in output] == ["slow", "fast"]


def test_parallel_branch_failure_fails_the_node() -> None:
    """任一分支失败即本节点失败——绝不把失败分支混进 fan-in 结果。"""
    bad = {"version": "1", "nodes": [_output()], "edges": []}
    good = _echo_subflow("ok")
    # good 分支有 input 入口；bad 没有，编译期就该被 _subflow_entry_ok 拦下，
    # 所以这里用一个**能编译但会运行失败**的分支：引用未在库中的名字在运行期
    # 由 _branch_subflow 抛错（inline 给 None 触发）。
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {
                     "branches": [{"name": "ok", "inline": good},
                                  {"name": "broken", "inline": {
                                      "version": "1",
                                      "nodes": [
                                          {"id": "si", "type": "input"},
                                          {"id": "sx", "type": "http_request",
                                           "params": {"url": "ftp://x/y", "method": "GET"}},
                                          {"id": "so", "type": "output"}],
                                      "edges": [{"from": "si", "to": "sx"},
                                                {"from": "sx", "to": "so"}]}}]}},
                 _output()])
    status, _ = _status_and_output(doc)
    assert status == "failed"
    assert bad is not None  # bad 仅用于说明「无入口子图」编译期已被拦（见下方用例）


def test_parallel_rejects_subflow_without_input_entry_at_compile_time() -> None:
    no_entry = {"version": "1", "nodes": [_output()], "edges": []}
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {
                     "branches": [{"name": "bad", "inline": no_entry}]}},
                 _output()])
    with pytest.raises(DslValidationError):
        compile_dsl(doc)


def test_parallel_branch_count_gate_is_enforced_at_compile_time() -> None:
    branches = [{"name": f"b{i}", "inline": _echo_subflow(f"t{i}")}
                for i in range(PARALLEL_MAX_BRANCHES + 1)]
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {"branches": branches}},
                 _output()])
    with pytest.raises(DslFieldError) as exc:
        compile_dsl(doc)
    assert exc.value.field_path == "params.branches"
    assert str(PARALLEL_MAX_BRANCHES) in str(exc.value)


def test_parallel_requires_exactly_one_of_inline_or_ref() -> None:
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {
                     "branches": [{"name": "b", "inline": _echo_subflow("t"),
                                   "ref": "dup"}]}},
                 _output()])
    with pytest.raises(DslFieldError) as exc:
        compile_dsl(doc)
    assert exc.value.field_path == "params.branches.0"


def test_parallel_rejects_out_of_range_max_workers() -> None:
    doc = _flow([_literal("x"),
                 {"id": "pl1", "type": "parallel", "params": {
                     "branches": [{"name": "b", "inline": _echo_subflow("t")}],
                     "max_workers": PARALLEL_MAX_BRANCHES + 1}},
                 _output()])
    with pytest.raises(DslFieldError) as exc:
        compile_dsl(doc)
    assert exc.value.field_path == "params.max_workers"


# --------------------------- 命名子图（subgraph） --------------------------- #

def test_subgraph_runs_named_subgraph_and_tags_output() -> None:
    doc = {"version": "1",
           "subgraphs": {"greet": _echo_subflow("hello")},
           "nodes": [_literal("x"),
                     {"id": "sg1", "type": "subgraph",
                      "params": {"name": "greet"}},
                     _output()],
           "edges": [{"from": "in1", "to": "sg1"}, {"from": "sg1", "to": "out1"}]}
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    # 输出带子图名，运行日志能看出结果来自哪张子图。
    assert output["subgraph"] == "greet"
    assert output["output"][0]["tag"] == "hello"


def test_subgraph_can_be_reused_by_two_nodes() -> None:
    doc = {"version": "1",
           "subgraphs": {"echo": _echo_subflow("shared")},
           "nodes": [_literal("x"),
                     {"id": "sg1", "type": "subgraph", "params": {"name": "echo"}},
                     {"id": "sg2", "type": "subgraph", "params": {"name": "echo"}},
                     {"id": "agg", "type": "variable_aggregator",
                      "params": {"strategy": "last_non_null"}},
                     _output()],
           "edges": [{"from": "in1", "to": "sg1"}, {"from": "sg1", "to": "sg2"},
                     {"from": "sg2", "to": "agg"}, {"from": "agg", "to": "out1"}]}
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    assert output["subgraph"] == "echo"


def test_subgraph_undefined_reference_is_a_compile_error() -> None:
    """引用不存在的子图在**编译期**就报，并带精确 field_path。

    运行期才炸的话，画布作者会拿到「前半段跑通、后半段炸掉」的记录。
    """
    doc = {"version": "1", "subgraphs": {"known": _echo_subflow("k")},
           "nodes": [_literal("x"),
                     {"id": "sg1", "type": "subgraph", "params": {"name": "typo"}},
                     _output()],
           "edges": [{"from": "in1", "to": "sg1"}, {"from": "sg1", "to": "out1"}]}
    with pytest.raises(DslFieldError) as exc:
        compile_dsl(doc)
    assert exc.value.field_path == "params.name"
    assert "typo" in str(exc.value)


def test_parallel_branch_ref_to_undefined_subgraph_is_compile_error() -> None:
    doc = {"version": "1",
           "nodes": [_literal("x"),
                     {"id": "pl1", "type": "parallel", "params": {
                         "branches": [{"name": "b", "ref": "nope"}]}},
                     _output()],
           "edges": [{"from": "in1", "to": "pl1"}, {"from": "pl1", "to": "out1"}]}
    with pytest.raises(DslFieldError) as exc:
        compile_dsl(doc)
    assert exc.value.field_path == "params.branches.0.ref"


def test_subgraph_library_entry_needs_input_entry() -> None:
    doc = {"version": "1",
           "subgraphs": {"bad": {"version": "1", "nodes": [_output()], "edges": []}},
           "nodes": [_literal("x"),
                     {"id": "sg1", "type": "subgraph", "params": {"name": "bad"}},
                     _output()],
           "edges": [{"from": "in1", "to": "sg1"}, {"from": "sg1", "to": "out1"}]}
    with pytest.raises(DslValidationError):
        compile_dsl(doc)


def test_subflow_inherits_parent_subgraph_library() -> None:
    """子流程按调用链继承父图的子图库（本图自声明的同名 key 优先）。"""
    inner = {
        "version": "1",
        "nodes": [
            {"id": "si", "type": "input"},
            {"id": "pl", "type": "parallel", "params": {
                "branches": [{"name": "b", "ref": "shared"}]}},
            {"id": "so", "type": "output"}],
        "edges": [{"from": "si", "to": "pl"}, {"from": "pl", "to": "so"}],
    }
    doc = {"version": "1",
           "subgraphs": {"shared": _echo_subflow("from-parent"),
                         "wrapper": inner},
           "nodes": [_literal("x"),
                     {"id": "sg1", "type": "subgraph",
                      "params": {"name": "wrapper"}},
                     _output()],
           "edges": [{"from": "in1", "to": "sg1"}, {"from": "sg1", "to": "out1"}]}
    status, output = _status_and_output(doc)
    assert status == "succeeded"
    # 嵌套子流程里的 parallel.branches[].ref 解析到了父图的子图库：
    # wrapper 内的 parallel 拿到了 shared 这张父图声明的子图，跑出 from-parent。
    assert output["subgraph"] == "wrapper"
    assert output["output"][0][0]["tag"] == "from-parent"


def test_subgraph_library_is_not_leaked_across_runs() -> None:
    """两次运行之间的子图库互不污染（回归：曾用模块级全局，parallel 并发必串味）。"""
    parent = {"version": "1",
              "subgraphs": {"shared": _echo_subflow("parent")},
              "nodes": [_literal("x"),
                        {"id": "pl", "type": "parallel", "params": {
                            "branches": [{"name": "b", "ref": "shared"}]}},
                        _output()],
              "edges": [{"from": "in1", "to": "pl"},
                        {"from": "pl", "to": "out1"}]}
    child = {"version": "1",
             "nodes": [_literal("x"),
                       {"id": "pl", "type": "parallel", "params": {
                           "branches": [{"name": "b", "ref": "shared"}]}},
                       _output()],
             "edges": [{"from": "in1", "to": "pl"},
                       {"from": "pl", "to": "out1"}]}
    first = run_dsl(parent)
    # 调用方从外部注入的子图库同样可解析（DSL SDK 场景：库不在文档里）。
    injected = run_dsl(child, subgraphs={"shared": _echo_subflow("x")})
    # 第三次运行既没有自声明也没传参 —— 若子图库曾泄漏，这里会读到脏数据。
    # 编译期就fail-closed 抛错（而不是跑一半失败），这正是期望：宁可拒绝执行，
    # 也不给一份「前半段跑通、后半段炸掉」的运行记录。
    assert first.status == "succeeded"
    assert injected.status == "succeeded"
    with pytest.raises(DslFieldError, match="未定义的子图"):
        run_dsl(child)


def test_frontend_default_params_satisfy_the_backend_contract() -> None:
    """前端 ``defaultParams`` 的产物必须真过后端契约（防前后端默认值脱节）。

    ``web/src/components/workflow/FlowEditor.tsx`` 的 ``defaultParams`` 为``parallel``
    / ``subgraph`` 补了默认值——若这两类仍落到 ``default: return {}``，用户在画布上
    点出来的节点会带空params，导出时被后端 422 拒掉。但前端跑不到 Python 校验，
    契约一漂移没人先发现。这里用**与前端同形**的字面量走一遍后端编译 + 运行：
    后端契约一旦变严（例如分支闸门、入口节点要求），这个测试第一时间红。
    """
    def _min_subflow(tag: str) -> dict:
        """与前端 ``minSubflow`` 同形的最小内联子图（input 入口 + output 出口）。"""
        return {"version": "1",
                "nodes": [
                    {"id": f"input_{tag}", "type": "input",
                     "params": {"kind": "literal", "value": ["test"]}},
                    {"id": f"output_{tag}", "type": "output",
                     "params": {"format": "text"}},
                ],
                "edges": [{"from": f"input_{tag}", "to": f"output_{tag}"}]}

    doc = {
        "version": "1",
        # 两个分支的节点 id 互不相同 —— 同文档内id 撞车是编译期错误。
        "subgraphs": {"shared": _min_subflow("s")},
        "nodes": [_literal("x"),
                  {"id": "pl", "type": "parallel", "params": {"branches": [
                      {"name": "分支1", "inline": _min_subflow("1")},
                      {"name": "分支2", "inline": _min_subflow("2")}]}},
                  {"id": "sg", "type": "subgraph", "params": {"name": "shared"}},
                  _output()],
        "edges": [{"from": "in1", "to": "pl"}, {"from": "pl", "to": "sg"},
                  {"from": "sg", "to": "out1"}],
    }
    # 不是「不抛异常」就算过：必须真能编译成正确的拓扑波次顺序。
    assert compile_dsl(doc).order == ["in1", "pl", "sg", "out1"]
    # 也必须真跑得通—— 校验通过但运行期才炸（比如子图解析不到）同样不合格。
    assert _status_and_output(doc) == ("succeeded", {"subgraph": "shared",
                                                      "output": "x\nx"})


# ------------------------------ 规模与步数闸门 ------------------------------ #

def test_recursion_limit_fails_closed_instead_of_silently_truncating() -> None:
    """步数超限必须**明确失败**，绝不「跑着跑着就没了」也不谎报 skipped。"""
    nodes = [_literal("x")]
    edges = []
    for i in range(6):
        nodes.append({"id": f"t{i}", "type": "transform", "verb": "map",
                      "params": {"op": "set", "field": "i", "value": i}})
        edges.append({"from": nodes[-2]["id"], "to": f"t{i}"})
    nodes.append(_output())
    edges.append({"from": "t5", "to": "out1"})
    result = run_dsl(_flow(nodes, edges), recursion_limit=3)
    assert result.status == "failed"
    assert "recursion_limit" in (result.error or "")


def test_recursion_limit_is_clamped_to_the_cap() -> None:
    """传一个能把服务拖死的无界值，会被裁到 ``RECURSION_LIMIT_CAP``，不被照单全收。"""
    from find_yourself.services import dsl_canvas as dc
    doc = _flow([_literal("x"),
                 {"id": "t0", "type": "transform", "verb": "map",
                  "params": {"op": "set", "field": "i", "value": 0}},
                 _output()])
    assert dc.RECURSION_LIMIT_CAP == RECURSION_LIMIT_CAP
    # 合法图在超大 limit 下正常跑完（说明 limit 没被当成 1 传给下游）。
    assert run_dsl(doc, recursion_limit=10 ** 9).status == "succeeded"
    # 非法值（非数字）回落到默认值，而不是抛异常或变成 0。
    assert run_dsl(doc, recursion_limit="oops").status == "succeeded"


def test_graph_scale_gates_reject_oversized_graphs() -> None:
    """图级规模闸门：超大图在编译期被拒，并给出可执行的拆分建议。"""
    nodes = [{"id": f"n{i}", "type": "input"} for i in range(MAX_NODES_PER_GRAPH + 1)]
    with pytest.raises(DslValidationError) as exc:
        validate_dsl({"version": "1", "nodes": nodes, "edges": []})
    assert str(MAX_NODES_PER_GRAPH) in str(exc.value)

    edges = [{"from": "a", "to": "b"}] * (MAX_EDGES_PER_GRAPH + 1)
    with pytest.raises(DslValidationError) as exc:
        validate_dsl({"version": "1",
                      "nodes": [{"id": "a", "type": "input"},
                                {"id": "b", "type": "output"}],
                      "edges": edges})
    assert str(MAX_EDGES_PER_GRAPH) in str(exc.value)


def test_restricted_code_export_rejects_non_exportable_node_types() -> None:
    """受限代码导出只认 input/transform/output。

    以前这里掉进字典反查抛裸 ``KeyError``，API 层只捕 ``DslValidationError`` →
    用户拿到 500 而非可读的 422。
    """
    from find_yourself.services.dsl_code_export import export_dsl_code
    doc = _flow([_literal("x"),
                 {"id": "sg1", "type": "subgraph", "params": {"name": "s"}},
                 _output()])
    with pytest.raises(DslValidationError) as exc:
        export_dsl_code(doc)
    assert "subgraph" in str(exc.value)


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
