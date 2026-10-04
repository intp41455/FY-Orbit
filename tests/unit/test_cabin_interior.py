"""Unit tests: W1 数码小屋室内布置 — 家具布局持久化与白名单校验.

覆盖任务书第 5 节的验收点：

* 布局 CRUD：GET 默认态 → PUT 创建 → GET 读回 → PUT 更新 → DELETE 复位；
* owner 隔离：另一账号读不到（404 语义），服务身份写入被拒（403）；
* 家具白名单：注册表外 id 直接 422，不会被静默丢弃后「假装保存成功」；
* version 乐观锁：过期版本 409，版本正确时递增；
* 体积/容量上限：>64KB 与 >60 件均被拒；
* 坐标按挂载方式夹取（地板/墙/天花板各自合法带）；
* 注册表与前端 ``furnitureCatalog.ts`` 的一致性（id 集合逐项比对）。
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

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401

LOCAL_TOKEN = "dev-token-secret-w1"


# --- Test-only SQLite TZ shim (same as tests/unit/test_preview_sources.py) ---
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):  # the local dev-token gate requires a loopback peer
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
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


def _layout(*items: dict) -> dict:
    return {"items": list(items)}


def _item(fid: str = "bed", x: int = 2, y: int = 6, **kw) -> dict:
    base = {
        "id": kw.pop("id", f"i-{fid}-{x}-{y}"),
        "furnitureId": fid,
        "x": x,
        "y": y,
        "flipped": False,
        "colorway": 0,
        "z": 0,
    }
    base.update(kw)
    return base


def _put(client: TestClient, headers: dict, house: str, layout: dict, version: int):
    return client.put(
        f"/api/cabin/interiors/{house}",
        json={"layout": layout, "expected_version": version},
        headers=headers,
    )


# ----------------------------------------------------------------------
# 家具注册表（GET /api/cabin/furniture）
# ----------------------------------------------------------------------
def test_furniture_catalog_endpoint_lists_whole_registry(client: TestClient, headers: dict):
    r = client.get("/api/cabin/furniture", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 12  # 任务书要求 ≥12 种家具
    assert body["max_items"] == 60
    assert body["max_layout_bytes"] == 64 * 1024
    ids = {i["id"] for i in body["items"]}
    assert {"bed", "bookshelf", "fish_tank", "picture_frame", "rug_large"} <= ids
    # 网格尺寸随注册表下发，前后端共用一套契约
    bed = next(i for i in body["items"] if i["id"] == "bed")
    assert bed["size_cells"] == {"w": 3, "h": 2}
    assert bed["mount"] == "floor"
    frame = next(i for i in body["items"] if i["id"] == "picture_frame")
    assert frame["mount"] == "wall"


def test_catalog_matches_frontend_registry() -> None:
    """后端白名单必须与前端 FURNITURE_CATALOG 的 id 集合完全一致。

    任何一侧新增/删除家具而忘了同步另一侧，都会让「后端接受但前端画不出」
    或「前端能拖但后端拒绝」的裂缝。这里直接读前端源码做集合比对。
    """
    from find_yourself.services.cabin_interior import FURNITURE_CATALOG

    root = Path(__file__).resolve().parents[2]
    ts = (root / "web" / "src" / "components" / "cabin" / "interior" / "furnitureCatalog.ts").read_text(
        encoding="utf-8"
    )
    block = ts[ts.index("export const FURNITURE_CATALOG") : ts.index("/* ---", ts.index("export const FURNITURE_CATALOG"))]
    frontend_ids = set(re.findall(r"\{\s*id:\s*'([a-z_]+)'", block))
    assert frontend_ids, "未能从前端源码解析出家具 id（源码结构变了？）"
    assert frontend_ids == set(FURNITURE_CATALOG), (
        f"前后端家具注册表不一致；仅前端有={frontend_ids - set(FURNITURE_CATALOG)}，"
        f"仅后端有={set(FURNITURE_CATALOG) - frontend_ids}"
    )


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------
def test_get_unset_layout_reports_defaulted(client: TestClient, headers: dict):
    r = client.get("/api/cabin/interiors/cabin", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 诚实语义：后端不编造默认布局，只回报「还没有」
    assert body["defaulted"] is True
    assert body["version"] == 0
    assert body["layout"]["items"] == []


def test_put_creates_then_get_returns_layout(client: TestClient, headers: dict):
    r = _put(client, headers, "cabin", _layout(_item("bed"), _item("bookshelf", x=20, y=4)), 0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["defaulted"] is False
    assert [i["furnitureId"] for i in body["layout"]["items"]] == ["bed", "bookshelf"]

    got = client.get("/api/cabin/interiors/cabin", headers=headers)
    assert got.status_code == 200
    assert got.json()["version"] == 1
    assert len(got.json()["layout"]["items"]) == 2


def test_put_updates_and_increments_version(client: TestClient, headers: dict):
    assert _put(client, headers, "villa", _layout(_item("bed")), 0).status_code == 200
    r = _put(client, headers, "villa", _layout(_item("bed"), _item("plant", x=8, y=5)), 1)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert len(r.json()["layout"]["items"]) == 2


def test_layouts_are_isolated_per_house_template(client: TestClient, headers: dict):
    _put(client, headers, "cabin", _layout(_item("bed", x=1, y=6)), 0)
    _put(client, headers, "castle", _layout(_item("stove", x=5, y=4), _item("bed", x=2, y=5)), 0)
    cabin = client.get("/api/cabin/interiors/cabin", headers=headers).json()
    castle = client.get("/api/cabin/interiors/castle", headers=headers).json()
    assert [i["furnitureId"] for i in cabin["layout"]["items"]] == ["bed"]
    assert {i["furnitureId"] for i in castle["layout"]["items"]} == {"stove", "bed"}
    assert castle["version"] == 1


def test_delete_resets_to_default(client: TestClient, headers: dict):
    _put(client, headers, "cave", _layout(_item("bed")), 0)
    r = client.request(
        "DELETE", "/api/cabin/interiors/cave", params={"expected_version": 1}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    after = client.get("/api/cabin/interiors/cave", headers=headers).json()
    assert after["defaulted"] is True
    assert after["version"] == 0


# ----------------------------------------------------------------------
# version 乐观锁 → 409
# ----------------------------------------------------------------------
def test_stale_version_conflicts_with_409(client: TestClient, headers: dict):
    _put(client, headers, "cabin", _layout(_item("bed")), 0)
    stale = _put(client, headers, "cabin", _layout(_item("bed"), _item("chair", x=9, y=6)), 0)
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "cabin_version_conflict"
    # 冲突后存储内容未被覆盖
    assert len(client.get("/api/cabin/interiors/cabin", headers=headers).json()["layout"]["items"]) == 1


def test_version_conflict_when_client_expects_existing_but_absent(client: TestClient, headers: dict):
    r = _put(client, headers, "snowcave", _layout(_item("bed")), 5)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "cabin_version_conflict"


def test_delete_with_stale_version_conflicts(client: TestClient, headers: dict):
    _put(client, headers, "bunker", _layout(_item("bed")), 0)
    r = client.request(
        "DELETE", "/api/cabin/interiors/bunker", params={"expected_version": 99}, headers=headers
    )
    assert r.status_code == 409, r.text


# ----------------------------------------------------------------------
# 家具白名单 + 坐标/体积校验 → 422
# ----------------------------------------------------------------------
def test_unknown_furniture_id_is_rejected(client: TestClient, headers: dict):
    r = _put(client, headers, "cabin", _layout(_item("gold_throne")), 0)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_unknown_furniture"
    # 拒绝即未落库
    assert client.get("/api/cabin/interiors/cabin", headers=headers).json()["defaulted"] is True


def test_non_integer_coordinates_rejected(client: TestClient, headers: dict):
    r = _put(client, headers, "cabin", _layout(_item("bed", x=2.5, y=6)), 0)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_invalid_coord"


def test_duplicate_item_ids_rejected(client: TestClient, headers: dict):
    r = _put(
        client,
        headers,
        "cabin",
        _layout(_item("bed", id="dup"), _item("chair", x=9, y=6, id="dup")),
        0,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_duplicate_item_id"


def test_too_many_items_rejected(client: TestClient, headers: dict):
    items = [_item("chair", x=1 + (i % 20), y=1 + (i // 20), id=f"c{i}") for i in range(61)]
    r = _put(client, headers, "cabin", _layout(*items), 0)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_layout_too_many_items"


def test_oversized_layout_rejected(client: TestClient, headers: dict):
    # 合法 id 但体积超 64KB —— 必须明确报错，不能截断后假装保存
    items = [_item("chair", x=1, y=1, id=f"big{i}", colorway=0) for i in range(60)]
    payload = _layout(*items)
    payload["padding"] = "x" * (70 * 1024)
    r = _put(client, headers, "cabin", payload, 0)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_layout_too_large"


def test_unknown_house_template_rejected(client: TestClient, headers: dict):
    r = _put(client, headers, "pyramid", _layout(_item("bed")), 0)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "cabin_unknown_house"


def test_coordinates_clamped_per_mount(client: TestClient, headers: dict):
    """地板家具夹到地面带；墙面/天花板家具夹到各自合法带。"""
    r = _put(
        client,
        headers,
        "cabin",
        _layout(
            _item("bed", x=-5, y=99, id="floor-clamp"),
            _item("picture_frame", x=1, y=9, id="wall-clamp"),
            _item("ceiling_lamp", x=1, y=8, id="ceil-clamp"),
        ),
        0,
    )
    assert r.status_code == 200, r.text
    items = {i["id"]: i for i in r.json()["layout"]["items"]}
    assert items["floor-clamp"]["x"] == 0 and items["floor-clamp"]["y"] == 8
    assert 0 <= items["wall-clamp"]["y"] <= 3
    assert 0 <= items["ceil-clamp"]["y"] <= 1


# ----------------------------------------------------------------------
# owner 隔离与鉴权
# ----------------------------------------------------------------------
def test_other_owner_cannot_see_or_overwrite_layout(app: FastAPI, client: TestClient, headers: dict):
    _put(client, headers, "cabin", _layout(_item("bed", x=1, y=6)), 0)
    first_owner = _owner_of(client, headers)

    # 第二个真实账号必须用**独立的 cookie jar**（独立 TestClient），
    # 否则注册会覆盖同一 jar 里的会话Cookie，测的就不是两个 owner 了。
    with TestClient(_force_loopback(app)) as other_client:
        reg = other_client.post(
            "/auth/register",
            json={
                "email": "other-owner@example.com",
                "password": "longenough1",
                "consent_accepted": True,
            },
        )
        assert reg.status_code == 200, reg.text
        other = {"X-CSRF-Token": reg.json()["csrf_token"]}
        assert reg.json()["owner_id"] != first_owner
        assert _owner_of(other_client, other) == reg.json()["owner_id"]

        # 读不到：视为「不存在」，不泄露他人数据是否存在
        got = other_client.get("/api/cabin/interiors/cabin", headers=other)
        assert got.status_code == 200
        assert got.json()["defaulted"] is True
        assert got.json()["layout"]["items"] == []

        # 写自己的（互不影响）
        mine = _put(other_client, other, "cabin", _layout(_item("stove", x=6, y=4)), 0)
        assert mine.status_code == 200, mine.text
        assert mine.json()["version"] == 1

    # 原 owner 的布置原样保留，且未看到对方的 stove
    still = client.get("/api/cabin/interiors/cabin", headers=headers).json()
    assert [i["furnitureId"] for i in still["layout"]["items"]] == ["bed"]


def _owner_of(client: TestClient, headers: dict) -> str:
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json().get("owner_id", "")


def test_unauthenticated_requests_rejected(client: TestClient):
    r = client.get("/api/cabin/interiors/cabin")
    assert r.status_code in (401, 403)
    w = client.put(
        "/api/cabin/interiors/cabin",
        json={"layout": _layout(_item("bed")), "expected_version": 0},
    )
    assert w.status_code in (401, 403)


def test_write_without_csrf_header_rejected(client: TestClient, headers: dict):
    """已登录但不带 CSRF 头的写请求必须被拒（会话 cookie 本身不够）。"""
    r = client.put(
        "/api/cabin/interiors/cabin",
        json={"layout": _layout(_item("bed")), "expected_version": 0},
    )
    assert r.status_code in (401, 403)


def test_service_identity_cannot_write_interiors(client: TestClient, session_maker) -> None:
    """服务身份（bearer）对 owner 私有资源写入 → 403，绝不代写。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.cabin_interior import CabinInteriorService
    from find_yourself.services.errors import PermissionDenied

    session = session_maker()
    try:
        svc = CabinInteriorService(session)
        actor = Actor.service("worker-1", "worker")
        with pytest.raises(PermissionDenied) as exc:
            svc.put_interior(actor, "cabin", _layout(_item("bed")), 0)
        assert exc.value.http_status == 403
    finally:
        session.close()
