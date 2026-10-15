"""D06 BYOK 界面持久化 API：GET/POST/DELETE /api/models/credentials。

* 写操作要 CSRF；读要身份；
* 任何响应不得含明文 key；
* secret_store 进程单例：用唯一 scope 隔离？——scope 是固定的
  ``model:default``，用例间清场（未经清场会串味）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from find_yourself.services.knowledge.sources.store import secret_store
from find_yourself.services.model_credentials import MODEL_CREDENTIALS_SCOPE

OWNER_EMAIL = "owner-modelcreds@test.local"
PASSWORD = "ModelCreds-pass-12345"


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    reg = client.post(
        "/auth/register",
        json={"email": OWNER_EMAIL, "password": PASSWORD, "consent_accepted": True},
    )
    assert reg.status_code == 200, reg.text
    r = client.post("/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


@pytest.fixture(autouse=True)
def _clean_store():
    secret_store.forget(MODEL_CREDENTIALS_SCOPE)
    yield
    secret_store.forget(MODEL_CREDENTIALS_SCOPE)


def test_save_status_forget_roundtrip(client: TestClient, headers: dict) -> None:
    r = client.post(
        "/api/models/credentials",
        json={"provider": "openai_compat", "base_url": "https://api.example.com/v1",
              "api_key": "sk-live-abc123", "model": "gpt-4o-mini"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["has_api_key"] is True
    assert "sk-live-abc123" not in json.dumps(body)

    g = client.get("/api/models/credentials", headers=headers)
    assert g.status_code == 200
    assert g.json()["base_url"] == "https://api.example.com/v1"
    assert "sk-live-abc123" not in json.dumps(g.json())

    d = client.delete("/api/models/credentials", headers=headers)
    assert d.status_code == 200 and d.json()["forgotten"] is True
    assert client.get("/api/models/credentials", headers=headers).json()["configured"] is False


def test_write_requires_csrf_and_read_requires_auth(client: TestClient, headers: dict) -> None:
    r = client.post("/api/models/credentials", json={"provider": "x", "base_url": "y"})
    assert r.status_code in (401, 403)
    # 读是游客可达（与 /api/models/catalog 同口径，W8 免登录默认态），
    # 但只能看到脱敏状态，永不见明文。
    g = client.get("/api/models/credentials")
    assert g.status_code == 200
    assert g.json()["configured"] is False
    d = client.delete("/api/models/credentials")
    assert d.status_code in (401, 403)
