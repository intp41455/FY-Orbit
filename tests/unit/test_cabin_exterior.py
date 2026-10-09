"""Unit tests: 室外家具移除黑名单 (GET/PUT/DELETE /api/cabin/exterior/{house_id}).

覆盖的验收点：

* 空态：从未删过家具时 GET 返回 ``removed_ids=[]`` 与 ``version=0``；
* 写入 / 读回：PUT 成功后 GET 返回同一份黑名单；
* 乐观锁：过期 expected_version 返回 409；版本正确时递增；
* 白名单：不在 EXTERIOR_REMOVABLE 的 id 显式 422，不静默丢弃；
* owner 隔离由 service 层按 owner_id 过滤（见test_cabin_interior 同款约定）；
* 复位：DELETE 清空黑名单，全部室外家具回来；
* 与前端 ``DEFAULT_PLACED_FURNITURE`` 的一致性（防止「画面能点但存不下来」）。
"""

from __future__ import annotations

import re
from datetime import timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime

LOCAL_TOKEN = "dev-token-secret-w1"


def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    """本地 dev-token 门禁要求 loopback 对端。"""

    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker, tmp_path) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    return create_app(session_maker=session_maker, settings=settings)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def _put(client: TestClient, headers: dict, house: str, ids, version: int):
    return client.put(
        f"/api/cabin/exterior/{house}",
        json={"removed_ids": ids, "expected_version": version},
        headers=headers,
    )


# ----------------------------------------------------------------------
# 空态与读写
# ----------------------------------------------------------------------
def test_default_state_is_empty_blacklist(client: TestClient, headers: dict):
    r = client.get("/api/cabin/exterior/cabin", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_ids"] == []
    assert body["version"] == 0


def test_put_then_get_roundtrip(client: TestClient, headers: dict):
    r = _put(client, headers, "cabin", ["b4_single_bed"], 0)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1

    g = client.get("/api/cabin/exterior/cabin", headers=headers)
    assert g.status_code == 200, g.text
    assert g.json()["removed_ids"] == ["b4_single_bed"]
    assert g.json()["version"] == 1


def test_removed_ids_are_deduplicated_and_sorted(client: TestClient, headers: dict):
    r = _put(client, headers, "cabin", ["b4_single_bed", "b4_rug_small", "b4_single_bed"], 0)
    assert r.status_code == 200, r.text
    assert r.json()["removed_ids"] == ["b4_rug_small", "b4_single_bed"]


def test_house_isolation(client: TestClient, headers: dict):
    """不同房屋模板的黑名单互相独立。"""
    assert _put(client, headers, "cabin", ["b4_writing_desk"], 0).status_code == 200
    g = client.get("/api/cabin/exterior/villa", headers=headers)
    assert g.json()["removed_ids"] == []


# ----------------------------------------------------------------------
# 乐观锁
# ----------------------------------------------------------------------
def test_stale_version_is_rejected_with_409(client: TestClient, headers: dict):
    assert _put(client, headers, "cabin", ["b4_writing_desk"], 0).status_code == 200
    r = _put(client, headers, "cabin", ["b4_stool"], 0)
    assert r.status_code == 409, r.text


def test_correct_version_increments(client: TestClient, headers: dict):
    assert _put(client, headers, "cabin", ["b4_writing_desk"], 0).json()["version"] == 1
    assert _put(client, headers, "cabin", ["b4_writing_desk", "b4_stool"], 1).json()["version"] == 2


def test_create_with_non_zero_expected_version_conflicts(client: TestClient, headers: dict):
    """存档不存在时只有 expected_version=0 才允许创建。"""
    r = _put(client, headers, "cabin", ["b4_writing_desk"], 3)
    assert r.status_code == 409, r.text


# ----------------------------------------------------------------------
# 白名单
# ----------------------------------------------------------------------
def test_unknown_furniture_id_is_rejected(client: TestClient, headers: dict):
    """未知 id 必须显式报错，不能静默丢弃后假装保存成功。"""
    r = _put(client, headers, "cabin", ["not_a_real_piece"], 0)
    assert r.status_code == 422, r.text


def test_unknown_house_id_is_404(client: TestClient, headers: dict):
    r = client.get("/api/cabin/exterior/not_a_house", headers=headers)
    assert r.status_code == 404, r.text


def test_extra_field_is_rejected(client: TestClient, headers: dict):
    r = client.put(
        "/api/cabin/exterior/cabin",
        json={"removed_ids": [], "expected_version": 0, "oops": 1},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# ----------------------------------------------------------------------
# 复位
# ----------------------------------------------------------------------
def test_delete_clears_blacklist(client: TestClient, headers: dict):
    assert _put(client, headers, "cabin", ["b4_writing_desk"], 0).status_code == 200
    d = client.delete("/api/cabin/exterior/cabin?expected_version=1", headers=headers)
    assert d.status_code == 200, d.text
    assert d.json() == {"house_id": "cabin", "removed_ids": [], "version": 0}
    assert client.get("/api/cabin/exterior/cabin", headers=headers).json()["removed_ids"] == []


def test_delete_with_stale_version_conflicts(client: TestClient, headers: dict):
    assert _put(client, headers, "cabin", ["b4_writing_desk"], 0).status_code == 200
    d = client.delete("/api/cabin/exterior/cabin?expected_version=0", headers=headers)
    assert d.status_code == 409, d.text


def test_delete_on_missing_row_is_noop(client: TestClient, headers: dict):
    d = client.delete("/api/cabin/exterior/cabin?expected_version=0", headers=headers)
    assert d.status_code == 200, d.text
    assert d.json()["version"] == 0


# ----------------------------------------------------------------------
# 与前端默认清单的一致性
# ----------------------------------------------------------------------
def test_removable_set_is_subset_of_frontend_default_placement() -> None:
    """后端可移除白名单必须是前端默认室外家具清单的子集。

    否则用户会遇到「画面里能点掉、但存不下来」的不一致。
    """
    from find_yourself.services.cabin_interior import EXTERIOR_REMOVABLE

    root = Path(__file__).resolve().parents[2]
    src = (root / "web" / "src" / "components" / "cabin" / "cabinBuildArt.ts").read_text(
        encoding="utf-8"
    )

    m = re.search(r"DEFAULT_PLACED_FURNITURE[^=]*=\s*\[(.*?)\]", src, re.S)
    assert m, "前端 DEFAULT_PLACED_FURNITURE 未找到（前端结构变了？）"
    frontend_ids = set(
        re.findall(r"furniture_id:\s*['\"]([^'\"]+)['\"]", m.group(1))
    )

    assert frontend_ids, "前端清单解析为空"
    assert EXTERIOR_REMOVABLE, "后端可移除清单不应为空"
    assert EXTERIOR_REMOVABLE <= frontend_ids, (
        f"后端允许移除但前端默认清单里没有：{sorted(EXTERIOR_REMOVABLE - frontend_ids)}"
    )
