"""验收 F2 桥接单测：hub 的 knowledge_source 连接 ↔ 知识库链路。

覆盖主控要求的三点：
  ① hub 配 ima 连接（mock）→ /api/kb/sources 可见且 sourced 自 hub；
  ② 该源 fetch 走 hub 凭证（断言取到了 hub store 的凭证路径）；
  ③ hub 断开 / 无连接 → 列表如实降级不报错。

外加：越权 403、非 knowledge_source kind 拒绝、凭证优先级、映射缺失降级。
凭证一律用 W6 的 Fernet 加密路径真实落盘再解，不塞明文进库。
"""

from __future__ import annotations

import httpx
import pytest

from find_yourself.db.workbench_models import HubConnection
from find_yourself.services.actor import Actor
from find_yourself.services.errors import NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.hub.connections import HubService
from find_yourself.services.knowledge import KnowledgeService, hub_bridge

API_KEY = "hub-side-key-abcdef123456"
BASE_URL = "https://ima.example"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

@pytest.fixture()
def kb(session, audit):
    return KnowledgeService(session, audit)


@pytest.fixture()
def owner_actor():
    return Actor.owner("owner-1", csrf_token="")


def make_hub_ima(session, owner_id="owner-1", *, name="中台 ima", api_key=API_KEY,
                 base_url=BASE_URL, state="active") -> HubConnection:
    """用真实的 HubService 建一条 knowledge_source 连接（凭证走 Fernet 加密）。"""
    svc = HubService(session)
    created = svc.create_connection(
        Actor.owner(owner_id, csrf_token=""),
        {
            "name": name,
            "kind": "knowledge_source",
            "config": {"source_id": "ima", "base_url": base_url},
            "credentials": {"api_key": api_key},
            "secret_fields": ["api_key"],
            "credential_fields": [
                {"key": "api_key", "label": "API Key", "secret": True, "required": True},
            ],
            "capabilities": [{"name": "knowledge.list", "tags": ["knowledge"]}],
        },
    )
    session.commit()
    return session.get(HubConnection, created["id"])


# --------------------------------------------------------------------------- #
# ① 发现：hub 源并入 /api/kb/sources
# --------------------------------------------------------------------------- #

def test_hub_knowledge_source_appears_in_source_list(kb, session, owner_actor):
    """hub 配了 ima 连接 → 知识库源列表里能看到，且来源标注为 hub。"""
    make_hub_ima(session)
    cards = kb.list_sources(owner_actor, owner_id="owner-1")
    hub_cards = [c for c in cards if str(c.get("source", "")).startswith("hub:")]
    assert len(hub_cards) == 1, "hub 的 knowledge_source 连接必须被发现"
    card = hub_cards[0]
    assert card["source"] == f"hub:{card['connection_id']}"
    assert card["display_name"] == "中台 ima"
    assert card["builtin_source_id"] == "ima"
    # 凭证已配 → 字段存在性如实为 True，available 反映真实可用性
    assert card["credentials_present"]["api_key"] is True
    assert card["origin"] == "connection"


def test_hub_card_shape_matches_native_cards(kb, session, owner_actor):
    """桥接卡片字段与 W3 原生卡片同形，前端无需分支即可渲染。"""
    make_hub_ima(session)
    cards = {c["source_id"]: c for c in kb.list_sources(owner_actor, owner_id="owner-1")}
    assert "ima" in cards, "原生源必须仍在（不因桥接被替换）"
    native, hub = cards["ima"], next(v for k, v in cards.items() if k.startswith("hub:"))
    for field in (
        "source_id", "display_name", "available", "configured", "degraded",
        "latency_ms", "detail", "hint", "credential_fields",
        "credentials_present", "storage", "persist_restart", "capabilities",
    ):
        assert field in hub, f"桥接卡片缺字段 {field}"
        assert field in native, f"原生卡片缺字段 {field}（对照基准变了）"
    assert hub["capabilities"].keys() == native["capabilities"].keys()


def test_hub_card_never_leaks_plaintext_secret(kb, session, owner_actor):
    make_hub_ima(session, api_key=API_KEY)
    cards = kb.list_sources(owner_actor, owner_id="owner-1")
    blob = repr(cards)
    assert API_KEY not in blob, "源列表绝不能吐出明文凭证"


# --------------------------------------------------------------------------- #
# ② 调用：凭证走 hub 加密存储
# --------------------------------------------------------------------------- #

def test_build_hub_source_uses_credentials_from_hub_connection(session, owner_actor):
    """适配器实例上的凭证 == hub 连接里 Fernet 密文解出来的值。"""
    conn = make_hub_ima(session)
    source = hub_bridge.build_hub_source(session, "owner-1", f"hub:{conn.id}")
    # 密文解出的 key + config 里的 base_url 一起构成可用凭证
    assert source.api_key == API_KEY
    assert source.base_url.rstrip("/") == BASE_URL.rstrip("/")
    assert source.is_configured() is True
    assert hub_bridge.credential_origin(session, "owner-1", f"hub:{conn.id}") == "connection"


def test_hub_source_never_reads_legacy_memory_store(session, owner_actor, monkeypatch):
    """凭证解析只走 hub store；把 W3 旧内存 store 打桩成「有值」也不应被采用。"""
    conn = make_hub_ima(session)
    # 让共享 store 里也有一份不同的 key：连接自带凭证必须优先
    from find_yourself.services.knowledge import sources as sources_mod

    class _Spy:
        def __init__(self, inner):
            self._inner = inner
            self.get_calls: list[str] = []

        def get(self, scope):
            self.get_calls.append(scope)
            return self._inner.get(scope)

        def __getattr__(self, item):
            return getattr(self._inner, item)

    spy = _Spy(sources_mod.secret_store)
    builtin, creds, origin = hub_bridge.resolve_hub_credentials(
        session, "owner-1", conn, store=spy
    )
    assert builtin == "ima"
    assert creds["api_key"] == API_KEY
    assert origin == "connection", "连接自带凭证必须优先于共享 store"
    assert spy.get_calls == [], "连接自带凭证齐备时根本不该去读共享 store"


def test_hub_source_falls_back_to_shared_hub_store(session, owner_actor):
    """连接没带凭证时，复用增补 E 已迁的 hub 加密凭证库（origin=shared_store）。"""
    svc = HubService(session)
    created = svc.create_connection(
        Actor.owner("owner-1", csrf_token=""),
        {
            "name": "无凭证中台连接",
            "kind": "knowledge_source",
            "config": {"source_id": "ima"},
            "credentials": {},
            "secret_fields": ["api_key"],
            "credential_fields": [
                {"key": "api_key", "label": "API Key", "secret": True, "required": True}
            ],
        },
    )
    session.commit()
    conn_id = created["id"]
    # 知识库卡片侧配置过凭证（存进 hub 加密 store）
    from find_yourself.services.knowledge import secret_store

    secret_store.set("ima", {"api_key": "shared-store-key-999", "base_url": BASE_URL})
    try:
        source = hub_bridge.build_hub_source(session, "owner-1", f"hub:{conn_id}")
        assert source.is_configured() is True
        assert hub_bridge.credential_origin(session, "owner-1", f"hub:{conn_id}") == "shared_store"
    finally:
        secret_store.forget("ima")


def test_sync_source_for_hub_id_routes_through_bridge(kb, session, owner_actor):
    """端到端：sync_source('hub:<id>') 走桥接构造适配器，凭证来自 hub。

    打桩网络层（httpx.MockTransport）让 list_sources 返回空集合，于是
    sync_source 正常返回 summary 而不发真网请求——被验证的是「适配器是桥接
    造的、凭证齐备、hub 源 id 原样出现在 summary 里」。
    """
    conn = make_hub_ima(session)
    from find_yourself.services.knowledge.sources.ima import ImaSource

    def _factory(**kwargs):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"data": {"knowledge_bases": []}})
        ))
        return ImaSource(**kwargs, client=client)

    # 让 build_hub_source 造出来的适配器带 mock 传输层（不打真网）
    monkey_target = hub_bridge
    original = monkey_target.ImaSource
    monkey_target.ImaSource = staticmethod(_factory)  # type: ignore[assignment]
    try:
        summary = kb.sync_source(owner_actor, owner_id="owner-1", source_id=f"hub:{conn.id}")
    finally:
        monkey_target.ImaSource = original  # type: ignore[assignment]
    assert summary["source_id"] == f"hub:{conn.id}"
    assert summary["collections"] == 0
    assert summary["errors"] == []


def test_sync_source_rejects_hub_source_without_credentials(kb, session, owner_actor):
    """hub 连接无凭证 → 同步必须显式报未接入，不能静默同步 0 条当成功。"""
    svc = HubService(session)
    created = svc.create_connection(
        Actor.owner("owner-1", csrf_token=""),
        {
            "name": "空壳",
            "kind": "knowledge_source",
            "config": {"source_id": "ima"},
            "credentials": {},
            "secret_fields": ["api_key"],
            "credential_fields": [
                {"key": "api_key", "label": "K", "secret": True, "required": True}
            ],
        },
    )
    session.commit()
    with pytest.raises(ValidationFailed) as err:
        kb.sync_source(owner_actor, owner_id="owner-1", source_id=f"hub:{created['id']}")
    assert err.value.code == "ima_not_configured"


# --------------------------------------------------------------------------- #
# ③ 无连接 / 已断开 → 如实降级
# --------------------------------------------------------------------------- #

def test_no_hub_connection_leaves_native_list_untouched(kb, session, owner_actor):
    """hub 侧一条连接都没有 → 列表只剩原生两条，语义与 W3 完全一致。"""
    cards = kb.list_sources(owner_actor, owner_id="owner-1")
    # P3 备轨上线：原生卡片清单扩为三源（与 W3 注册表一致）。
    assert [c["source_id"] for c in cards] == ["baidu_pan", "ima", "local_files"]


def test_missing_hub_connection_raises_not_found(session, owner_actor):
    """连接被删后仍拿旧 id 调 → 显式报错，不静默返回空。"""
    with pytest.raises(NotFound) as err:
        hub_bridge.build_hub_source(session, "owner-1", "hub:does-not-exist")
    assert err.value.code == "unknown_hub_source"


def test_unconfigured_hub_connection_reports_not_connected_honestly(kb, session, owner_actor):
    """连接在、但一个凭证都没有 → 显示「未接入」，不谎报可用。"""
    svc = HubService(session)
    created = svc.create_connection(
        Actor.owner("owner-1", csrf_token=""),
        {
            "name": "空壳连接",
            "kind": "knowledge_source",
            "config": {"source_id": "ima"},
            "credentials": {},
            "secret_fields": ["api_key"],
            "credential_fields": [
                {"key": "api_key", "label": "K", "secret": True, "required": True}
            ],
        },
    )
    session.commit()
    cards = kb.list_sources(owner_actor, owner_id="owner-1")
    card = next(c for c in cards if c["source_id"] == f"hub:{created['id']}")
    assert card["available"] is False
    assert card["configured"] is False
    assert "未接入" in card["detail"]
    assert "中台页" in card["detail"]
    assert card["origin"] == "none"


def test_hub_connection_unmapped_to_builtin_degrades_without_breaking_list(
    kb, session, owner_actor,
):
    """config 里没写 source_id → 单条降级成不可用卡片，其余卡片照常返回。"""
    svc = HubService(session)
    created = svc.create_connection(
        Actor.owner("owner-1", csrf_token=""),
        {"name": "没映射的连接", "kind": "knowledge_source", "config": {}},
    )
    session.commit()
    cards = kb.list_sources(owner_actor, owner_id="owner-1")
    ids = [c["source_id"] for c in cards]
    assert f"hub:{created['id']}" in ids
    assert "ima" in ids, "一条坏数据不能拖垮整个列表"
    bad = next(c for c in cards if c["source_id"] == f"hub:{created['id']}")
    assert bad["available"] is False
    assert bad["origin"] == "unmapped"


def test_hub_health_failure_reason_is_surfaced(kb, session, owner_actor):
    """hub 侧探活失败的真实原因要出现在卡片 detail 里。"""
    conn = make_hub_ima(session)
    conn.last_health_ok = False
    conn.last_health_detail = "Connection refused"
    session.commit()
    cards = kb.list_sources(owner_actor, owner_id="owner-1")
    card = next(c for c in cards if c["source_id"] == f"hub:{conn.id}")
    assert "Connection refused" in card["detail"]
    assert card["health"]["ok"] is False
    assert card["health"]["detail"] == "Connection refused"


# --------------------------------------------------------------------------- #
# 隔离与错误路径
# --------------------------------------------------------------------------- #

def test_other_owner_cannot_reach_hub_source(session, owner_actor):
    """越权：别人的 hub 连接 → 403，且不泄露连接名。"""
    conn = make_hub_ima(session, owner_id="owner-2", name="别人的私有源")
    with pytest.raises(PermissionDenied) as err:
        hub_bridge.build_hub_source(session, "owner-1", f"hub:{conn.id}")
    assert err.value.code == "hub_connection_forbidden"
    assert "别人的私有源" not in str(err.value.message)


def test_non_knowledge_source_connection_is_refused(session, owner_actor):
    """kind 不是 knowledge_source 的连接不能当知识源用。"""
    svc = HubService(session)
    created = svc.create_connection(
        Actor.owner("owner-1", csrf_token=""),
        {"name": "一个 webhook", "kind": "http_webhook",
         "config": {"url": "https://api.example.com/hook"}},
    )
    session.commit()
    with pytest.raises(ValidationFailed) as err:
        hub_bridge.build_hub_source(session, "owner-1", f"hub:{created['id']}")
    assert err.value.code == "hub_not_knowledge_source"


def test_kb_source_cannot_be_configured_through_w3_api(kb, owner_actor):
    """hub 源的凭证只在中台配；走 W3 配置接口要显式拒绝并指路。"""
    with pytest.raises(ValidationFailed) as err:
        kb.configure_source("hub:whatever", {"api_key": "x"})
    assert err.value.code == "hub_source_configure_in_hub"


def test_kb_source_cannot_be_forgotten_through_w3_api(kb):
    with pytest.raises(ValidationFailed) as err:
        kb.forget_source("hub:whatever")
    assert err.value.code == "hub_source_forget_in_hub"


# --------------------------------------------------------------------------- #
# id 工具
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected,is_hub", [
    ("hub:c-1", "c-1", True),
    ("ima", None, False),
    ("baidu_pan", None, False),
])
def test_source_id_helpers(raw, expected, is_hub):
    assert hub_bridge.is_hub_source(raw) is is_hub
    if is_hub:
        assert hub_bridge.hub_connection_id(raw) == expected
    else:
        with pytest.raises(ValidationFailed):
            hub_bridge.hub_connection_id(raw)


def test_hub_source_id_roundtrip():
    assert hub_bridge.hub_connection_id(hub_bridge.hub_source_id("c-9")) == "c-9"


def test_hub_prefix_without_id_is_rejected():
    with pytest.raises(ValidationFailed) as err:
        hub_bridge.hub_connection_id("hub:   ")
    assert err.value.code == "hub_source_missing_id"
