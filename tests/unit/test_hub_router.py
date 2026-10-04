"""W6 · 能力画像与路由（v1 确定性评分，非 LLM 路由）。"""

from __future__ import annotations

import pytest

from find_yourself.db.workbench_models import HubConnection
from find_yourself.services.actor import Actor
from find_yourself.services.errors import PermissionDenied, ValidationFailed
from find_yourself.services.hub.router import CapabilityRouter, tokenize


def _conn(session, owner_id, name, kind="openai_chat", *, capabilities=None,
          healthy=None, state="active", preference=0):
    row = HubConnection(
        id=f"hub-{name}", owner_id=owner_id, name=name, kind=kind,
        group="ai" if kind in {"openai_chat", "anthropic"} else "tool",
        capabilities=capabilities or [], state=state,
        last_health_ok=healthy, preference=preference,
    )
    session.add(row)
    session.flush()
    return row


# --- tokenize ---------------------------------------------------------------- #

def test_tokenize_splits_ascii_and_maps_chinese_synonyms():
    tokens = tokenize("帮我搜索知识库 chat")
    assert "chat" in tokens
    assert "search" in tokens      # 搜索 -> search
    assert "knowledge" in tokens   # 知识 -> knowledge


def test_tokenize_is_empty_safe():
    assert tokenize("") == []


# --- registry ---------------------------------------------------------------- #

def test_register_capability_is_idempotent_by_name(session):
    router = CapabilityRouter(session, "owner-1")
    conn = _conn(session, "owner-1", "c1")
    router.register_capability(conn.id, {"name": "chat", "tags": ["chat"]})
    router.register_capability(conn.id, {"name": "chat", "tags": ["chat", "llm"]})
    caps = router.register_capability(conn.id, {"name": "chat", "tags": ["chat", "llm"]})
    assert len(caps) == 1
    assert set(caps[0]["tags"]) == {"chat", "llm"}


def test_register_capability_enforces_owner(session):
    router = CapabilityRouter(session, "owner-1")
    conn = _conn(session, "owner-2", "c2")
    with pytest.raises(PermissionDenied):
        router.register_capability(conn.id, {"name": "chat"})


def test_unregister_capability(session):
    router = CapabilityRouter(session, "owner-1")
    conn = _conn(session, "owner-1", "c3")
    router.register_capability(conn.id, {"name": "chat", "tags": ["chat"]})
    left = router.unregister_capability(conn.id, "chat")
    assert left == []


# --- routing ----------------------------------------------------------------- #

def test_route_requires_a_hint(session):
    with pytest.raises(ValidationFailed):
        CapabilityRouter(session, "owner-1").route("   ")


def test_route_returns_empty_when_nothing_matches(session):
    _conn(session, "owner-1", "c4", capabilities=[{"name": "chat", "tags": ["chat"]}])
    ranked = CapabilityRouter(session, "owner-1").route("画一张像素风立绘")
    assert ranked == []  # 没有 image 能力 → 不凑数


def test_route_matches_tags_and_explains_why(session):
    _conn(session, "owner-1", "ollama-local",
          capabilities=[{"name": "chat", "tags": ["chat", "llm", "local"]}], healthy=True)
    _conn(session, "owner-1", "my-search", kind="http_webhook",
          capabilities=[{"name": "search", "tags": ["search"]}], healthy=True)
    ranked = CapabilityRouter(session, "owner-1").route("帮我检索内部知识库")
    assert ranked and ranked[0]["connection_name"] == "my-search"
    assert any("标签命中" in r for r in ranked[0]["reasons"])
    assert ranked[0]["score"] > 0


def test_route_prefers_healthy_over_unhealthy(session):
    _conn(session, "owner-1", "healthy", capabilities=[{"name": "chat", "tags": ["chat"]}],
          healthy=True)
    _conn(session, "owner-1", "sick", capabilities=[{"name": "chat", "tags": ["chat"]}],
          healthy=None)
    ranked = CapabilityRouter(session, "owner-1").route("chat")
    assert [c["connection_name"] for c in ranked] == ["healthy", "sick"]
    assert any("探活通过" in r for r in ranked[0]["reasons"])


def test_route_penalises_failed_health_and_honours_preference(session):
    _conn(session, "owner-1", "failing", capabilities=[{"name": "chat", "tags": ["chat"]}],
          healthy=False)
    _conn(session, "owner-1", "favoured", capabilities=[{"name": "chat", "tags": ["chat"]}],
          healthy=True, preference=10)
    ranked = CapabilityRouter(session, "owner-1").route("chat", include_unhealthy=True)
    assert ranked[0]["connection_name"] == "favoured"
    assert any("偏好" in r for r in ranked[0]["reasons"])
    failing = [c for c in ranked if c["connection_name"] == "failing"][0]
    assert any("探活失败" in r for r in failing["reasons"])
    assert failing["score"] < ranked[0]["score"]


def test_route_skips_disabled_and_credential_less_connections(session):
    _conn(session, "owner-1", "disabled", capabilities=[{"name": "chat", "tags": ["chat"]}],
          state="disabled")
    _conn(session, "owner-1", "needs", capabilities=[{"name": "chat", "tags": ["chat"]}],
          state="needs_credentials")
    ranked = CapabilityRouter(session, "owner-1").route("chat")
    assert ranked == []


def test_route_merges_reasons_for_the_same_connection(session):
    _conn(session, "owner-1", "multi", capabilities=[
        {"name": "chat", "tags": ["chat"]},
        {"name": "code", "tags": ["code"]},
    ], healthy=True)
    ranked = CapabilityRouter(session, "owner-1").route("chat and code")
    assert len(ranked) == 1
    assert len(ranked[0]["reasons"]) >= 2


def test_route_is_owner_scoped(session):
    _conn(session, "owner-2", "theirs", capabilities=[{"name": "chat", "tags": ["chat"]}])
    assert CapabilityRouter(session, "owner-1").route("chat") == []


def test_route_for_owner_helper_matches_actor(session):
    from find_yourself.services.hub.router import route_for_owner

    _conn(session, "owner-1", "c5", capabilities=[{"name": "chat", "tags": ["chat"]}])
    assert route_for_owner(session, "owner-1", "chat")[0]["connection_id"] == "hub-c5"
    assert route_for_owner(session, Actor.owner("owner-9").owner_id, "chat") == []
