"""P5 · 插件市场 HTTP 接口测试（列表/详情/上架/安装）。

走完整 app 鉴权栈（共享 ``client`` fixture）。核心契约：
* 未过门禁的包在**服务端**就不可见（列表与 total 都不含它）；
* 分页超界 → 422；
* 上架复用 promote 门禁（缺 evaluation → 409）；
* plugin 包安装缺授权 → 422 显式拒绝。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def _instruction(name: str) -> dict:
    return {
        "name": name,
        "semantic_version": "1.0.0",
        "skill_md": f"# {name}\n\n惰性指令包。",
        "source": "internal",
        "license": "MIT",
        "domain": "work",
    }


def _stage_and_publish(client: TestClient, headers: dict, name: str) -> str:
    r = client.post("/api/skills/stage",
                    json={"name": name, "semantic_version": "1.0.0",
                          "package": _instruction(name), "source": "internal",
                          "license": "MIT", "domain": "work"},
                    headers=headers)
    assert r.status_code == 200, r.text
    skill_id = r.json()["id"]
    r = client.post(f"/api/skills/{skill_id}/evaluate",
                    json={"static_passed": True, "functional_passed": True},
                    headers=headers)
    assert r.status_code == 200, r.text
    evaluation_id = r.json()["evaluation_id"]
    r = client.post(f"/api/plugins/marketplace/{skill_id}/publish",
                    json={"evaluation_id": evaluation_id}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"
    return skill_id


def test_marketplace_list_empty_then_lists_published(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/plugins/marketplace", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "limit": 20, "offset": 0, "sort_by": "score"}

    sk_id = _stage_and_publish(client, headers, "http-listed")
    r = client.get("/api/plugins/marketplace", headers=headers)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["skill_id"] == sk_id
    assert body["items"][0]["risk_level"] in ("low", "medium", "high")


def test_marketplace_hides_unpublished_server_side(client: TestClient):
    """门禁核心：stage 未 promote 的包，HTTP 列表查不到（服务端过滤）。"""
    headers = login_owner(client)
    r = client.post("/api/skills/stage",
                    json={"name": "http-hidden", "semantic_version": "1.0.0",
                          "package": _instruction("http-hidden"), "source": "internal",
                          "license": "MIT", "domain": "work"},
                    headers=headers)
    assert r.status_code == 200
    body = client.get("/api/plugins/marketplace", headers=headers).json()
    assert body["total"] == 0
    assert all(i["name"] != "http-hidden" for i in body["items"])


def test_marketplace_detail_404_for_unlisted(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/plugins/marketplace/missing-id", headers=headers)
    assert r.status_code == 404


def test_marketplace_detail_shows_risk_and_scan(client: TestClient):
    headers = login_owner(client)
    sk_id = _stage_and_publish(client, headers, "http-detail")
    r = client.get(f"/api/plugins/marketplace/{sk_id}", headers=headers)
    assert r.status_code == 200
    card = r.json()
    assert card["gate_profile"] == "instruction"
    assert card["capabilities"] == ["instruction:inline"]
    assert card["scan"]["passed"] is True
    assert "risk_reasons" in card and "risk_level" in card


def test_marketplace_publish_requires_evaluation(client: TestClient):
    """上架复用 promote 门禁：无 evaluation → 409（不是市场自己的放行）。"""
    headers = login_owner(client)
    r = client.post("/api/skills/stage",
                    json={"name": "http-noev", "semantic_version": "1.0.0",
                          "package": _instruction("http-noev"), "source": "internal",
                          "license": "MIT", "domain": "work"},
                    headers=headers)
    sk_id = r.json()["id"]
    r = client.post(f"/api/plugins/marketplace/{sk_id}/publish",
                    json={"evaluation_id": "nope"}, headers=headers)
    assert r.status_code == 409


def test_marketplace_pagination_over_limit_422(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/plugins/marketplace", params={"limit": 101}, headers=headers)
    assert r.status_code == 422
    r = client.get("/api/plugins/marketplace", params={"limit": 0}, headers=headers)
    assert r.status_code == 422
    r = client.get("/api/plugins/marketplace", params={"offset": -1}, headers=headers)
    assert r.status_code == 422
    # 默认有界：不带参数时 limit=20
    body = client.get("/api/plugins/marketplace", headers=headers).json()
    assert body["limit"] == 20


def test_marketplace_query_and_capability_filters(client: TestClient):
    headers = login_owner(client)
    _stage_and_publish(client, headers, "filter-alpha")
    _stage_and_publish(client, headers, "filter-beta")
    body = client.get("/api/plugins/marketplace", params={"query": "alpha"},
                      headers=headers).json()
    assert body["total"] == 1
    body = client.get("/api/plugins/marketplace",
                      params={"capability": "instruction:inline"}, headers=headers).json()
    assert body["total"] >= 1
    assert all("instruction:inline" in i["capabilities"] for i in body["items"])
    r = client.get("/api/plugins/marketplace",
                   params={"capability": "made-up"}, headers=headers)
    assert r.status_code == 422


def test_marketplace_install_instruction_ok_and_unknown_404(client: TestClient):
    headers = login_owner(client)
    sk_id = _stage_and_publish(client, headers, "http-install")
    r = client.post(f"/api/plugins/marketplace/{sk_id}/install", json={}, headers=headers)
    assert r.status_code == 200
    assert r.json()["installed"] is True
    r = client.post("/api/plugins/marketplace/no-such/install", json={}, headers=headers)
    assert r.status_code == 404


def test_marketplace_endpoints_require_auth(client: TestClient):
    r = client.get("/api/plugins/marketplace")
    assert r.status_code in (401, 403)
    r = client.post("/api/plugins/marketplace/x/install", json={})
    assert r.status_code in (401, 403)
