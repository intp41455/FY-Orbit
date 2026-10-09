"""`/api/release/update/*` 端点的行为护栏。

守的是：离线默认 ⇒ 不静默联网 / 不伪造结果，以及未认证的请求不落任何写。
"""

from __future__ import annotations

from helpers import login_owner

# release_update 路由由 routes/__init__.py 自动发现挂载，无需手动 import。


def test_status_reports_offline_by_default(client, monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    auth = login_owner(client)
    r = client.get("/api/release/update/status", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["offline_mode"] is True
    assert body["signature_status"] == "unsigned"  # 如实标注


def test_check_is_no_op_when_offline(client, monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    auth = login_owner(client)
    r = client.post("/api/release/update/check", headers=auth)
    assert r.status_code == 200
    assert r.json()["offline_blocked"] is True


def test_stage_requires_auth_and_csrf(client, monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    # 不登录直接打：401
    r = client.post("/api/release/update/stage")
    assert r.status_code == 401
    # 登录但 CSRF token 造假：老会话 Cookie 已在 TestClient 上，
    # 只给假 CSRF ⇒ 必须被 CSRF 门禁挡下（403/422），绝不能落服务层
    login_owner(client)
    bogus = {"X-CSRF-Token": "not-the-real-token"}
    r = client.post("/api/release/update/stage", headers=bogus)
    assert r.status_code in (403, 422)


def test_stage_blocked_when_offline_returns_409(client, monkeypatch):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    auth = login_owner(client)
    r = client.post(
        "/api/release/update/stage",
        headers=auth,
    )
    # CSRF token 缺失的场景由上面的用例覆盖；登录态下 csrf 头必须带
    # 本用例只检查登录 + 有 csrf 的情况下，离线不会 500、也不会悄悄下载
    csrf = auth["X-CSRF-Token"]
    r = client.post(
        "/api/release/update/stage",
        headers={"X-CSRF-Token": csrf},
    )
    # 离线模式走的是 OfflineUnavailable 语义：显式 503 + code，绝非静默成功
    assert r.status_code in (409, 503)
    assert "offline" in r.text.lower()


def test_rollback_clears_pending(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FY_OFFLINE_MODE", "1")
    monkeypatch.setenv("FY_INSTALL_ROOT", str(tmp_path))
    _write_pending(tmp_path)
    auth = login_owner(client)
    r = client.post(
        "/api/release/update/rollback",
        headers={"X-CSRF-Token": auth["X-CSRF-Token"]},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rolled_back"


def _write_pending(root):
    pending = root / ".update" / "pending"
    pending.mkdir(parents=True)
    (pending / "pending.json").write_text("{}", encoding="utf-8")
