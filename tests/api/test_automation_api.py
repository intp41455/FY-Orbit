"""W10-B · GUI 自动化权限门 HTTP 级测试（主控返工 2026-10-04）。

证明 routes/automation.py 的导入修复与真实路由行为：
  ① 未登录 GET  -> 401
  ② 登录 GET    -> 200 且默认 mode=off（诚实形状）
  ③ PUT 无 CSRF -> 403
  ④ PUT full 无 TTL（带 CSRF）-> 403（门禁语义：full 必须带 TTL）
  ⑤ PUT full+ttl=60 -> 200 + expires_at 合理 + 审计事件落库

不打真网、不碰真鼠标：本测试只走权限门 HTTP surface，不调用 automation 工具。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from find_yourself.db.models import AuditEvent
from find_yourself.services.automation import get_permissions


@pytest.fixture(autouse=True)
def _reset_to_off(client: TestClient):
    """权限门是进程级、文件持久化单例；每个用例前强制回到 off 干净起点。"""
    get_permissions().set_mode("off")
    yield
    get_permissions().set_mode("off")


def test_get_requires_auth(client: TestClient) -> None:
    r = client.get("/api/automation/permissions")
    assert r.status_code == 401


def test_get_logged_in_defaults_to_off(client: TestClient) -> None:
    from helpers import login_owner

    headers = login_owner(client)
    r = client.get("/api/automation/permissions", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "off"
    assert body["expires_at"] is None
    assert body["ttl_remaining_seconds"] is None
    # 诚实形状：带档位列表与诚实文案。
    assert set(body["modes"]) == {"off", "readonly", "safe", "full"}
    assert "默认关闭" in body["honesty_note"]


def test_put_without_csrf_is_forbidden(client: TestClient) -> None:
    from helpers import login_owner

    login_owner(client)  # 登录拿到 cookie，但下面不带 X-CSRF-Token
    r = client.put("/api/automation/permissions", json={"mode": "readonly"})
    assert r.status_code == 403


def test_put_full_without_ttl_is_forbidden(client: TestClient) -> None:
    from helpers import login_owner

    headers = login_owner(client)
    r = client.put("/api/automation/permissions", headers=headers, json={"mode": "full"})
    # 门禁语义：full 必须带 TTL；pydantic 允许缺省，权限门抛 403。
    assert r.status_code == 403, r.text


def test_put_full_with_ttl_writes_audit(
    client: TestClient, session_maker
) -> None:
    from helpers import login_owner

    headers = login_owner(client)
    r = client.put(
        "/api/automation/permissions",
        headers=headers,
        json={"mode": "full", "ttl_seconds": 60},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "full"
    assert body["expires_at"] is not None
    # expires_at 应落在未来约 60s 处（允许少量执行耗时）。
    exp = datetime.fromisoformat(body["expires_at"])
    left = (exp - datetime.now(timezone.utc)).total_seconds()
    assert 50 < left <= 60
    assert body["ttl_remaining_seconds"] is not None
    assert 50 < body["ttl_remaining_seconds"] <= 60

    # 审计事件落主审计链。
    db: Session = session_maker()
    try:
        rows = list(
            db.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "automation.permission_change"
                )
            ).scalars()
        )
        assert rows, "permission change must be audited"
        last = rows[-1]
        assert last.details["mode"] == "full"
        assert last.details["ttl_seconds"] == 60
    finally:
        db.close()
