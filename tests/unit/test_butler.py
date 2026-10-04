"""数码小屋专属管家 Agent（butler）单元测试。

覆盖（工单：管家 Agent 接线）：
1. persona prompt 组装：管家人设 / 性格语气 / 非敏感 context 白名单注入 / 画像隔离声明。
2. 无模型 key 时诚实抛 ModelNotConfigured（绝不回退假台词；回退池是前端职责）。
3. 进程内限频：每 owner 每分钟 ≤10 次，超限 ButlerRateLimited（HTTP 429）。
4. 路由：未认证 401；认证后 GET /status 返回 model_configured、POST /dialogue
   未配置模型时诚实 503 model_not_configured（CSRF 保护）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from find_yourself.api.errors import register_exception_handlers
from find_yourself.api.routes import butler as butler_routes
from find_yourself.config import Settings
from find_yourself.db.models import AuthSession
from find_yourself.db.types import utcnow
from find_yourself.runtime.gateway import CallResult, ModelGateway, ModelNotConfigured
from find_yourself.services.actor import Actor
from find_yourself.services.butler import (
    BUTLER_RATE_LIMIT,
    BUTLER_RATE_WINDOW_SECONDS,
    ButlerRateLimited,
    ButlerService,
    check_butler_rate_limit,
)


class _StubProvider:
    """Deterministic provider recording the last prompt it received."""

    def __init__(self, text: str = "晚上好，主人。小屋一切都好。"):
        from decimal import Decimal

        self._decimal = Decimal
        self.text = text
        self.last_call: dict = {}

    def complete(self, *, model: str, prompt: str, max_tokens: int = 1024,
                 timeout_seconds: float = 30.0):
        self.last_call = {"model": model, "prompt": prompt, "max_tokens": max_tokens}
        return CallResult(
            text=self.text,
            usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            settled_amount=self._decimal("0"),
            provider_request_id="stub-1",
        )


def _model_backed_butler(provider: _StubProvider | None = None, rate_store=None) -> ButlerService:
    settings = Settings(
        session_secret="x" * 32,
        model_api_key="test-key",
        model_base_url="http://127.0.0.1:9",
        model_name="gpt-4o-mini",
    )
    gateway = ModelGateway(settings, provider=provider or _StubProvider())
    return ButlerService(settings=settings, model_gateway=gateway, rate_store=rate_store)


# ============================================================================
# 1. Persona prompt 组装
# ============================================================================

def test_dialogue_prompt_persona_tone_and_context_injection():
    svc = ButlerService()
    prompt = svc.build_dialogue_prompt(
        speaker="pet",
        personality="cool",
        context={"user_name": "小寻", "pet_name": "团子", "house": "cabin", "background": "forest"},
    )
    # 管家人设 + 角色映射 + 语气基调
    assert "专属管家" in prompt
    assert "宠物" in prompt  # speaker=pet -> 宠物
    assert "高冷" in prompt  # cool -> 高冷简短
    # 非敏感 context 注入
    assert "主人名字：小寻" in prompt
    assert "宠物名字：团子" in prompt
    assert "小木屋" in prompt and "树林" in prompt
    # 画像隔离：如实声明未接入画像数据（留待 RAG 适配器），禁止编造
    assert "没有接入用户的个人画像与私人记忆数据" in prompt
    assert "RAG" in prompt
    # 一句台词的输出纪律
    assert "只输出这一句台词本身" in prompt


def test_dialogue_prompt_context_whitelist_and_truncation():
    svc = ButlerService()
    prompt = svc.build_dialogue_prompt(
        speaker="person",
        personality="melancholy",
        context={"user_name": "阿" * 100, "secret_diary": "不该出现的内容", "house": "castle"},
    )
    assert "阿" * 24 in prompt
    assert "阿" * 25 not in prompt  # 超长截断到 24 字
    assert "不该出现的内容" not in prompt  # 白名单外字段绝不注入
    assert "城堡" in prompt


def test_dialogue_prompt_unknown_personality_falls_back_to_neutral_tone():
    svc = ButlerService()
    prompt = svc.build_dialogue_prompt(speaker="person", personality="mystery")
    assert "语气基调" in prompt  # 不抛错，回退默认语气
    assert "mystery" in prompt  # 仍如实携带 personality 供模型参考


# ============================================================================
# 2. 诚实语义：无 key 抛 ModelNotConfigured；有网关走 model 来源
# ============================================================================

def test_dialogue_without_settings_raises_honest_error(owner: Actor):
    svc = ButlerService(rate_store={})
    with pytest.raises(ModelNotConfigured) as exc:
        svc.dialogue(owner, speaker="person", personality="lively")
    assert "FY_MODEL_API_KEY" in str(exc.value)


def test_dialogue_with_unkeyed_settings_raises_honest_error(owner: Actor):
    svc = ButlerService(settings=Settings(session_secret="x" * 32), rate_store={})
    with pytest.raises(ModelNotConfigured) as exc:
        svc.dialogue(owner, speaker="person", personality="lively")
    assert "FY_MODEL_API_KEY" in str(exc.value)


def test_dialogue_with_gateway_returns_model_source(owner: Actor):
    provider = _StubProvider("晚上好，主人。")
    svc = _model_backed_butler(provider=provider, rate_store={})
    resp = svc.dialogue(
        owner,
        speaker="person",
        personality="chatty",
        context={"user_name": "小寻", "house": "villa"},
    )
    assert resp == {"line": "晚上好，主人。", "source": "model", "model": "gpt-4o-mini"}
    # prompt 真实送达 provider，且一句台词受 max_tokens=64 限制
    assert "小寻" in provider.last_call["prompt"]
    assert provider.last_call["max_tokens"] == 64
    assert "别墅" in provider.last_call["prompt"]


def test_dialogue_rejects_unknown_speaker(owner: Actor):
    svc = ButlerService(rate_store={})
    from find_yourself.services.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        svc.dialogue(owner, speaker="ghost", personality="lively")


def test_dialogue_requires_authenticated_actor():
    svc = ButlerService(rate_store={})
    from find_yourself.services.errors import Unauthenticated

    with pytest.raises(Unauthenticated):
        svc.dialogue(Actor(subject_type=""), speaker="person", personality="lively")


# ============================================================================
# 3. 进程内限频：每 owner 每分钟 ≤10 次，超限 429
# ============================================================================

def test_rate_limit_allows_ten_then_429(owner: Actor):
    svc = _model_backed_butler(rate_store={})
    for _ in range(BUTLER_RATE_LIMIT):
        resp = svc.dialogue(owner, speaker="pet", personality="lively")
        assert resp["source"] == "model"
    with pytest.raises(ButlerRateLimited) as exc:
        svc.dialogue(owner, speaker="pet", personality="lively")
    assert exc.value.http_status == 429
    assert exc.value.code == "butler_rate_limited"


def test_rate_limit_is_per_owner(owner: Actor):
    svc = _model_backed_butler(rate_store={})
    for _ in range(BUTLER_RATE_LIMIT):
        svc.dialogue(owner, speaker="person", personality="cool")
    # 另一个 owner 不受影响
    other = Actor.owner("owner-2")
    resp = svc.dialogue(other, speaker="person", personality="cool")
    assert resp["source"] == "model"
    # 原 owner 依旧超限
    with pytest.raises(ButlerRateLimited):
        svc.dialogue(owner, speaker="person", personality="cool")


def test_rate_limit_window_slides(owner: Actor):
    import time as _time

    store: dict = {}
    for _ in range(BUTLER_RATE_LIMIT):
        check_butler_rate_limit("owner-1", store=store)
    with pytest.raises(ButlerRateLimited):
        check_butler_rate_limit("owner-1", store=store)
    # 模拟窗口滑出：手工把最早一条的时间戳拨回 window 之前
    hits = store["owner-1"]
    hits[0] = _time.monotonic() - BUTLER_RATE_WINDOW_SECONDS - 1
    check_butler_rate_limit("owner-1", store=store)  # 不再 429


# ============================================================================
# 4. 路由：鉴权 401 / status / 诚实 503（CSRF 保护）
# ============================================================================

@pytest.fixture()
def route_env(engine) -> tuple[sessionmaker, Settings]:
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret",
    )
    return sm, settings


def _build_client(sm: sessionmaker, settings: Settings) -> TestClient:
    app = FastAPI()
    app.state.settings = settings
    app.state.session_maker = sm
    register_exception_handlers(app)
    app.include_router(butler_routes.router)
    return TestClient(app)


def _make_owner_session(sm: sessionmaker, *, session_id: str, csrf_secret: str) -> None:
    s = sm()
    try:
        s.add(
            AuthSession(
                id=session_id,
                owner_id="owner-1",
                csrf_secret=csrf_secret,
                token_hash=None,  # 明文 id 直查主键（legacy 路径，仅测试）
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        s.commit()
    finally:
        s.close()


def test_butler_routes_reject_unauthenticated(route_env):
    sm, settings = route_env
    client = _build_client(sm, settings)

    r = client.get("/api/butler/status")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"

    r2 = client.post("/api/butler/dialogue", json={"speaker": "person", "personality": "lively"})
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "unauthenticated"


def test_butler_status_reports_unconfigured(route_env):
    sm, settings = route_env
    _make_owner_session(sm, session_id="sess-butler-1", csrf_secret="csrf-1")
    client = _build_client(sm, settings)

    r = client.get("/api/butler/status", cookies={"fy_session": "sess-butler-1"})
    assert r.status_code == 200
    assert r.json() == {"model_configured": False}


def test_butler_dialogue_route_csrf_and_honest_503(route_env):
    sm, settings = route_env
    _make_owner_session(sm, session_id="sess-butler-2", csrf_secret="csrf-2")
    client = _build_client(sm, settings)
    cookies = {"fy_session": "sess-butler-2"}

    # 缺 CSRF -> 403
    r_no_csrf = client.post(
        "/api/butler/dialogue",
        json={"speaker": "person", "personality": "lively"},
        cookies=cookies,
    )
    assert r_no_csrf.status_code == 403
    assert r_no_csrf.json()["error"]["code"] == "csrf"

    # 带 CSRF 但未配置模型 -> 诚实 503，绝不返回伪装台词
    r = client.post(
        "/api/butler/dialogue",
        json={
            "speaker": "person",
            "personality": "lively",
            "context": {"user_name": "小寻", "house": "cabin"},
        },
        headers={"X-CSRF-Token": "csrf-2"},
        cookies=cookies,
    )
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "model_not_configured"
    assert "FY_MODEL_API_KEY" in body["error"]["message"]


def test_butler_dialogue_route_rejects_extra_fields(route_env):
    sm, settings = route_env
    _make_owner_session(sm, session_id="sess-butler-3", csrf_secret="csrf-3")
    client = _build_client(sm, settings)
    r = client.post(
        "/api/butler/dialogue",
        json={"speaker": "person", "personality": "lively", "evil_field": 1},
        headers={"X-CSRF-Token": "csrf-3"},
        cookies={"fy_session": "sess-butler-3"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"
