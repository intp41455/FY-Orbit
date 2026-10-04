"""需求 5 ·受限动词集与代码导出（往返一致性）单测。

三条被测不变量：

1. **封闭性**——白名单外的 verb 必须让 :func:`validate_dsl` 失败；受限动词集的
   「受限」二字全靠这一条落地（静默忽略等于没有边界）。
2. **每个动词都真的能跑**——注册表里 9 个动词逐个执行，断言真实输出而不只是
   「不抛异常」。
3. **代码导出往返一致**——``画布 → export_dsl_code → parse_dsl_code → 等价画布``，
   且导出物里不可能出现受限动词集之外的动词。

外加解析器的封闭性：导出代码只允许五种构造调用，``import``/赋值/循环/函数定义/
属性访问一律拒绝（``ast`` 静态解析，**从不 eval**）。

结构照``tests/unit/test_preview_sources.py``：内存 SQLite + ``create_all``，
经真实 HTTP 面打接口。
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime
from find_yourself.services.dsl_canvas import (
    AGGREGATE_OPS,
    MAP_OPS,
    MERGE_OPS,
    OUTPUT_FORMATS,
    TRANSFORM_VERBS,
    VERB_REGISTRY,
    DslSuspended,
    DslValidationError,
    canonical_dsl,
    compile_dsl,
    dsl_digest,
    execute_node,
    run_dsl,
    validate_dsl,
    verb_catalog,
)
from find_yourself.services.dsl_code_export import export_dsl_code, parse_dsl_code

LOCAL_TOKEN = "dev-token-secret-verbset"


# --------------------------------------------------------------------------- #
# 测试装置（内存 SQLite + 真实 app）
# --------------------------------------------------------------------------- #
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=__import__("datetime").timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker, tmp_path) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    return create_app(session_maker=session_maker, settings=settings)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# --------------------------------------------------------------------------- #
# 构造器
# --------------------------------------------------------------------------- #
def _doc(nodes: list[dict], edges: list[dict]) -> dict:
    return {"version": "1", "nodes": nodes, "edges": edges}


def _lit(value) -> dict:
    return {"id": "src", "type": "input", "params": {"kind": "literal", "value": value}}


def _xf(node_id: str, verb: str, **params) -> dict:
    return {"id": node_id, "type": "transform", "verb": verb, "params": params}


def _out(node_id: str = "out", fmt: str = "json") -> dict:
    return {"id": node_id, "type": "output", "params": {"format": fmt}}


# =========================================================================== #
# 1. 封闭性：白名单外的动词必须被拒（需求 5 的核心闸门）
# =========================================================================== #
class TestVerbSetIsClosed:
    @pytest.mark.parametrize("verb", [
        "exec", "eval", "llm", "python", "sql", "subprocess", "translate",
        "call_function", "Map", "loop", "code", "shell",
    ])
    def test_verb_outside_whitelist_is_rejected(self, verb: str):
        """白名单外的动词**明确报错**，绝不静默忽略。"""
        doc = _doc([{"id": "t", "type": "transform", "verb": verb},
                    {"id": "o", "type": "output"}],
                   [{"from": "t", "to": "o"}])
        with pytest.raises(DslValidationError) as exc:
            validate_dsl(doc)
        assert "verb" in str(exc.value)

    def test_rejection_names_the_offending_verb(self):
        """错误信息必须带上模型实际写出的动词，便于自我修正。"""
        doc = _doc([{"id": "t", "type": "transform", "verb": "translate",
                     "params": {"template": "x"}},
                    {"id": "o", "type": "output"}],
                   [{"from": "t", "to": "o"}])
        with pytest.raises(DslValidationError) as exc:
            validate_dsl(doc)
        assert "translate" in str(exc.value)
        assert "map" in str(exc.value)  # 同时告知允许集

    def test_verb_case_and_whitespace_variants_are_rejected(self):
        """大小写/空格变体同样出闸（白名单是精确匹配，不做模糊归一）。"""
        for verb in ("Map", " map", "map ", "aggregate\t"):
            doc = _doc([{"id": "t", "type": "transform", "verb": verb,
                         "params": {"op": "count"}},
                        {"id": "o", "type": "output"}],
                       [{"from": "t", "to": "o"}])
            with pytest.raises(DslValidationError):
                validate_dsl(doc)

    def test_compile_dsl_also_rejects_unknown_verb(self):
        """编译器同样过不了这一关（不能只靠上层拦）。"""
        doc = _doc([{"id": "t", "type": "transform", "verb": "nope"},
                    {"id": "o", "type": "output"}],
                   [{"from": "t", "to": "o"}])
        with pytest.raises(DslValidationError):
            compile_dsl(doc)

    def test_executor_refuses_unknown_verb_even_when_called_directly(self):
        """绕过 validate_dsl 直接执行时，执行器仍有兜底闸门。"""
        with pytest.raises(DslValidationError, match="受限动词集"):
            execute_node({"id": "t", "type": "transform", "verb": "nope"},
                         payload=[1, 2])

    def test_params_outside_contract_are_rejected(self):
        """动词在册但参数越界（如map 塞 template）同样拒绝 —— 契约是封闭的。"""
        doc = _doc([_xf("t", "map", op="set", field="a", template="越界参数"),
                    _out()],
                   [{"from": "t", "to": "out"}])
        with pytest.raises(DslValidationError, match="template"):
            validate_dsl(doc)

    def test_registry_and_whitelist_are_the_same_source_of_truth(self):
        """TRANSFORM_VERBS 由注册表派生，二者不可能漂移。"""
        assert set(TRANSFORM_VERBS) == set(VERB_REGISTRY)
        assert len(TRANSFORM_VERBS) == len(set(TRANSFORM_VERBS))

    def test_json_schema_enum_matches_whitelist(self):
        """对外暴露的 JSON Schema 与白名单一致（前端据此渲染）。"""
        from find_yourself.services.dsl_canvas import DSL_JSON_SCHEMA
        node_schema = DSL_JSON_SCHEMA["properties"]["nodes"]["items"]
        assert node_schema["properties"]["verb"]["enum"] == list(TRANSFORM_VERBS)

    def test_verb_catalog_exposes_contract_for_every_verb(self):
        """每个动词都必须在 catalog 里有名字 + 分类 + 参数契约。"""
        catalog = verb_catalog()
        assert [v["name"] for v in catalog] == list(TRANSFORM_VERBS)
        for entry in catalog:
            assert entry["category"] and entry["summary"]
            assert entry["params_schema"]["type"] == "object"
            assert entry["params_schema"]["additionalProperties"] is False


# =========================================================================== #
# 2. 每个动词都必须能跑（断言真实输出，不只是「没抛异常」）
# =========================================================================== #
class TestEveryVerbExecutes:
    def test_map_and_filter_and_template_still_behave(self):
        """既有三动词语义不得回归。"""
        doc = _doc(
            [_lit([{"name": "a", "score": 90}, {"name": "b", "score": 40}]),
             _xf("hi", "filter", field="score", op="gt", value=60),
             _xf("tag", "map", op="set", field="grade", value="优秀:{name}"),
             _out()],
            [{"from": "src", "to": "hi"}, {"from": "hi", "to": "tag"},
             {"from": "tag", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == [{"name": "a", "score": 90, "grade": "优秀:a"}]

    def test_map_upper_and_lower(self):
        for op, expect in (("upper", "AB"), ("lower", "ab")):
            doc = _doc([_lit(["ab"]),
                        _xf("m", "map", op=op),
                        _out(fmt="text")],
                       [{"from": "src", "to": "m"}, {"from": "m", "to": "out"}])
            result = run_dsl(doc)
            assert result.status == "succeeded", result.error
            assert result.output == expect

    def test_branch_emits_label_and_preserves_payload(self):
        """branch 把判定写进数据面，供下游边 condition 路由。"""
        for mode, label in (("fast", "fast"), ("slow", "slow")):
            doc = _doc([_lit({"mode": mode}),
                        _xf("br", "branch", field="mode", op="eq", value="fast",
                            then_label="fast", else_label="slow"),
                        _out()],
                       [{"from": "src", "to": "br"}, {"from": "br", "to": "out"}])
            result = run_dsl(doc)
            assert result.status == "succeeded", result.error
            assert result.output == {"branch": label, "value": {"mode": mode}}

    def test_branch_output_can_route_downstream_edge_condition(self):
        """branch + 边condition 联动：不命中的分支记skipped。

        注意载荷形状：branch 产出 ``{"branch": 标签, "value": 原载荷}``，
        所以边 condition 读顶层 ``branch``，而下游模板要读 ``{value.mode}``。
        """
        doc = _doc(
            [_lit({"mode": "fast"}),
             _xf("br", "branch", field="mode", op="eq", value="fast",
                 then_label="fast", else_label="slow"),
             _xf("fast", "template", template="快速:{value.mode}"),
             _xf("slow", "template", template="慢速:{value.mode}"),
             _out(fmt="text")],
            [{"from": "src", "to": "br"},
             {"from": "br", "to": "fast",
              "condition": {"field": "branch", "op": "eq", "value": "fast"}},
             {"from": "br", "to": "slow",
              "condition": {"field": "branch", "op": "eq", "value": "slow"}},
             {"from": "fast", "to": "out"}, {"from": "slow", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == "快速:fast"
        statuses = {log.node_id: log.status for log in result.logs}
        assert statuses["fast"] == "succeeded"
        assert statuses["slow"] == "skipped"

    @pytest.mark.parametrize("op,expect", [
        ("count", 3), ("sum", 150), ("min", 10), ("max", 90), ("avg", 50.0),
    ])
    def test_aggregate_numeric_ops(self, op: str, expect):
        doc = _doc([_lit([{"score": 90}, {"score": 10}, {"score": 50}]),
                    _xf("agg", "aggregate", op=op, field="score"),
                    _out()],
                   [{"from": "src", "to": "agg"}, {"from": "agg", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == expect

    def test_aggregate_first_last_join_unique(self):
        """给了 field 时，全部算子都作用于 field 取值（unique 也是）。"""
        payload = [{"t": "b"}, {"t": "a"}, {"t": "b"}]
        for op, expect in (
            ("first", "b"), ("last", "b"),
            ("join", "b,a,b"), ("unique", ["b", "a"]),
        ):
            doc = _doc([_lit(payload),
                        _xf("agg", "aggregate", op=op, field="t", sep=","),
                        _out()],
                       [{"from": "src", "to": "agg"},
                        {"from": "agg", "to": "out"}])
            result = run_dsl(doc)
            assert result.status == "succeeded", result.error
            assert result.output == expect, op

    def test_aggregate_unique_without_field_dedupes_whole_items(self):
        """不给 field 时unique 对整项去重（保序）。"""
        doc = _doc([_lit([{"t": "b"}, {"t": "a"}, {"t": "b"}]),
                    _xf("agg", "aggregate", op="unique"),
                    _out()],
                   [{"from": "src", "to": "agg"}, {"from": "agg", "to": "out"}])
        result = run_dsl(doc)
        assert result.output == [{"t": "b"}, {"t": "a"}]

    def test_aggregate_first_without_field_returns_whole_item(self):
        """不给field 时first 返回整项（而不是 None）。"""
        doc = _doc([_lit([{"t": "b"}, {"t": "a"}]),
                    _xf("agg", "aggregate", op="first"),
                    _out()],
                   [{"from": "src", "to": "agg"}, {"from": "agg", "to": "out"}])
        result = run_dsl(doc)
        assert result.output == {"t": "b"}

    def test_aggregate_count_needs_no_field(self):
        doc = _doc([_lit([1, 2, 3]),
                    _xf("agg", "aggregate", op="count"),
                    _out()],
                   [{"from": "src", "to": "agg"}, {"from": "agg", "to": "out"}])
        assert run_dsl(doc).output == 3

    def test_aggregate_rejects_non_numeric_for_sum(self):
        doc = _doc([_lit([{"score": "高"}]),
                    _xf("agg", "aggregate", op="sum", field="score"),
                    _out()],
                   [{"from": "src", "to": "agg"}, {"from": "agg", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "failed"
        assert "数值" in (result.error or "")

    def test_merge_converges_two_parallel_branches(self):
        """并行汇聚：两路入边收敛成一路。"""
        doc = _doc(
            [_lit([{"v": 1}, {"v": 2}]),
             _xf("left", "template", template="L{v}"),
             _xf("right", "map", op="upper"),
             _xf("m", "merge", mode="concat"),
             _out()],
            [{"from": "src", "to": "left"}, {"from": "src", "to": "right"},
             {"from": "left", "to": "m"}, {"from": "right", "to": "m"},
             {"from": "m", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        # left 产出 [L1, L2]，right 原样透传；merge.concat 拉平两路。
        assert result.output == ["L1", "L2", {"v": 1}, {"v": 2}]

    @pytest.mark.parametrize("mode,expect", [
        ("first", "L1"), ("last", "L2"), ("concat", ["L1", "L2"]),
    ])
    def test_merge_modes(self, mode: str, expect):
        """单路上游时 merge 的三种策略（拉平后取首/末/全部）。"""
        doc = _doc(
            [_lit([{"v": 1}, {"v": 2}]),
             _xf("left", "template", template="L{v}"),
             _xf("m", "merge", mode=mode),
             _out()],
            [{"from": "src", "to": "left"}, {"from": "left", "to": "m"},
             {"from": "m", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == expect

    def test_artifact_wraps_payload_in_named_envelope(self):
        doc = _doc([_lit({"k": 1}),
                    _xf("art", "artifact", name="report", kind="table"),
                    _out()],
                   [{"from": "src", "to": "art"}, {"from": "art", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "succeeded", result.error
        assert result.output == {"artifact": "report", "kind": "table",
                                 "content": {"k": 1}}

    def test_artifact_kind_defaults_to_generic(self):
        doc = _doc([_lit([1]), _xf("art", "artifact", name="n"), _out()],
                   [{"from": "src", "to": "art"}, {"from": "art", "to": "out"}])
        result = run_dsl(doc)
        assert result.output["kind"] == "generic"

    def test_agent_calls_injected_resolver(self):
        """agent 是「确定性外壳 + 不受限内核」：解析器由调用方注入。"""
        calls: list[tuple] = []

        def resolver(name, payload, params):
            calls.append((name, payload, dict(params)))
            return {"agent": name, "echo": payload}

        doc = _doc([_lit({"q": "你好"}),
                    _xf("ag", "agent", agent="summarizer"),
                    _out()],
                   [{"from": "src", "to": "ag"}, {"from": "ag", "to": "out"}])
        result = run_dsl(doc, agent_resolver=resolver)
        assert result.status == "succeeded", result.error
        assert result.output == {"agent": "summarizer", "echo": {"q": "你好"}}
        assert calls[0][0] == "summarizer"

    def test_agent_without_resolver_fails_honestly(self):
        """未注入解析器 → 明确失败，绝不假装调用成功。"""
        doc = _doc([_lit({"q": 1}),
                    _xf("ag", "agent", agent="summarizer"),
                    _out()],
                   [{"from": "src", "to": "ag"}, {"from": "ag", "to": "out"}])
        result = run_dsl(doc)
        assert result.status == "failed"
        assert "agent_resolver" in (result.error or "")

    def test_confirm_suspends_until_human_decision(self):
        """confirm 是控制流信号（DslSuspended），**不是节点失败**。"""
        doc = _doc([_lit({"amount": 100}),
                    _xf("cf", "confirm", prompt="确认这笔金额？", role="owner"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        with pytest.raises(DslSuspended) as exc:
            run_dsl(doc, execution_id="exec-1")
        susp = exc.value
        assert susp.checkpoint == "exec-1#cf"
        assert susp.node_id == "cf"
        assert susp.context["prompt"] == "确认这笔金额？"
        assert susp.context["role"] == "owner"
        # 二元语义：多选项属于团队审批流（需求 6），不该泛化 confirm。
        assert [o["value"] for o in susp.options] == ["approve", "reject"]
        assert susp.dsl_digest == dsl_digest(doc)

    def test_confirm_carries_digest_for_toctou_guard(self):
        """挂起必须带文档摘要，好让裁决方验证「人所见即所批」。"""
        doc = _doc([_lit({"amount": 100}),
                    _xf("cf", "confirm", prompt="确认？"), _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        with pytest.raises(DslSuspended) as exc:
            run_dsl(doc, execution_id="e")
        assert exc.value.dsl_digest
        # 图一改，摘要就变 —— 裁决方据此拒绝「按旧版拍板」。
        changed = json.loads(json.dumps(doc))
        changed["nodes"][0]["params"]["value"] = {"amount": 999}
        assert dsl_digest(changed) != exc.value.dsl_digest

    def test_confirm_approve_resumes_and_runs_to_completion(self):
        """裁决为 approve → 重开一轮 run_dsl，一路跑到底。"""
        doc = _doc([_lit({"amount": 100}),
                    _xf("cf", "confirm", prompt="确认？"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        result = run_dsl(doc, execution_id="exec-1",
                         confirm_decision=lambda nid, ex: "approve")
        assert result.status == "succeeded", result.error
        assert result.output == {"amount": 100}
        statuses = {log.node_id: log.status for log in result.logs}
        assert statuses == {"src": "succeeded", "cf": "succeeded", "out": "succeeded"}

    def test_confirm_reject_fails_explicitly(self):
        """裁决为 reject → 明确失败（不静默放行，也不静默跳过）。"""
        doc = _doc([_lit({"amount": 100}),
                    _xf("cf", "confirm", prompt="确认？"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        result = run_dsl(doc, execution_id="e",
                         confirm_decision=lambda nid, ex: "reject")
        assert result.status == "failed"
        assert "驳回" in (result.error or "")
        assert next(l for l in result.logs if l.node_id == "out").status == "skipped"

    def test_confirm_reader_seeing_no_decision_still_suspends(self):
        """读取器说「尚无裁决」→ 仍挂起，绝不猜成approve。"""
        doc = _doc([_lit({"amount": 1}), _xf("cf", "confirm", prompt="确认？"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        with pytest.raises(DslSuspended):
            run_dsl(doc, execution_id="e", confirm_decision=lambda nid, ex: None)

    def test_confirm_never_trusts_a_decision_written_into_the_document(self):
        """文档里自称「已批准」绝不算数—— 裁决只认注入的读取器。"""
        doc = _doc([_lit({"amount": 1}),
                    _xf("cf", "confirm", prompt="确认？", decision="approve"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        # 参数契约封闭：塞不进 decision 键。
        with pytest.raises(DslValidationError, match="decision"):
            validate_dsl(doc)
        # 即使强行构造出该节点，无读取器时也只会挂起。
        forged = _doc([_lit({"amount": 1}),
                       {"id": "cf", "type": "transform", "verb": "confirm",
                        "params": {"prompt": "确认？"}},
                       _out()],
                      [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        with pytest.raises(DslSuspended):
            run_dsl(forged, execution_id="e")

    def test_suspend_does_not_swallow_into_generic_failure(self):
        """DslSuspended 必须穿透执行器的两处 except Exception。

        若被吞掉，挂起会静默退化成「节点 failed + 分支终止」，上层永远等不到
        interrupt —— 这是最难排查的假绿，故显式钉住。
        """
        doc = _doc([_lit({"amount": 1}), _xf("cf", "confirm", prompt="确认？"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        # 不是 DslValidationError（普通失败），也不是 RunResult（被降级）。
        with pytest.raises(DslSuspended):
            run_dsl(doc, execution_id="e")

    def test_execution_id_is_required_for_resumable_checkpoints(self):
        """不传 execution_id 也能挂起，但 checkpoint 明确标记为无归属。"""
        doc = _doc([_lit({"amount": 1}), _xf("cf", "confirm", prompt="确认？"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        with pytest.raises(DslSuspended) as exc:
            run_dsl(doc)
        assert exc.value.checkpoint == "-#cf"  # 显式占位，不静默用 run_id

    def test_digest_is_stable_for_semantically_equal_canvases(self):
        """语义等价的画布摘要相同（``params: {}`` 与省略 params 归一后一致）。

        注意对照：``format:"text"`` 与省略 ``format``（默认 ``json``）**不是**
        等价画布，摘要必须不同——否则乐观锁会放过「所见非所批」。
        """
        a = _doc([_lit("x"), {"id": "out", "type": "output", "params": {}}],
                 [{"from": "src", "to": "out"}])
        b = _doc([_lit("x"), {"id": "out", "type": "output"}],
                 [{"from": "src", "to": "out"}])
        assert canonical_dsl(a) == canonical_dsl(b)
        assert dsl_digest(a) == dsl_digest(b)

        text_fmt = _doc([_lit("x"), {"id": "out", "type": "output",
                                     "params": {"format": "text"}}],
                        [{"from": "src", "to": "out"}])
        assert dsl_digest(text_fmt) != dsl_digest(b)

    def test_every_verb_in_registry_has_an_executor_and_runs(self):
        """注册表里每个动词都真的接到了实现（漏一个就红）。"""
        for name in TRANSFORM_VERBS:
            spec = VERB_REGISTRY[name]
            assert callable(spec.execute), name
            assert spec.params_schema["type"] == "object", name

    def test_aggregate_ops_and_merge_ops_are_exhaustive(self):
        assert set(AGGREGATE_OPS) <= set(VERB_REGISTRY["aggregate"]
                                         .params_schema["properties"]["op"]["enum"])
        assert set(MERGE_OPS) == set(VERB_REGISTRY["merge"]
                                     .params_schema["properties"]["mode"]["enum"])


# =========================================================================== #
# 3. 代码导出往返一致（画布 → 代码 → 画布）
# =========================================================================== #
def _kitchen_sink_doc() -> dict:
    """一张覆盖全部注册动词 + 条件边 + 各种参数类型的画布。"""
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
            _xf("ap", "approval", op="memory.upsert", target_id="mem-1",
                reason="归档报表", rollback="删除产物"),
            _out(fmt="json"),
        ],
        [
            {"from": "src", "to": "keep"}, {"from": "keep", "to": "tag"},
            {"from": "tag", "to": "txt"}, {"from": "txt", "to": "br"},
            {"from": "br", "to": "agg"}, {"from": "br", "to": "agg2"},
            {"from": "agg", "to": "mrg"}, {"from": "agg2", "to": "mrg"},
            {"from": "mrg", "to": "art"}, {"from": "art", "to": "ag"},
            {"from": "ag", "to": "cf"}, {"from": "cf", "to": "ap"},
            {"from": "ap", "to": "out"},
        ],
    )


class TestCodeExportRoundTrip:
    def test_roundtrip_is_lossless_for_kitchen_sink_canvas(self):
        """画布 → 导出 → 解析 → **等价画布**（含全部动词与条件边）。"""
        doc = _kitchen_sink_doc()
        exported = export_dsl_code(doc)
        parsed = parse_dsl_code(exported["code"])
        assert parsed == canonical_dsl(doc)
        assert validate_dsl(parsed) is parsed  # 解析结果仍是合法 DSL

    def test_roundtrip_is_lossless_for_minimal_canvas(self):
        doc = _doc([_lit("hello"), _out(fmt="text")],
                   [{"from": "src", "to": "out"}])
        assert parse_dsl_code(export_dsl_code(doc)["code"]) == canonical_dsl(doc)

    def test_roundtrip_preserves_edge_conditions(self):
        doc = _doc(
            [_lit({"mode": "fast"}),
             _xf("a", "template", template="A"),
             _xf("b", "template", template="B"),
             _out(fmt="text")],
            [{"from": "src", "to": "a",
              "condition": {"field": "mode", "op": "eq", "value": "fast"}},
             {"from": "src", "to": "b",
              "condition": {"field": "mode", "op": "eq", "value": "slow"}},
             {"from": "a", "to": "out"}, {"from": "b", "to": "out"}])
        assert parse_dsl_code(export_dsl_code(doc)["code"]) == canonical_dsl(doc)

    def test_roundtrip_preserves_unicode_and_nested_json(self):
        """中文/嵌套结构/布尔/浮点都必须无损（repr 字面量足够保真）。"""
        value = {"名称": "张三", "nested": [{"k": [1, 2.5, True, None, "引号\"x\""]}]}
        doc = _doc([_lit(value), _out()], [{"from": "src", "to": "out"}])
        parsed = parse_dsl_code(export_dsl_code(doc)["code"])
        assert parsed == canonical_dsl(doc)
        assert parsed["nodes"][0]["params"]["value"] == value

    def test_roundtrip_is_idempotent(self):
        """导出 → 解析 → 再导出，代码逐字节相同（导出是确定性的）。"""
        doc = _kitchen_sink_doc()
        once = export_dsl_code(doc)["code"]
        twice = export_dsl_code(parse_dsl_code(once))["code"]
        assert once == twice

    def test_exported_code_only_contains_whitelisted_verbs(self):
        """导出物**不可能**包含受限动词集之外的动词。"""
        code = export_dsl_code(_kitchen_sink_doc())["code"]
        for name in VERB_REGISTRY:
            assert f"verb={name!r}" in code
        # 逐个扫描所有 verb= 取值，必须全在白名单内。
        import re
        for found in re.findall(r"verb='([^']+)'", code):
            assert found in VERB_REGISTRY, found

    def test_exported_code_has_no_import_of_arbitrary_modules(self):
        """除find_yourself_dsl 外不得有别的 import（也便于解析器封闭）。"""
        code = export_dsl_code(_kitchen_sink_doc())["code"]
        imports = [ln.strip() for ln in code.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        assert imports == [
            "from find_yourself_dsl import Flow, edge, input_node, output_node,"
            " transform_node"]

    def test_exported_code_contains_no_secret(self):
        """导出物不含任何疑似密钥的赋值（对齐既有导出脚本的自律）。"""
        code = export_dsl_code(_kitchen_sink_doc())["code"]
        for line in code.splitlines():
            low = line.lower()
            assert "api_key" not in low
            assert "secret" not in low
            assert "token" not in low
            assert "bearer " not in low

    def test_exported_code_is_valid_python_syntax(self):
        import ast
        ast.parse(export_dsl_code(_kitchen_sink_doc())["code"])  # 不抛即合法

    def test_exported_code_reports_counts_and_verbs(self):
        doc = _kitchen_sink_doc()
        out = export_dsl_code(doc)
        assert out["node_count"] == len(doc["nodes"])
        assert out["edge_count"] == len(doc["edges"])
        assert out["verbs"] == list(TRANSFORM_VERBS)
        assert out["filename"].endswith(".py")

    def test_export_rejects_non_whitelisted_verb(self):
        """白名单外动词**导不出**代码（绝不生成半成品）。"""
        doc = _doc([{"id": "t", "type": "transform", "verb": "exec"},
                    _out()], [{"from": "t", "to": "out"}])
        with pytest.raises(DslValidationError):
            export_dsl_code(doc)

    def test_export_rejects_cyclic_canvas(self):
        cyclic = _doc([_lit(1), _out()],
                      [{"from": "src", "to": "out"}, {"from": "out", "to": "src"}])
        with pytest.raises(DslValidationError, match="环"):
            export_dsl_code(cyclic)

    def test_export_result_can_run_and_match_direct_execution(self):
        """导出→解析回来的 DSL 与原画布执行结果一致（导出不改变语义）。"""
        doc = _doc(
            [_lit([{"name": "张三", "score": 90}, {"name": "李四", "score": 40}]),
             _xf("hi", "filter", field="score", op="gt", value=60),
             _xf("agg", "aggregate", op="avg", field="score"),
             _xf("art", "artifact", name="均值"),
             _out()],
            [{"from": "src", "to": "hi"}, {"from": "hi", "to": "agg"},
             {"from": "agg", "to": "art"}, {"from": "art", "to": "out"}])
        direct = run_dsl(doc)
        round_tripped = run_dsl(parse_dsl_code(export_dsl_code(doc)["code"]))
        assert direct.status == round_tripped.status == "succeeded"
        assert direct.output == round_tripped.output == {
            "artifact": "均值", "kind": "generic", "content": 90.0}


# =========================================================================== #
# 4. 解析器封闭性：导出代码之外的一切都不接受（ast 静态解析，绝不 eval）
# =========================================================================== #
class TestParserIsRestricted:
    def test_rejects_verb_outside_whitelist(self):
        code = (
            "from find_yourself_dsl import Flow, transform_node, output_node\n"
            "Flow(version='1')\n"
            "transform_node('t', verb='exec')\n"
            "output_node('o')\n"
        )
        with pytest.raises(DslValidationError, match="verb"):
            parse_dsl_code(code)

    def test_rejects_extra_param_outside_contract(self):
        code = (
            "from find_yourself_dsl import Flow, transform_node, output_node\n"
            "Flow(version='1')\n"
            "transform_node('t', verb='template', template='x', evil='y')\n"
            "output_node('o')\n"
        )
        with pytest.raises(DslValidationError, match="evil"):
            parse_dsl_code(code)

    def test_rejects_import_of_other_modules(self):
        code = "import os\nFlow(version='1')\n"
        with pytest.raises(DslValidationError, match="import"):
            parse_dsl_code(code)

    def test_rejects_arbitrary_statements(self):
        for snippet in (
            "x = 1\n",
            "del x\n",
            "for i in range(3): pass\n",
            "while True: break\n",
            "def f(): pass\n",
            "class C: pass\n",
            "if True: pass\n",
            "raise SystemExit('boom')\n",
            "print('hi')\n",
            "import_module('os')\n",
        ):
            code = ( "from find_yourself_dsl import Flow\nFlow(version='1')\n"
                     + snippet)
            with pytest.raises(DslValidationError):
                parse_dsl_code(code, )

    def test_rejects_function_calls_in_arguments(self):
        """参数位置只允许字面量：任何调用/属性访问都被拒（不执行代码）。"""
        for snippet in (
            "transform_node('t', verb='template', template=str(1))",
            "transform_node('t', verb='template', template=open('x'))",
            "transform_node('t', verb='template', template=__import__('os'))",
            "transform_node('t', verb='template', template=(1).__class__)",
            "transform_node('t', verb='template', template=f'{1+1}')",
        ):
            code = ("from find_yourself_dsl import Flow, transform_node\n"
                    "Flow(version='1')\n" + snippet + "\n")
            with pytest.raises(DslValidationError):
                parse_dsl_code(code)

    def test_rejects_unknown_constructor_calls(self):
        code = "from find_yourself_dsl import Flow\nFlow(version='1')\nevil('x')\n"
        with pytest.raises(DslValidationError, match="构造调用"):
            parse_dsl_code(code)

    def test_rejects_syntax_error(self):
        with pytest.raises(DslValidationError, match="合法 Python"):
            parse_dsl_code("Flow(version='1'\n")

    def test_rejects_empty_code(self):
        for empty in ("", "   \n"):
            with pytest.raises(DslValidationError, match="为空"):
                parse_dsl_code(empty)

    def test_rejects_missing_flow_constructor(self):
        code = ("from find_yourself_dsl import output_node\n"
                "output_node('o')\n")
        with pytest.raises(DslValidationError, match="Flow"):
            parse_dsl_code(code)

    def test_rejects_edge_with_wrong_arity(self):
        code = ("from find_yourself_dsl import Flow, edge\n"
                "Flow(version='1')\nedge('a')\n")
        with pytest.raises(DslValidationError, match="两个位置参数"):
            parse_dsl_code(code)

    def test_rejects_kwargs_unpacking(self):
        code = ("from find_yourself_dsl import Flow, output_node\n"
                "Flow(version='1')\noutput_node('o', **kw)\n")
        with pytest.raises(DslValidationError, match="解包"):
            parse_dsl_code(code)

    def test_rejects_dangling_edge_reference(self):
        code = ("from find_yourself_dsl import Flow, edge, output_node\n"
                "Flow(version='1')\noutput_node('o')\nedge('o', 'ghost')\n")
        with pytest.raises(DslValidationError, match="未定义节点"):
            parse_dsl_code(code)

    def test_rejects_duplicate_node_ids(self):
        code = ("from find_yourself_dsl import Flow, output_node\n"
                "Flow(version='1')\noutput_node('o')\noutput_node('o')\n")
        with pytest.raises(DslValidationError, match="重复"):
            parse_dsl_code(code)


# =========================================================================== #
# 5. HTTP 面：schema 暴露动词集/ 导出与导入端点
# =========================================================================== #
class TestHttpSurface:
    def test_schema_exposes_full_verb_catalog(self, client: TestClient):
        body = client.get("/api/dsl-canvas/schema").json()
        assert body["node_types"] == ["input", "transform", "output"]
        assert body["transform_verbs"] == list(TRANSFORM_VERBS)
        assert [v["name"] for v in body["verb_catalog"]] == list(TRANSFORM_VERBS)
        assert body["aggregate_ops"] == list(AGGREGATE_OPS)
        assert body["merge_ops"] == list(MERGE_OPS)
        assert body["output_formats"] == list(OUTPUT_FORMATS)

    def test_export_then_import_roundtrip_over_http(self, client: TestClient,
                                                    headers: dict[str, str]):
        doc = _kitchen_sink_doc()
        r = client.post("/api/dsl-canvas/export-code", json={"dsl": doc},
                        headers=headers)
        assert r.status_code == 200, r.text
        exported = r.json()
        assert exported["filename"].endswith(".py")

        back = client.post("/api/dsl-canvas/import-code",
                           json={"code": exported["code"]}, headers=headers)
        assert back.status_code == 200, back.text
        assert back.json()["dsl"] == canonical_dsl(doc)
        assert back.json()["topological_order"] == compile_dsl(doc).order

    def test_export_endpoint_rejects_non_whitelisted_verb(self, client: TestClient,
                                                          headers: dict[str, str]):
        doc = _doc([{"id": "t", "type": "transform", "verb": "exec"}, _out()],
                   [{"from": "t", "to": "out"}])
        r = client.post("/api/dsl-canvas/export-code", json={"dsl": doc},
                        headers=headers)
        assert r.status_code == 422
        assert "verb" in r.json()["detail"]

    def test_import_endpoint_rejects_evil_code(self, client: TestClient,
                                               headers: dict[str, str]):
        r = client.post("/api/dsl-canvas/import-code",
                        json={"code": "import os\nos.system('echo pwned')\n"},
                        headers=headers)
        assert r.status_code == 422
        assert "import" in r.json()["detail"]

    def test_import_endpoint_requires_code_field(self, client: TestClient,
                                                 headers: dict[str, str]):
        r = client.post("/api/dsl-canvas/import-code", json={}, headers=headers)
        assert r.status_code == 422

    def test_export_endpoint_accepts_dsl_text_and_custom_filename(
        self, client: TestClient, headers: dict[str, str]
    ):
        doc = _doc([_lit("hi"), _out(fmt="text")], [{"from": "src", "to": "out"}])
        r = client.post("/api/dsl-canvas/export-code",
                        json={"dsl": json.dumps(doc, ensure_ascii=False),
                              "filename": "my_flow.py"},
                        headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["filename"] == "my_flow.py"

    def test_endpoints_require_authentication(self, client: TestClient):
        doc = _doc([_lit(1), _out()], [{"from": "src", "to": "out"}])
        assert client.post("/api/dsl-canvas/export-code",
                           json={"dsl": doc}).status_code == 401
        assert client.post("/api/dsl-canvas/import-code",
                           json={"code": "Flow(version='1')\n"}).status_code == 401

    def test_validate_endpoint_rejects_non_whitelisted_verb(self, client: TestClient,
                                                             headers: dict[str, str]):
        doc = _doc([{"id": "t", "type": "transform", "verb": "shell"}, _out()],
                   [{"from": "t", "to": "out"}])
        r = client.post("/api/dsl-canvas/validate", json={"dsl": doc}, headers=headers)
        assert r.status_code == 422

    def test_run_endpoint_executes_canvas_using_new_verbs(self, client: TestClient,
                                                           headers: dict[str, str]):
        doc = _doc(
            [_lit([{"score": 90}, {"score": 40}]),
             _xf("hi", "filter", field="score", op="gt", value=60),
             _xf("art", "artifact", name="top"),
             _out()],
            [{"from": "src", "to": "hi"}, {"from": "hi", "to": "art"},
             {"from": "art", "to": "out"}])
        r = client.post("/api/dsl-canvas/runs", json={"dsl": doc}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "succeeded"
        assert body["output"] == {"artifact": "top", "kind": "generic",
                                  "content": [{"score": 90}]}
        verbs = {log["verb"] for log in body["logs"] if log["verb"]}
        assert verbs == {"filter", "artifact"}

    def test_run_endpoint_returns_202_suspended_for_confirm(
        self, client: TestClient, headers: dict[str, str]
    ):
        """confirm 走到即挂起 → 202 + 挂起载荷，**不是 500、也不是 failed**。"""
        doc = _doc([_lit({"amount": 100}),
                    _xf("cf", "confirm", prompt="确认发布？", role="owner"),
                    _out()],
                   [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}])
        r = client.post("/api/dsl-canvas/runs",
                        json={"dsl": doc, "execution_id": "exec-9"},
                        headers=headers)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "suspended"
        susp = body["suspended"]
        assert susp["checkpoint"] == "exec-9#cf"
        assert susp["node_id"] == "cf"
        assert susp["context"]["prompt"] == "确认发布？"
        assert [o["value"] for o in susp["options"]] == ["approve", "reject"]
        assert susp["dsl_digest"] == dsl_digest(doc)

    def test_run_endpoint_rejects_non_string_execution_id(self, client: TestClient,
                                                            headers: dict[str, str]):
        doc = _doc([_lit(1), _out()], [{"from": "src", "to": "out"}])
        r = client.post("/api/dsl-canvas/runs",
                        json={"dsl": doc, "execution_id": 123}, headers=headers)
        assert r.status_code == 422
        assert "execution_id" in r.json()["detail"]


# =========================================================================== #
# 6. 回归护栏：既有枚举没被顺手改掉
# =========================================================================== #
class TestExistingEnumsUnchanged:
    def test_legacy_enums_keep_their_values(self):
        from find_yourself.services.dsl_canvas import (
            CONDITION_OPS, FILTER_OPS, INPUT_KINDS, MAP_OPS)
        assert MAP_OPS == ("set", "upper", "lower")
        assert FILTER_OPS == ("eq", "ne", "gt", "lt", "contains")
        assert CONDITION_OPS == FILTER_OPS
        assert INPUT_KINDS == ("literal", "text_lines")
        assert OUTPUT_FORMATS == ("json", "text")

    def test_legacy_verbs_are_still_first_in_whitelist(self):
        """前三个动词保持原顺序（前端/文档的稳定性）。"""
        assert TRANSFORM_VERBS[:3] == ("map", "filter", "template")

    def test_map_contract_still_accepts_legacy_params(self):
        doc = _doc([_lit([{"a": 1}]),
                    _xf("m", "map", op="upper"), _out()],
                   [{"from": "src", "to": "m"}, {"from": "m", "to": "out"}])
        assert run_dsl(doc).status == "succeeded"