"""验收 F2 · API 层桥接：``GET /api/kb/sources`` 并入 hub 知识源。

主控要求①的端点级证据：hub 配了 ima 连接后，``/api/kb/sources`` 看得见它，
且 ``source`` 字段标注为 ``hub:<connection_id>``；hub 侧无连接时列表如实降级。
"""

from __future__ import annotations

import pytest
from helpers import login_owner

from find_yourself.services.hub.connections import HubService

API_KEY = "api-hub-key-0123456789"

# 本地账号表的 owner_id（/auth/me 返回的那个），**不是** OIDC 的 sub。
OWNER_ID = "owner"
INTRUDER_ID = "intruder"
BASE_URL = "https://ima.example"


@pytest.fixture()
def owner(client):
    return client, login_owner(client)


def _create_hub_source(session_maker, owner_id: str, *, name: str = "中台 ima",
                       api_key: str = API_KEY, with_secret: bool = True) -> str:
    """直接经 HubService 建连接，返回 conn_id（凭证走真实 Fernet 加密）。"""
    from find_yourself.services.actor import Actor

    with session_maker() as s:
        svc = HubService(s)
        created = svc.create_connection(
            Actor.owner(owner_id, csrf_token=""),
            {
                "name": name,
                "kind": "knowledge_source",
                "config": {"source_id": "ima", "base_url": BASE_URL},
                "credentials": {"api_key": api_key} if with_secret else {},
                "secret_fields": ["api_key"],
                "credential_fields": [
                    {"key": "api_key", "label": "API Key", "secret": True, "required": True}
                ],
                "capabilities": [{"name": "knowledge.list", "tags": ["knowledge"]}],
            },
        )
        s.commit()
        return str(created["id"])


# --------------------------------------------------------------------------- #
# ① 发现
# --------------------------------------------------------------------------- #

def test_hub_knowledge_source_visible_in_kb_sources_api(client, app, owner):
    """hub 配 ima → /api/kb/sources 里出现 source=hub:<id> 的卡片。"""
    c, _ = owner
    conn_id = _create_hub_source(app.state.session_maker, OWNER_ID)
    r = c.get("/api/kb/sources")
    assert r.status_code == 200, r.text
    sources = r.json()["sources"]
    ids = [s["source_id"] for s in sources]
    assert f"hub:{conn_id}" in ids
    card = next(s for s in sources if s["source_id"] == f"hub:{conn_id}")
    assert card["source"] == f"hub:{conn_id}"
    assert card["display_name"] == "中台 ima"
    assert card["origin"] == "connection"
    assert card["credentials_present"]["api_key"] is True
    # 原生源不受影响
    assert "ima" in ids and "baidu_pan" in ids


def test_kb_sources_api_never_leaks_plaintext(client, app, owner):
    c, _ = owner
    _create_hub_source(app.state.session_maker, OWNER_ID)
    r = c.get("/api/kb/sources")
    assert API_KEY not in r.text


def test_kb_sources_api_shape_is_front_end_compatible(client, app, owner):
    """桥接卡片与原生卡片字段一致，前端 KnowledgePage 无需分支。"""
    c, _ = owner
    _create_hub_source(app.state.session_maker, OWNER_ID)
    sources = c.get("/api/kb/sources").json()["sources"]
    native = next(s for s in sources if s["source_id"] == "ima")
    hub = next(s for s in sources if s["source_id"].startswith("hub:"))
    required = {
        "source_id", "display_name", "available", "configured", "degraded",
        "latency_ms", "detail", "hint", "credential_fields",
        "credentials_present", "storage", "persist_restart", "capabilities",
    }
    assert required <= set(hub), f"缺字段：{required - set(hub)}"
    assert set(hub["capabilities"]) == set(native["capabilities"])


# --------------------------------------------------------------------------- #
# ③ 无连接 / 未配置 → 如实降级
# --------------------------------------------------------------------------- #

def test_kb_sources_api_without_any_hub_connection_is_unchanged(client, owner):
    """hub 一条连接都没有 → 只剩原生两条，count 不变。"""
    c, _ = owner
    body = c.get("/api/kb/sources").json()
    assert body["count"] == 3  # P3 备轨上线：三源
    # P3 备轨上线：/api/kb/sources 原生清单扩为三源。
    assert [s["source_id"] for s in body["sources"]] == ["baidu_pan", "ima", "local_files"]


def test_kb_sources_api_reports_unconfigured_hub_source_honestly(client, app, owner):
    c, _ = owner
    conn_id = _create_hub_source(
        app.state.session_maker, OWNER_ID, name="空壳", with_secret=False
    )
    sources = c.get("/api/kb/sources").json()["sources"]
    card = next(s for s in sources if s["source_id"] == f"hub:{conn_id}")
    assert card["available"] is False
    assert card["configured"] is False
    assert "未接入" in card["detail"]


def test_sync_hub_source_after_connection_deleted_is_404_not_silent(client, app, owner):
    """连接被删后调 sync → 404 显式报错，不静默同步 0 条。"""
    c, csrf = owner
    conn_id = _create_hub_source(app.state.session_maker, OWNER_ID)
    r = c.delete(f"/api/hub/connections/{conn_id}", headers=csrf)
    assert r.status_code == 200
    r2 = c.post(f"/api/kb/sources/hub:{conn_id}/sync", headers=csrf)
    assert r2.status_code == 404, r2.text
    assert r2.json()["error"]["code"] == "unknown_hub_source"


def test_sync_hub_source_without_credentials_is_422(client, app, owner):
    c, csrf = owner
    conn_id = _create_hub_source(
        app.state.session_maker, OWNER_ID, name="空壳", with_secret=False
    )
    r = c.post(f"/api/kb/sources/hub:{conn_id}/sync", headers=csrf)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "ima_not_configured"


# --------------------------------------------------------------------------- #
# 越权与写路径归属
# --------------------------------------------------------------------------- #

def test_hub_source_of_other_owner_is_403_and_hidden(client, app, owner):
    """别人的 hub 连接：不出现在列表里，直接访问 403。"""
    c, _ = owner
    conn_id = _create_hub_source(
        app.state.session_maker, INTRUDER_ID, name="别人的私有源"
    )
    # 列表里没有别人的源
    ids = [s["source_id"] for s in c.get("/api/kb/sources").json()["sources"]]
    assert f"hub:{conn_id}" not in ids
    # 直接 sync → 403，且错误体不泄露连接名
    csrf = login_owner(c)
    r = c.post(f"/api/kb/sources/hub:{conn_id}/sync", headers=csrf)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "hub_connection_forbidden"
    assert "别人的私有源" not in r.text


def test_configure_hub_source_through_kb_api_is_rejected(client, owner):
    """hub 源的凭证只在中台配；走 kb 配置接口要 422 + 指路。"""
    c, csrf = owner
    r = c.post(
        "/api/kb/sources/hub:whatever/configure",
        json={"api_key": "x"},
        headers=csrf,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "hub_source_configure_in_hub"


def test_forget_hub_source_through_kb_api_is_rejected(client, owner):
    c, csrf = owner
    r = c.delete("/api/kb/sources/hub:whatever", headers=csrf)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "hub_source_forget_in_hub"


def test_non_knowledge_source_connection_rejected_by_sync(client, app, owner):
    c, csrf = owner
    from find_yourself.services.actor import Actor

    with app.state.session_maker() as s:
        created = HubService(s).create_connection(
            Actor.owner(OWNER_ID, csrf_token=""),
            {"name": "普通 webhook", "kind": "http_webhook",
             "config": {"url": "https://api.example.com/hook"}},
        )
        s.commit()
        webhook_id = str(created["id"])
    r = c.post(f"/api/kb/sources/hub:{webhook_id}/sync", headers=csrf)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "hub_not_knowledge_source"


def test_kb_sources_requires_auth(client):
    r = client.get("/api/kb/sources")
    assert r.status_code in (401, 403)
