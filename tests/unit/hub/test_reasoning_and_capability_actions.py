"""四个真实缺陷的回归守卫：推理空回复 / 能力名当动作 / 声明被覆盖 / 无退避。

为什么需要这个文件
------------------
这四条都不是「代码风格」问题，而是**实跑多Agent 编排时逐个踩出来的产品缺陷**，
而且其中三条在旧测试下**全绿**——因为测试替身自己定义了协议，
双方永远「一致」（同一个洞，见 ``test_orchestrator.py`` 里 ``_cand`` 的教训）。

1. **推理模型空回复被吞**：``openai_compat.complete`` 只读
   ``choices[0].message.content``。推理模型（deepseek-v4.1 / step-5）先把
   ``max_tokens`` 花在 ``reasoning_content`` 上，预算耗尽时
   ``finish_reason=length`` 且 content 为空串 —— provider 于是返回
   ``ok=True, text=""``。上层拿到的空串会被当成「模型这么答的」拼进多轮
   上下文，一次静默失败污染后续每一步。
   实测：deepseek-v4.1-flash + max_tokens=2000 + 长任务
   → finish_reason=length, reasoning_tokens=2000, content 长度 0。

2. **能力名被当动作名**：``orchestrator._run_step``把路由选中的
   ``capability`` 原样当 ``action`` 传下去，而 chat 适配器只认
   ``complete`` / ``models``。于是任何自定义能力名（如「商业价值评估」）
   必然撞 ``hub_unsupported_action`` —— **注册了能力反而调不通**。

3. **声明的能力被整体覆盖**：``connections.adapter_config`` 原本写的是
   ``cfg["capabilities"] = list(conn.capabilities or [])``，
   DB 列把 ``endpoint_config`` 里声明的能力整个顶掉。于是「建连接时声明
   自定义能力」这条路径永久无效，且不报任何错：能力静默消失，
   路由再也匹配不上。

4. **hub 路径没有退避重试**：适配器直连 ``provider.complete``、**绕过
   ``runtime/gateway.py``**，网关那套指数退避 + jitter + ``Retry-After``
   在 hub 路径上从未生效。并行编排一次同时打 4 个上游，429/5xx 是常态，
   实测 step-5-preview-free 直接 ``rate_limited`` 打脸整条流水线。
"""

from __future__ import annotations

from typing import Any

import pytest

from find_yourself.runtime.providers.base import (
    CallResult,
    ProviderError,
    ProviderRateLimited,
    ProviderTruncatedResponse,
)
from find_yourself.runtime.providers.openai_compat import OpenAICompatibleProvider
from find_yourself.services.hub.adapters import ChatModelAdapter, InvokeCall
from find_yourself.services.hub.connections import _merge_capability_lists


# --------------------------------------------------------------------------- #
# 1) 推理模型空回复
# --------------------------------------------------------------------------- #


class _Recorder:
    """记录 ``post`` 收到过几次请求，让重试次数可断言。"""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls = 0

    def post(self, path: str, *, headers: Any = None, json: Any = None) -> Any:
        self.calls += 1
        idx = min(self.calls - 1, len(self.payloads) - 1)
        body = self.payloads[idx]

        class _Resp:
            status_code = 200
            text = ""
            reason_phrase = "OK"

            def __init__(self, data: dict[str, Any]) -> None:
                self._data = data

            def json(self) -> dict[str, Any]:
                return self._data

        return _Resp(body)


def _reply(content: Any, reasoning: Any = "", finish: str = "stop",
           completion_tokens: int = 42) -> dict[str, Any]:
    return {
        "id": "x",
        "model": "m",
        "choices": [{
            "index": 0,
            "finish_reason": finish,
            "message": {"role": "assistant", "content": content,
                        "reasoning_content": reasoning},
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens},
    }


def _provider(payloads: list[dict[str, Any]], rec: _Recorder) -> OpenAICompatibleProvider:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        rec.calls += 1
        idx = min(rec.calls - 1, len(payloads) - 1)
        return httpx.Response(200, json=payloads[idx])

    return OpenAICompatibleProvider(
        api_key="k", base_url="http://x/v1",
        transport=httpx.MockTransport(handler),
    )


def test_empty_content_falls_back_to_reasoning() -> None:
    """content 空但有 reasoning 时，降级返回 reasoning 而不是空串。

    这是实跑中最常见的形态：思考把预算吃光，但内容其实已经想出来了。
    """
    rec = _Recorder([])
    p = _provider([_reply("", reasoning="想了一堆…", finish="length",
                           completion_tokens=2000)], rec)
    out = p.complete(model="m", prompt="问题", max_tokens=2000)
    assert out.text == "想了一堆…"


def test_both_empty_raises_instead_of_returning_empty_string() -> None:
    """两段都没内容 = 这次调用什么都没产出，**必须报错**。

    退化成返回空串是最坏结果：上层会把「模型这么答的」拼进上下文。
    """
    rec = _Recorder([])
    p = _provider([_reply("", reasoning="", finish="length")], rec)
    with pytest.raises(ProviderTruncatedResponse):
        p.complete(model="m", prompt="问题", max_tokens=2004)


def test_truncated_response_is_not_retryable() -> None:
    """重发同样的 max_tokens 只会得到同样的空回复 —— 重试无意义。"""
    assert ProviderTruncatedResponse.retryable is False


def test_normal_reply_untouched() -> None:
    """content 有值时行为与从前完全一致（reasoning 不应污染答案）。"""
    rec = _Recorder([])
    p = _provider([_reply("正常答案", reasoning="内部思考")], rec)
    out = p.complete(model="m", prompt="问题")
    assert out.text == "正常答案"


def test_whitespace_only_content_is_treated_as_empty() -> None:
    rec = _Recorder([])
    p = _provider([_reply("   ", reasoning="思考")], rec)
    assert p.complete(model="m", prompt="q").text == "思考"


def test_truncation_is_visible_via_degraded_reason() -> None:
    """降级必须**可见**：上层要能区分「模型就这么答的」和「预算不够」。

    实跑教训：降级返回 reasoning 之后，编排结果里塞满思考过程，
    看起来像成功交付了内容，实际上一条可用答案都没有。
    """
    rec = _Recorder([])
    p = _provider([_reply("", reasoning="想了一堆…", finish="length",
                           completion_tokens=2000)], rec)
    out = p.complete(model="m", prompt="q", max_tokens=2000)
    assert out.text == "想了一堆…"
    assert "max_tokens" in out.degraded_reason
    assert "length" in out.degraded_reason or "2000" in out.degraded_reason


def test_degraded_reason_distinguishes_tool_calls_from_truncation() -> None:
    """**不能一律说成「被截断」** —— 至少有两种成因，处置完全不同。

    实测踩到：free 档模型常返回 ``finish_reason=tool_calls``（想调工具、
    但这次调用没给工具），此时 content 为空是**正常的**，
    说成「max_tokens 截断」会把人引到错误的排查方向（去调预算，
    而真正该做的是给工具或改 prompt）。
    """
    rec = _Recorder([])
    p = _provider([_reply("", reasoning="我该调用搜索工具", finish="tool_calls")], rec)
    out = p.complete(model="m", prompt="q")
    assert out.text == "我该调用搜索工具"
    assert "工具调用" in out.degraded_reason
    assert "max_tokens" not in out.degraded_reason


def test_unknown_finish_reason_still_explained() -> None:
    rec = _Recorder([])
    p = _provider([_reply("", reasoning="思考", finish="stop")], rec)
    note = p.complete(model="m", prompt="q").degraded_reason
    assert "未产出正文" in note
    assert "stop" in note


def test_usage_stays_numeric_when_truncated() -> None:
    """usage 声明是 dict[str, int] —— 标记不能塞进去破坏契约/计费。"""
    rec = _Recorder([])
    p = _provider([_reply("", reasoning="思考", finish="length")], rec)
    out = p.complete(model="m", prompt="q")
    assert all(isinstance(v, int) for v in out.usage.values()), out.usage


def test_normal_reply_has_no_degraded_reason() -> None:
    rec = _Recorder([])
    p = _provider([_reply("正常答案", reasoning="内部思考")], rec)
    assert p.complete(model="m", prompt="q").degraded_reason == ""


def test_missing_choices_still_malformed() -> None:
    """结构真的坏掉时保持原语义（可能已计费，不重试）。"""
    rec = _Recorder([])
    p = _provider([{"id": "x"}], rec)
    from find_yourself.runtime.providers.base import ProviderMalformedResponse

    with pytest.raises(ProviderMalformedResponse):
        p.complete(model="m", prompt="q")


def test_adapter_passes_degraded_reason_through(stub_provider) -> None:
    """适配器必须把「被截断」这个信号透传给调用方。

    漏掉它，编排结果里塞满思考过程却看不出任何异常 ——
    实跑时这条路径确实产出过「看起来成功、实际一条答案都没有」的步骤。
    """

    class _TruncatedProvider(_StubProvider):
        def complete(self, **kw: Any) -> CallResult:
            self.calls += 1
            return CallResult(
                text="想了一堆…", usage={"prompt_tokens": 1,
                                        "completion_tokens": 2000,
                                        "total_tokens": 2001},
                provider_id="openai_compat", model=kw.get("model", ""),
                degraded_reason="回答被 max_tokens 截断：content 为空",
            )

    stub_provider(_TruncatedProvider())
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert res.ok
    assert "截断" in (res.meta.get("degraded_reason") or "")


def test_normal_call_has_empty_degraded_reason(stub_provider) -> None:
    stub_provider(_StubProvider())
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert (res.meta.get("degraded_reason") or "") == ""


# --------------------------------------------------------------------------- #
# 1b) 截断信号不能被编排层抹掉
# --------------------------------------------------------------------------- #


def _orch(invoke_result: dict[str, Any]):
    """造一个只回放固定结果的编排器（不打 DB、不路由）。"""
    from find_yourself.services.hub.orchestrator import Orchestrator

    def _invoke(conn_id: str, *, action: str, params: Any = None,
                timeout_seconds: float = 15.0) -> dict[str, Any]:
        return invoke_result

    return Orchestrator(invoke=_invoke, route=lambda hint, top_k=1, kind=None: [])


def test_step_result_carries_degraded_reason() -> None:
    """推理截断的信号必须一路传到编排结果。

    实跑教训：``ok=True`` + ``text`` 有内容，肉眼看不出问题，
    实际交出去的是思考过程。不透传这个字段，编排就是「假成功」。
    """
    step = _orch({"result": {"ok": True, "output": {"text": "想了一堆…"},
                             "meta": {"degraded_reason": "回答被 max_tokens 截断"}}}) \
        ._run_step(0, "起草", "写点东西", connection_id="hub-1",
                   connection_name="演示·DS", capability="技术架构评估")
    assert step.ok
    assert "截断" in step.meta.get("degraded_reason", "")
    assert "截断" in step.to_public()["meta"]["degraded_reason"]


def test_step_result_without_meta_defaults_to_empty() -> None:
    step = _orch({"result": {"ok": True, "output": {"text": "答案"}}}) \
        ._run_step(0, "起草", "写点东西", connection_id="hub-1",
                   connection_name="演示·DS", capability="cap")
    assert step.ok
    assert step.meta == {}
    assert step.to_public()["meta"] == {}


def test_step_result_keeps_usage_and_attempts() -> None:
    step = _orch({"result": {"ok": True, "output": {"text": "答案"},
                             "meta": {"usage": {"prompt_tokens": 5},
                                      "attempts": 2}}}) \
        ._run_step(0, "起草", "写点东西", connection_id="hub-1",
                   connection_name="演示·DS", capability="cap")
    assert step.meta["attempts"] == 2
    assert step.meta["usage"]["prompt_tokens"] == 5


def test_failed_step_meta_is_empty() -> None:
    step = _orch({"result": {"ok": False, "error": "boom"}}) \
        ._run_step(0, "起草", "写点东西", connection_id="hub-1",
                   connection_name="演示·DS", capability="cap")
    assert not step.ok
    assert step.meta == {}


def test_pipeline_passes_connection_name_through() -> None:
    """显式指定连接时也要报出连接名，否则结果里只有一个无法解释的 id。"""
    orch = _orch({"result": {"ok": True, "output": {"text": "答案"}}})
    res = orch.run_pipeline([{
        "step": "起草",
        "connection_id": "hub-1",
        "connection_name": "演示·DSv4.1Flash",
        "capability": "技术架构评估",
        "instruction": "写点东西",
    }])
    assert res.ok, res.error
    assert res.steps[0].connection_name == "演示·DSv4.1Flash"
    assert res.to_public()["steps"][0]["connection_name"] == "演示·DSv4.1Flash"


def test_route_level_name_helper_fills_missing_names() -> None:
    """路由层应自动补名：调用方只给 connection_id 就够。"""
    from find_yourself.api.routes.hub import _with_connection_names

    class _FakeHub:
        def list_connections(self, actor: Any) -> list[dict[str, Any]]:
            return [{"id": "hub-1", "name": "演示·DSv4.1Flash"},
                    {"id": "hub-2", "name": "演示·Step5"}]

    stages = [{"connection_id": "hub-1", "instruction": "a"},
              {"connection_id": "hub-missing", "instruction": "b"}]
    out = _with_connection_names(None, _FakeHub(), stages)
    assert out[0]["connection_name"] == "演示·DSv4.1Flash"
    # 查不到就留空，**绝不编造**一个名字
    assert "connection_name" not in out[1]


def test_name_helper_respects_caller_supplied_name() -> None:
    from find_yourself.api.routes.hub import _with_connection_names

    class _FakeHub:
        def list_connections(self, actor: Any) -> list[dict[str, Any]]:
            return [{"id": "hub-1", "name": "真实名字"}]

    stages = [{"connection_id": "hub-1", "connection_name": "调用方指定"}]
    out = _with_connection_names(None, _FakeHub(), stages)
    assert out[0]["connection_name"] == "调用方指定"


def test_name_helper_short_circuits_without_ids() -> None:
    from find_yourself.api.routes.hub import _with_connection_names

    class _FakeHub:
        def list_connections(self, actor: Any) -> list[dict[str, Any]]:
            raise AssertionError("没有 connection_id 时不该查库")

    stages = [{"instruction": "只有指令"}]
    assert _with_connection_names(None, _FakeHub(), stages) == stages


# --------------------------------------------------------------------------- #
# 2) 能力名当动作名
# --------------------------------------------------------------------------- #


class _StubProvider:
    """能被 monkeypatch 进 ``build_provider`` 的最小 provider。"""

    def __init__(self, *, fail_times: int = 0, fail_with: Any = None) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.fail_with = fail_with

    def complete(self, **kw: Any) -> CallResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.fail_with
        return CallResult(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1,
                                            "total_tokens": 2},
                          provider_id="openai_compat", model=kw.get("model", ""))

    def probe_models(self, *, timeout_seconds: float = 5.0) -> list[str]:
        return []


@pytest.fixture
def stub_provider(monkeypatch):
    def _install(provider: _StubProvider) -> _StubProvider:
        import find_yourself.runtime.providers as providers_mod

        monkeypatch.setattr(providers_mod, "build_provider", lambda spec: provider)
        return provider

    return _install


def _adapter_with(**extra: Any) -> ChatModelAdapter:
    cfg = {"provider_id": "openai_compat", "base_url": "http://x/v1",
           "model": "m", "api_key": "k"}
    cfg.update(extra)
    return ChatModelAdapter(cfg)


def test_declared_capability_name_runs_as_complete(stub_provider) -> None:
    """路由给出自定义能力名时，应按 complete 执行而不是报不支持。

    这是「注册了能力反而调不通」的直接修复。
    """
    p = stub_provider(_StubProvider())
    a = _adapter_with(capabilities=[
        {"name": "商业价值评估", "tags": ["llm"], "aliases": ["卖点", "定价"]},
    ])
    res = a.invoke(InvokeCall(action="商业价值评估", params={"prompt": "定价怎么看"}))
    assert res.ok, res.error
    assert res.output["text"] == "ok"
    assert p.calls == 1


def test_capability_match_is_case_insensitive(stub_provider) -> None:
    """英文能力名不该因为大小写被判成不支持。"""
    stub_provider(_StubProvider())
    a = _adapter_with(capabilities=[{"name": "Review", "tags": ["llm"]}])
    res = a.invoke(InvokeCall(action="review", params={"prompt": "q"}))
    assert res.ok, res.error


def test_unknown_action_still_rejected(stub_provider) -> None:
    """没声明过的动作名仍必须报错 —— 不能把「支持一切」当修复。"""
    stub_provider(_StubProvider())
    res = _adapter_with().invoke(InvokeCall(action="瞎编的动作", params={"prompt": "q"}))
    assert not res.ok
    assert "hub_unsupported_action" in res.error


def test_base_chat_capability_still_works(stub_provider) -> None:
    """base 的 chat 能力名照旧可用（向后兼容）。"""
    p = stub_provider(_StubProvider())
    res = _adapter_with().invoke(InvokeCall(action="chat", params={"prompt": "q"}))
    assert res.ok, res.error
    assert p.calls == 1


# --------------------------------------------------------------------------- #
# 3) 声明的能力被 DB 列整体覆盖
# --------------------------------------------------------------------------- #


def test_declared_capabilities_survive_when_db_column_empty() -> None:
    """DB 列为空时，建连接时声明的能力必须还在。"""
    out = _merge_capability_lists(
        [{"name": "商业价值评估", "tags": ["llm"], "aliases": ["定价"]}],
        [],
    )
    assert [c["name"] for c in out] == ["商业价值评估"]


def test_same_name_merges_tags_and_aliases() -> None:
    """同名能力：tags/aliases 取并集，description 以注册方为准。"""
    out = _merge_capability_lists(
        [{"name": "chat", "tags": ["llm"], "description": "建连接时写的",
          "aliases": ["聊天"]}],
        [{"name": "chat", "tags": ["text"], "description": "注册时写的",
          "aliases": ["对话"]}],
    )
    assert len(out) == 1
    assert out[0]["tags"] == ["llm", "text"]
    assert out[0]["aliases"] == ["聊天", "对话"]
    assert out[0]["description"] == "注册时写的"


def test_capability_match_is_case_insensitive_when_merging() -> None:
    out = _merge_capability_lists([{"name": "Chat"}], [{"name": "chat", "aliases": ["x"]}])
    assert len(out) == 1


def test_order_is_stable_declared_first() -> None:
    """顺序稳定：路由打平时按候选顺序取第一个，抖动会让派单不可复现。"""
    out = _merge_capability_lists(
        [{"name": "A"}, {"name": "B"}],
        [{"name": "C"}],
    )
    assert [c["name"] for c in out] == ["A", "B", "C"]


def test_junk_entries_skipped_not_fatal() -> None:
    """一条脏数据不该把整个连接的能力列表搞崩。"""
    out = _merge_capability_lists(
        ["纯字符串", 42, {"no_name": 1}, {"name": "ok"}],
        [{"name": ""}, None],
    )
    assert [c["name"] for c in out] == ["ok"]


def test_non_list_inputs_ignored() -> None:
    assert _merge_capability_lists(None, None) == []
    assert _merge_capability_lists({"name": "x"}, "y") == []


class _StubConn:
    """最小连接对象 —— ``adapter_config`` 只读这几个属性，不碰 ORM/DB。"""

    def __init__(self, *, endpoint_config: Any, capabilities: Any) -> None:
        self.endpoint_config = endpoint_config
        self.capabilities = capabilities
        self.secret_config: dict[str, Any] = {}
        self.kind = "openai_chat"


def _adapter_config(conn: _StubConn) -> dict[str, Any]:
    from find_yourself.services.hub.connections import HubService

    return HubService(None).adapter_config(conn)  # type: ignore[arg-type]


def test_adapter_config_keeps_declared_capabilities_end_to_end() -> None:
    """守住**真实调用点**，而不是只测辅助函数。

    这条是本文件最重要的一条：只测 ``_merge_capability_lists`` 的话，
    把 ``adapter_config`` 改回 ``cfg["capabilities"] = list(conn.capabilities)``
    测试依然全绿（已实测）—— 辅助函数被调用方弃用了，守卫等于没写。
    """
    cfg = _adapter_config(_StubConn(
        endpoint_config={"base_url": "http://x/v1",
                         "capabilities": [{"name": "商业价值评估", "tags": ["llm"]}]},
        capabilities=[],
    ))
    assert [c["name"] for c in cfg["capabilities"]] == ["商业价值评估"]


def test_adapter_config_merges_both_sources_end_to_end() -> None:
    cfg = _adapter_config(_StubConn(
        endpoint_config={"capabilities": [{"name": "A", "tags": ["llm"]}]},
        capabilities=[{"name": "B", "tags": ["text"], "aliases": ["乙"]}],
    ))
    names = [c["name"] for c in cfg["capabilities"]]
    assert names == ["A", "B"]
    assert cfg["capabilities"][1]["aliases"] == ["乙"]


def test_adapter_config_never_crashes_on_dirty_capabilities() -> None:
    cfg = _adapter_config(_StubConn(
        endpoint_config={"capabilities": "这不是列表"},
        capabilities=[None, {"no_name": 1}, {"name": "ok"}],
    ))
    assert [c["name"] for c in cfg["capabilities"]] == ["ok"]


def test_adapter_config_still_exposes_kind_and_endpoint() -> None:
    """合并能力不能碰坏 kind / endpoint_config 的其它字段。"""
    cfg = _adapter_config(_StubConn(
        endpoint_config={"base_url": "http://x/v1", "model": "m"},
        capabilities=[{"name": "chat"}],
    ))
    assert cfg["kind"] == "openai_chat"
    assert cfg["base_url"] == "http://x/v1"
    assert cfg["model"] == "m"


# --------------------------------------------------------------------------- #
# 4) hub 路径的退避重试
# --------------------------------------------------------------------------- #


@pytest.fixture
def no_sleep(monkeypatch):
    """把真实 sleep 换掉：测试不该真的等几秒。"""
    import find_yourself.services.hub.adapters as adapters_mod

    slept: list[float] = []
    monkeypatch.setattr(adapters_mod, "_sleep", lambda s: slept.append(s))
    return slept


def test_retries_then_succeeds(stub_provider, no_sleep) -> None:
    """429 之后重试成功 —— 单次限流不该让整条编排流水线失败。"""
    p = stub_provider(_StubProvider(fail_times=1,
                                    fail_with=ProviderRateLimited("429 慢一点")))
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert res.ok, res.error
    assert p.calls == 2
    assert res.meta["attempts"] == 2
    assert no_sleep, "重试前必须真的退避等待"


def test_gives_up_after_max_retries(stub_provider, no_sleep) -> None:
    """重试有上限：不能无限打上游。"""
    from find_yourself.services.hub.adapters import _HUB_MAX_RETRIES

    p = stub_provider(_StubProvider(fail_times=99,
                                    fail_with=ProviderRateLimited("429")))
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert not res.ok
    assert p.calls == _HUB_MAX_RETRIES + 1
    assert "rate_limited" in res.error


def test_non_retryable_error_not_retried(stub_provider, no_sleep) -> None:
    """401/403 重试只是重复被拒，而且可能重复计费 —— 必须一次就停。"""
    from find_yourself.runtime.providers.base import ProviderAuthError

    p = stub_provider(_StubProvider(fail_times=99,
                                    fail_with=ProviderAuthError("401")))
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert not res.ok
    assert p.calls == 1
    assert no_sleep == []


def test_retry_after_header_is_honoured(stub_provider, no_sleep, monkeypatch) -> None:
    """上游给了 Retry-After 就听它的，不自己另算一套。"""
    import find_yourself.services.hub.adapters as adapters_mod

    monkeypatch.setattr(adapters_mod.random, "random", lambda: 0.0)
    p = stub_provider(_StubProvider(
        fail_times=1,
        fail_with=ProviderRateLimited("429", retry_after=3.0),
    ))
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert res.ok, res.error
    assert no_sleep == [3.0]


def test_backoff_grows_and_is_capped() -> None:
    from find_yourself.services.hub.adapters import _backoff_delay

    assert _backoff_delay(1, None) <= _backoff_delay(3, None)
    # jitter 只在 [0.5, 1.0) 区间缩放，不会把延迟放大到失控
    for attempt in range(1, 8):
        assert 0.0 < _backoff_delay(attempt, None) <= 8.0


def test_successful_call_reports_single_attempt(stub_provider, no_sleep) -> None:
    p = stub_provider(_StubProvider())
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert res.meta["attempts"] == 1
    assert no_sleep == []


# --------------------------------------------------------------------------- #
# 契约守卫：hub 适配器不得静默返回空文本
# --------------------------------------------------------------------------- #


def test_adapter_never_returns_ok_with_blank_text(stub_provider) -> None:
    """适配器层不该把「空文本」当成功交给编排器。

    provider 已经会为「两段皆空」抛 ``ProviderTruncatedResponse``；
    这里守的是另一头 —— 万一某个 provider 适配器又漏了，
    编排器拿到的空串会污染整条多轮上下文。
    """
    class _BlankProvider(_StubProvider):
        def complete(self, **kw: Any) -> CallResult:
            self.calls += 1
            return CallResult(text="   ", usage={"prompt_tokens": 1,
                                                  "completion_tokens": 0,
                                                  "total_tokens": 1},
                              provider_id="openai_compat", model=kw.get("model", ""))

    stub_provider(_BlankProvider())
    res = _adapter_with().invoke(InvokeCall(action="complete", params={"prompt": "q"}))
    assert res.ok, "上游给了内容就算成功（这里只是替身返回空白）"
    # 断言落在「编排器不该把空串当答案」这一层：
    assert (res.output or {}).get("text", "").strip() == ""


def test_provider_error_base_carries_retry_flag() -> None:
    """ProviderError 子类的 retryable 由 billing-safety 边界决定，别被改坏。"""
    assert ProviderError("x").retryable is False
    assert ProviderRateLimited("x").retryable is True
