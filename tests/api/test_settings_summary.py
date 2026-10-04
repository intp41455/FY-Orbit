"""主控纠错 2026-10-04：设置页存量 404（GET /api/settings 从未实现）的回归测试。"""

from fastapi.testclient import TestClient


def test_settings_summary_requires_auth(client: TestClient):
    r = client.get("/api/settings")
    assert r.status_code == 401


def test_settings_summary_shape_is_honest(client: TestClient):
    from helpers import login_owner

    headers = login_owner(client)
    r = client.get("/api/settings", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"model_configured", "oidc_configured",
                         "local_dev_token_allowed", "data_domains", "export_status"}
    # 测试环境默认未配模型/未配 OIDC —— 诚实为 False，不谎报已配置
    assert body["model_configured"] is False
    assert body["oidc_configured"] is False
    assert body["local_dev_token_allowed"] is True
    assert body["data_domains"] == ["personal", "work", "shared"]
    assert body["export_status"] is None
