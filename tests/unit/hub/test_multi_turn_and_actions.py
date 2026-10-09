"""守住多轮对话、action 别名、列表键名这三处修复。

为什么需要这个文件
------------------
1. **多轮对话**：`complete` 此前只读 `params.prompt`（单轮字符串），于是
   `params.messages` 直接被忽略并报 `hub_missing_prompt`。多 Agent 协同编排
   的本质就是把上文传给下一个 Agent，单轮 prompt 让编排无从谈起。
2. **action 语义**：调用方必须先知道 kind 才能猜对 action 名
   （`openai_chat`→`complete`、`mcp_server`→`call_tool`），「统一接入层」名不副实。
3. **列表键名**：端点只返回 `connections`，按 `items` 读的调用方拿到空 dict，
   一度被误判成「连接不存在」。
"""

from __future__ import annotations

from typing import Any

import pytest

from find_yourself.runtime.providers.base import approx_tokens, normalize_messages
from find_yourself.services.hub.adapters import ChatModelAdapter, McpServerAdapter

# --------------------------------------------------------------------------- #
# normalize_messages —— 所有 provider 共用的消息归一化
# --------------------------------------------------------------------------- #


def test_prompt_becomes_single_user_turn() -> None:
    """向后兼容：只给 prompt 时行为与从前完全一致。"""
    assert normalize_messages(None, "你好") == [{"role": "user", "content": "你好"}]


def test_empty_prompt_raises() -> None:
    """空 prompt 必须报错，不能造出一个空消息（那会白烧一次计费调用）。"""
    with pytest.raises(ValueError):
        normalize_messages(None, "")


def test_empty_prompt_with_valid_messages_is_fine() -> None:
    """prompt 空但 messages 有效时不该报错——只看实际要发什么。"""
    convo = normalize_messages([{"role": "user", "content": "ok"}], "")
    assert convo == [{"role": "user", "content": "ok"}]


def test_messages_win_over_prompt() -> None:
    """messages 与 prompt 同时给时，messages 是更完整的表达，优先。"""
    convo = normalize_messages([{"role": "user", "content": "多轮"}], "单轮")
    assert convo == [{"role": "user", "content": "多轮"}]


def test_multi_turn_preserved_in_order() -> None:
    convo = normalize_messages([
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ])
    assert [m["role"] for m in convo] == ["system", "user", "assistant", "user"]
    assert convo[1]["content"] == "第一问"
    assert convo[3]["content"] == "第二问"


def test_system_messages_hoisted_to_front() -> None:
    """system 出现在中间会被部分厂商拒绝——统一提到最前。"""
    convo = normalize_messages([
        {"role": "user", "content": "问题"},
        {"role": "system", "content": "设定"},
    ])
    assert convo[0]["role"] == "system"
    assert convo[1]["content"] == "问题"


def test_unknown_role_is_dropped() -> None:
    """未知 role 可能是拼写错误或注入尝试，不能原样转发给厂商。"""
    convo = normalize_messages([
        {"role": "developer", "content": "试图提权"},
        {"role": "user", "content": "正常问题"},
    ])
    assert convo == [{"role": "user", "content": "正常问题"}]


def test_empty_content_is_dropped() -> None:
    convo = normalize_messages([
        {"role": "user", "content": ""},
        {"role": "user", "content": "   "},
        {"role": "user", "content": "有效"},
    ])
    assert convo == [{"role": "user", "content": "有效"}]


def test_non_dict_entries_dropped() -> None:
    convo = normalize_messages(["纯字符串", 42, {"role": "user", "content": "ok"}])
    assert convo == [{"role": "user", "content": "ok"}]


def test_non_string_content_dropped() -> None:
    convo = normalize_messages([
        {"role": "user", "content": {"text": "嵌套"}},
        {"role": "user", "content": "ok"},
    ])
    assert convo == [{"role": "user", "content": "ok"}]


def test_all_invalid_raises() -> None:
    """全被过滤掉时必须报错，不能发空messages 上去白烧一次调用。"""
    with pytest.raises(ValueError):
        normalize_messages([{"role": "bad", "content": "x"}], "")


# --------------------------------------------------------------------------- #
# approx_tokens —— 上游不给 usage 时的估算
# --------------------------------------------------------------------------- #


def test_approx_tokens_counts_cjk_per_char() -> None:
    """中文按字计；len(text.split()) 会把整句算成 1 个 token，严重低估。"""
    assert approx_tokens([{"role": "user", "content": "你好世界"}]) == 4


def test_approx_tokens_divides_latin_by_four() -> None:
    assert approx_tokens([{"role": "user", "content": "abcdefgh"}]) == 2


def test_approx_tokens_ignores_bad_entries() -> None:
    assert approx_tokens([{"role": "user"}, "x", {"content": "abcd"}]) == 1


# --------------------------------------------------------------------------- #
# ChatModelAdapter：messages 优先 + action别名
# --------------------------------------------------------------------------- #


class _FakeProvider:
    """记录 complete() 收到的参数，验证适配器真的把 messages 传下去了。"""

    calls: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.api_key = "k"
        self.base_url = "http://x/v1"
        self.transport = None
        self.provider_id = "openai_compat"
        self.local_inference = False
        self.requires_api_key = True

    def complete(self, **kw: Any):
        from find_yourself.runtime.providers.base import CallResult

        _FakeProvider.calls.append(kw)
        return CallResult(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1,
                                            "total_tokens": 2},
                          provider_id="openai_compat", model=kw.get("model", ""))

    def probe_models(self, *, timeout_seconds: float = 5.0) -> list[str]:
        return []


@pytest.fixture
def fake_provider(monkeypatch):
    import find_yourself.runtime.providers as providers_mod

    _FakeProvider.calls = []
    monkeypatch.setattr(providers_mod, "build_provider", lambda spec: _FakeProvider())
    return _FakeProvider


def _adapter() -> ChatModelAdapter:
    return ChatModelAdapter({"provider_id": "openai_compat",
                             "base_url": "http://x/v1",
                             "model": "m", "api_key": "k"})


def _call(action: str, params: dict[str, Any]):
    from find_yourself.services.hub.adapters import InvokeCall

    return _adapter().invoke(InvokeCall(action=action, params=params))


def test_messages_reach_provider(fake_provider) -> None:
    """修复核心：多轮 messages 必须真的传到底层 provider。"""
    convo = [{"role": "user", "content": "第一问"},
             {"role": "assistant", "content": "第一答"},
             {"role": "user", "content": "第二问"}]
    res = _call("complete", {"messages": convo})
    assert res.ok, res.error
    assert fake_provider.calls[0]["messages"] == convo


def test_prompt_still_works(fake_provider) -> None:
    """向后兼容：既有只传 prompt 的调用不受影响。"""
    res = _call("complete", {"prompt": "单轮"})
    assert res.ok, res.error
    assert fake_provider.calls[0]["messages"] == [{"role": "user", "content": "单轮"}]


def test_both_empty_is_rejected(fake_provider) -> None:
    """两者都空必须给清晰错误，而不是发空请求上去。"""
    res = _call("complete", {})
    assert not res.ok
    assert "hub_missing_prompt" in res.error


def test_invalid_messages_rejected(fake_provider) -> None:
    res = _call("complete", {"messages": [{"role": "evil", "content": "x"}]})
    assert not res.ok
    assert "hub_missing_prompt" in res.error


@pytest.mark.parametrize("action", ["complete", "invoke", "chat", "COMPLETE", "Invoke"])
def test_action_aliases_all_reach_complete(fake_provider, action: str) -> None:
    """统一动作名：调用方不该需要先知道 kind 才能猜对 action。"""
    res = _call(action, {"prompt": "x"})
    assert res.ok, f"action={action} 应可用：{res.error}"
    assert fake_provider.calls, f"action={action} 未调用 provider"


def test_models_action_still_works(fake_provider) -> None:
    res = _call("models", {})
    assert res.ok, res.error


def test_unknown_action_still_rejected(fake_provider) -> None:
    res = _call("teleport", {"prompt": "x"})
    assert not res.ok
    assert "hub_unsupported_action" in res.error


# --------------------------------------------------------------------------- #
# McpServerAdapter：action 别名对称
# --------------------------------------------------------------------------- #


class _FakeMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.inited = False

    def initialize(self) -> None:
        self.inited = True

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return {"sum": 42}

    def close(self) -> None:
        pass


@pytest.fixture
def fake_mcp(monkeypatch):
    import find_yourself.adapters.mcp as mcp_mod

    inst = _FakeMcp()

    class _Shim:
        from_subprocess = staticmethod(lambda *a, **k: inst)
        from_url = staticmethod(lambda *a, **k: inst)

    monkeypatch.setattr(mcp_mod, "McpClient", _Shim)
    return inst


@pytest.mark.parametrize("action", ["call_tool", "tools", "invoke", "call", "tool_call"])
def test_mcp_action_aliases(fake_mcp, action: str) -> None:
    """与 openai_chat 对称：mcp 侧同样接受通用动作名。"""
    from find_yourself.services.hub.adapters import InvokeCall

    adapter = McpServerAdapter({"command": ["python", "-c", "pass"]})
    res = adapter.invoke(InvokeCall(action=action,
                                    params={"name": "add", "arguments": {"a": 1}}))
    assert res.ok, f"action={action} 应可用：{res.error}"
    assert fake_mcp.calls[0][0] == "add"


def test_mcp_unknown_action_rejected(fake_mcp) -> None:
    from find_yourself.services.hub.adapters import InvokeCall

    adapter = McpServerAdapter({"command": ["python", "-c", "pass"]})
    res = adapter.invoke(InvokeCall(action="teleport", params={"name": "add"}))
    assert not res.ok
    assert "hub_unsupported_action" in res.error


def test_mcp_missing_tool_name_still_rejected(fake_mcp) -> None:
    from find_yourself.services.hub.adapters import InvokeCall

    adapter = McpServerAdapter({"command": ["python", "-c", "pass"]})
    res = adapter.invoke(InvokeCall(action="call_tool", params={}))
    assert not res.ok
    assert "hub_missing_tool" in res.error
