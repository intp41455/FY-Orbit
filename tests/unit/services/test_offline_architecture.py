"""A-离线优先-01/03 · 默认离线架构单测（服务层 + 网关门）。

判据逐条对应：

1. **默认语义**：``FY_OFFLINE_MODE`` 缺省 True —— 不是「可切离线」，是「默认离线」；
2. **远程调用点明确降级**：配置里配出来的远程 provider 在离线模式下**不出网**，
   且报的是 ``offline_mode_remote_blocked``（含人话原因），不是含糊的「网络错误」；
   更不是把人支去配一个仍然用不上的 API key（那才是误导）；
3. **本地能力照常**：本机推理路由（ollama）与宿主显式注入的适配器不在门内；
   降级链照常走，且如实标 ``degraded_from`` / ``degraded_reason``；
4. **断网探测**：``probe_network`` 真的会因 DNS/TCP 失败而判不可达（可注入替身，
   测试不碰真网络）。
"""

from __future__ import annotations

import socket

import pytest

from find_yourself.config import Settings
from find_yourself.runtime.gateway import (
    LOCAL_INFERENCE_PROVIDERS,
    CallResult,
    MockModelProvider,
    ModelGateway,
    ModelNotConfigured,
    ProviderRoute,
)
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict
from find_yourself.services.offline import (
    OFFLINE_MODE_ENV,
    OfflineUnavailable,
    clear_cache,
    is_offline,
    offline_mode_enabled,
    probe_network,
    remote_block_reason,
    require_remote,
    status,
)

ACTOR = Actor.owner("owner-offline")


def _settings(**overrides) -> Settings:
    base = dict(environment="test", session_secret="x" * 40,
                database_url="sqlite://", public_url="http://x")
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例都从「干净环境」起：外部恰好导出的 FY_OFFLINE_MODE 不许串味。"""
    monkeypatch.delenv(OFFLINE_MODE_ENV, raising=False)
    clear_cache()
    yield
    clear_cache()


# --------------------------------------------------------------------------- #
# 判据 3 · 默认语义
# --------------------------------------------------------------------------- #

def test_offline_mode_defaults_to_true():
    assert _settings().offline_mode is True
    assert offline_mode_enabled(_settings()) is True
    assert is_offline(_settings()) is True


def test_explicit_env_can_open_the_network(monkeypatch):
    monkeypatch.setenv(OFFLINE_MODE_ENV, "0")
    s = Settings(environment="test", session_secret="x" * 40,
                 database_url="sqlite://", public_url="http://x")
    assert s.offline_mode is False
    assert is_offline(s) is False


def test_offline_status_snapshot_is_honest_about_not_probing():
    """默认离线时**不**声称探测过网络：没探测就说没探测。"""
    snap = status(_settings())
    assert snap.offline_mode is True
    assert snap.network_reachable is None
    assert OFFLINE_MODE_ENV in remote_block_reason("远程模型调用", _settings())


# --------------------------------------------------------------------------- #
# 断网探测（判据 2 的「物理断网」替身）
# --------------------------------------------------------------------------- #

def test_probe_reports_unreachable_when_dns_fails():
    def boom(_host):
        raise socket.gaierror("name resolution failed (simulated offline)")

    assert probe_network(_settings(model_base_url="https://api.example/v1"),
                         resolve=boom) is False


def test_probe_reports_reachable_when_dns_and_tcp_succeed():
    calls = []

    def fake_resolve(host):
        calls.append(("resolve", host))
        return "127.0.0.1"

    def fake_connect(host, port):
        calls.append(("connect", host, port))
        return object()

    assert probe_network(_settings(model_base_url="https://api.example:8443/v1"),
                         resolve=fake_resolve, connect=fake_connect) is True
    assert calls == [("resolve", "api.example"), ("connect", "api.example", 8443)]


def test_probe_without_base_url_is_unreachable():
    assert probe_network(_settings(), resolve=lambda h: "127.0.0.1") is False


# --------------------------------------------------------------------------- #
# 远程能力门
# --------------------------------------------------------------------------- #

def test_require_remote_raises_typed_degradation_when_offline():
    with pytest.raises(OfflineUnavailable) as info:
        require_remote("远程模型调用", _settings())
    exc = info.value
    assert exc.code == "offline_mode_remote_blocked"
    assert exc.http_status == 503
    # 是统一信封的一个 Conflict 子类，API 层能按 code 映射。
    assert isinstance(exc, Conflict)
    assert OFFLINE_MODE_ENV in exc.message
    # 明确说了「本地能力不受影响」与「怎么恢复联网」——这才是降级提示。
    assert "本地能力" in exc.message and "不受影响" in exc.message


def test_require_remote_is_a_noop_when_network_is_open():
    assert require_remote("远程模型调用", _settings(offline_mode=False)) is None


# --------------------------------------------------------------------------- #
# 判据 2 · 网关门：远程被拦 / 本地照常
# --------------------------------------------------------------------------- #

def test_settings_wired_remote_call_is_blocked_before_model_not_configured():
    """配了 key 也没用：默认离线就不出网，且**先报离线**而不是「未配置模型」。"""
    gw = ModelGateway(_settings(model_api_key="k", model_base_url="https://api.example/v1"))
    assert [r.provider_id for r in gw.routes] == ["openai_compat"]
    with pytest.raises(OfflineUnavailable) as info:
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hi")
    assert info.value.code == "offline_mode_remote_blocked"


def test_unconfigured_and_offline_still_reports_not_configured():
    """零配置首启（连 base_url 都没有）：报「未配置模型」，不把离线当唯一原因。"""
    gw = ModelGateway(_settings())
    assert gw.routes == []
    with pytest.raises(ModelNotConfigured):
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hi")


def test_online_mode_actually_attempts_the_remote_call():
    """``FY_OFFLINE_MODE=0`` 才是真联网：失败是 provider 层的失败，不是门拦的。"""
    gw = ModelGateway(_settings(offline_mode=False, model_api_key="k",
                                model_base_url="https://127.0.0.1:9/v1"),
                      max_retries=0)
    with pytest.raises(Exception) as info:
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hi")
    assert not isinstance(info.value, OfflineUnavailable)


def test_injected_adapter_is_not_gated():
    """宿主显式注入的适配器不算「悄悄出网」，门不拦（拦它是拦错东西）。"""
    gw = ModelGateway(provider=MockModelProvider(default_response="本地桩回答"))
    result = gw.complete(ACTOR, task_id="t2", model="mock-deterministic", prompt="hi")
    assert result.text == "本地桩回答"


def test_local_inference_fallback_serves_the_call_and_says_what_was_blocked():
    """远程被拦 → 本机 ollama 顶上，且**如实标注**刚才谁被拦住、为什么。"""
    fallback = MockModelProvider(default_response="本机模型兜底回答")
    gw = ModelGateway(_settings(model_api_key="k", model_base_url="https://api.example/v1"))
    gw.routes = gw.routes + [
        ProviderRoute("ollama", fallback, model_override="qwen2.5:7b", source="fallback"),
    ]
    result = gw.complete(ACTOR, task_id="t3", model="gpt-4o-mini", prompt="hi")

    assert result.text == "本机模型兜底回答"
    assert result.provider_id == "ollama"
    assert result.degraded_from == "openai_compat:gpt-4o-mini"
    assert OFFLINE_MODE_ENV in result.degraded_reason
    assert "ollama" in LOCAL_INFERENCE_PROVIDERS


def test_all_routes_blocked_surfaces_the_offline_reason_list():
    """全部路由都被拦（且没有任何可顶的本地路由）→ 专用 code，不伪装成供应商故障。"""
    gw = ModelGateway(_settings(model_api_key="k", model_base_url="https://api.example/v1"))
    assert [r.provider_id for r in gw.routes] == ["openai_compat"]
    with pytest.raises(OfflineUnavailable) as info:
        gw.complete(ACTOR, task_id="t4", model="gpt-4o-mini", prompt="hi")
    assert "openai_compat" in info.value.message


def test_injected_local_route_can_take_over_while_remote_is_blocked():
    """混合链路：注入的本地路由可以顶上远程，且降级原因如实写明「被谁拦、为什么」。"""
    injected = ProviderRoute("local-stub", _StubProvider("本地桩直答"), source="injected")
    gw = ModelGateway(_settings(model_api_key="k", model_base_url="https://api.example/v1"))
    gw.routes = gw.routes + [injected]

    result = gw.complete(ACTOR, task_id="t5", model="gpt-4o-mini", prompt="hi")
    assert result.text == "本地桩直答"
    assert result.provider_id == "local-stub"
    # 主路（配置里的远程）被门拦下 → 如实标降级原因，不假装用的是原模型。
    assert result.degraded_from == "openai_compat:gpt-4o-mini"
    assert OFFLINE_MODE_ENV in result.degraded_reason


class _StubProvider:
    """最小可用替身：直接吐一个成功结果（模拟「本机就有个能用的推理」）。"""

    provider_id = "local-stub"

    def __init__(self, text: str):
        self._text = text

    def complete(self, *, model, prompt, max_tokens=1024, timeout_seconds=30.0):
        return CallResult(text=self._text,
                          usage={"prompt_tokens": 2, "completion_tokens": 2,
                                 "total_tokens": 4})
