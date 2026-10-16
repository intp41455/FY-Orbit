"""回归守卫：Chat 调试页两个「演示必翻车」缺陷。

缺陷 1（price_unknown）：``StreamChatRequest.model`` 缺省是字面 ``"default"``，
   网关 ``resolve_pricing`` 三个价格源都不命中它 → ``PriceUnknown``（§7 拒绝
   按 0 计费）→ 每次提问以 error 帧收场。
缺陷 2（task_not_found）：Chat 用 ``chat-debug``/``adhoc`` 这类合成 task_id，
   ``BudgetService.reserve`` 只认已存在的 Task 行 → ``NotFound`` → 同上。

修复：路由层把占位名解成 BYOK 叠加后的真实模型名，并幂等自建归集 Task 行
（P0-7 同范式，对照 ``workflow_gen._ensure_gen_task``）。
"""

from __future__ import annotations

import pytest

from find_yourself.api.routes import streaming as s
from find_yourself.api.routes.streaming import (
    _PLACEHOLDER_MODELS,
    _ensure_chat_task,
    _resolve_model_name,
)


class _Settings:
    """最小 Settings 替身：只带网关叠加关心的字段。"""

    def __init__(self, **kw):
        self.model_name = kw.get("model_name", "")
        self.model_api_key = kw.get("model_api_key", "")
        self.model_base_url = kw.get("model_base_url", "")

    def model_copy(self, update: dict) -> "_Settings":
        merged = {
            "model_name": self.model_name,
            "model_api_key": self.model_api_key,
            "model_base_url": self.model_base_url,
        }
        merged.update(update)
        return _Settings(**merged)


class _Actor:
    owner_id = "owner-abc"


@pytest.fixture(autouse=True)
def _isolate_secret_store(monkeypatch):
    """secret_store 是磁盘持久化单例——测试一律纯内存隔离，不污染真实存储。"""
    from find_yourself.services.knowledge.sources import store as store_mod

    store_mod.secret_store.set_in_memory_only = True  # type: ignore[attr-defined]
    monkeypatch.setattr(
        store_mod.secret_store, "get", lambda scope: {}, raising=True
    )


# -- 缺陷 1：占位模型名必须解析成真实模型名 ------------------------------


def test_explicit_model_name_passes_through():
    assert _resolve_model_name("agnes-3.0-flash", _Settings()) == "agnes-3.0-flash"


def test_explicit_name_wins_over_byok():
    """显式点名优先于 BYOK 解析——不能悄悄把用户指定的模型换掉。"""
    settings = _Settings(model_name="byok-model-9")
    assert _resolve_model_name("explicit-model", settings) == "explicit-model"


def test_placeholder_default_resolves_via_byok(monkeypatch):
    from find_yourself.services import model_credentials as mc

    def _fake_resolve(settings):
        return settings.model_copy({"model_name": "byok-model-9"})

    monkeypatch.setattr(mc, "resolve_model_settings", _fake_resolve)
    settings = _Settings(model_name="")
    assert _resolve_model_name("default", settings) == "byok-model-9"


def test_placeholder_auto_empty_variants(monkeypatch):
    from find_yourself.services import model_credentials as mc

    monkeypatch.setattr(
        mc, "resolve_model_settings",
        lambda st: st.model_copy({"model_name": "byok-model-9"}),
    )
    settings = _Settings()
    for placeholder in ("", "default", "auto", "DEFAULT", "  "):
        assert _resolve_model_name(placeholder, settings) == "byok-model-9", placeholder


def test_unresolvable_returns_placeholder_not_fabricated():
    """解析不出时必须原样返回占位名，让上层如实报 MODEL_NOT_CONFIGURED。"""
    settings = _Settings()
    assert _resolve_model_name("default", settings) == "default"
    assert _resolve_model_name("", settings) == ""


def test_placeholder_set_is_lowercase_compared():
    assert _PLACEHOLDER_MODELS == {"", "default", "auto"}


# -- 缺陷 2：归集 Task 行幂等自建 ----------------------------------------


class _FakeSession:
    def __init__(self, existing=None):
        self._rows = dict(existing or {})
        self.added = []

    def get(self, model, pk):
        return self._rows.get(pk)

    def add(self, row):
        self._rows[row.id] = row
        self.added.append(row)

    def flush(self):
        pass


class _Task:
    def __init__(self, **kw):
        self.id = kw["id"]


def test_ensure_chat_task_creates_row_once():
    session = _FakeSession()
    _ensure_chat_task(session, _Actor(), "chat-debug")
    assert len(session.added) == 1
    row = session.added[0]
    assert row.owner_id == "owner-abc"
    assert "Chat 调试" in row.goal and "非用户业务任务" in row.goal
    # 幂等：第二次不再建
    _ensure_chat_task(session, _Actor(), "chat-debug")
    assert len(session.added) == 1


def test_ensure_chat_task_reuses_real_business_task():
    """调用方带了真实业务任务 id 时，绝不覆盖、绝不另建归集行。"""
    real = _Task(id="real-task-1")
    session = _FakeSession(existing={"real-task-1": real})
    _ensure_chat_task(session, _Actor(), "real-task-1")
    assert session.added == []
    assert session.get(_Task, "real-task-1") is real


def test_ensure_chat_task_tolerates_none_session():
    _ensure_chat_task(None, _Actor(), "chat-debug")  # 不抛
    _ensure_chat_task(_FakeSession(), _Actor(), "")  # 空 task_id 不建


def test_module_exports_helpers():
    assert callable(_resolve_model_name)
    assert callable(_ensure_chat_task)
    assert hasattr(s, "StreamChatRequest")


# -- 缺陷 3：BYOK 叠加遗漏 → 明明配了模型却永远出 stub --------------------


class _StubSettings:
    """空宿主 settings：凭据只可能来自 BYOK 叠加。"""

    model_name = ""
    model_api_key = ""
    model_base_url = ""
    model_provider = ""
    model_fallbacks: list = []

    def model_copy(self, update: dict) -> "_StubSettings":
        new = _StubSettings()
        for k, v in update.items():
            setattr(new, k, v)
        return new


class _FakeProvider:
    provider_id = "openai_compat"


def test_streaming_service_applies_byok_overlay(monkeypatch):
    """核心回归：构造 StreamingService 时必须叠加 BYOK，否则 provider=None → stub。

    用户在设置页填的模型凭据只落在 secret_store。若用未叠加的 settings 构造
    网关，provider 恒为 None，Chat 永远出确定性 stub——「配了模型却不能用」。
    """
    from find_yourself.services import model_credentials as mc
    from find_yourself.services.streaming import StreamingService

    monkeypatch.setattr(
        mc, "resolve_model_settings",
        lambda st: st.model_copy({
            "model_api_key": "sk-test-key-1234567890",
            "model_base_url": "https://example.invalid/v1",
            "model_name": "byok-model-9",
        }),
    )
    svc = StreamingService(_StubSettings())
    # gateway 用叠加后的 settings 建成 → 有 provider → 不会落 stub
    assert svc.gateway.provider is not None
    assert svc.resolve_mode() == "gateway"
    assert svc.gateway.primary_provider_id == "openai_compat"


def test_injected_gateway_is_respected():
    """显式注入的 gateway 不被叠加逻辑覆盖（宿主/测试自接线优先）。"""
    from find_yourself.runtime.gateway import ModelGateway
    from find_yourself.services.streaming import StreamingService

    class _FakeGateway(ModelGateway):
        def __init__(self):
            self.settings = None
            self.provider = _FakeProvider()
            self.routes = []
            self.primary_provider_id = "openai_compat"
            self.price_overrides = {}

    injected = _FakeGateway()
    svc = StreamingService(_StubSettings(), gateway=injected)
    assert svc.gateway is injected
    assert svc.resolve_mode() == "gateway"
