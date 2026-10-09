"""W9 资产库 HTTP 层单测（含图片/TTS 通道）。

覆盖任务书验收清单：

* 未配任何 key：音乐模块**真实**产出 WAV（可播放字节、raw 端点取回、挂 BGM）；
  图片模块诚实 503「未接入生成服务」，绝不返回占位图；
* 配 key（``httpx.MockTransport`` 打桩，不打真网）后生成图片 → 入库 → 缩略图 URL 可取回
  → 挂小屋墙 → ``/api/assets/cabin/mounted?role=wall`` 能读到；
* 跨 owner 拿不到他人资产 raw（404）；路径穿越被拒；
* 删除资产文件与记录级联干净；
* 鉴权：未登录 401、缺 CSRF 403；超限 413；raw 端点带 nosniff + inline。
* 单价未知（云端端点 + 未设 FY_IMAGE_PRICE_USD）→ 拒绝调用（不得按零费用放行）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.models import AuditEvent
from find_yourself.db.types import TZDateTime
from find_yourself.services.assets import Asset, forget_credentials, set_credentials

LOCAL_TOKEN = "dev-token-secret-w9"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
PNG_B64 = base64.b64encode(PNG_BYTES).decode()


def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=__import__("datetime").timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture(autouse=True)
def _clean_credentials():
    forget_credentials("image")
    forget_credentials("tts")
    yield
    forget_credentials("image")
    forget_credentials("tts")


@pytest.fixture()
def assets_root(tmp_path: Path) -> Path:
    target = tmp_path / "assets"
    target.mkdir()
    return target


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker, assets_root) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        owner_id="owner",
        assets_dir=str(assets_root),
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


def _upload(client: TestClient, headers: dict, name: str, kind: str, data: bytes):
    return client.post(
        f"/api/assets?name={name}&kind={kind}",
        content=data,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )


# --------------------------------------------------------------------------- #
# 上传 / 列表 / raw / 删除
# --------------------------------------------------------------------------- #
def test_upload_then_raw_returns_bytes(client, headers):
    r = _upload(client, headers, "壁纸.png", "image", PNG_BYTES)
    assert r.status_code == 200, r.text
    asset = r.json()["asset"]
    assert asset["kind"] == "image" and asset["mime"] == "image/png"

    raw = client.get(asset["raw_url"])
    assert raw.status_code == 200
    assert raw.content == PNG_BYTES
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.headers["content-disposition"].startswith("inline;")
    assert raw.headers["x-content-type-options"] == "nosniff"


def test_raw_url_never_contains_disk_path(client, headers):
    asset = _upload(client, headers, "a.png", "image", PNG_BYTES).json()["asset"]
    assert ":" not in asset["raw_url"]
    assert asset["storage_rel"].startswith("owner/")


def test_list_reports_limits_and_kinds(client, headers):
    _upload(client, headers, "a.png", "image", PNG_BYTES)
    body = client.get("/api/assets").json()
    assert body["count"] == 1
    assert body["kinds"] == ["image", "audio", "music", "doc"]
    assert body["max_bytes_by_kind"]["image"] == 10 * 1024 * 1024
    assert body["max_bytes_by_kind"]["music"] == 20 * 1024 * 1024
    assert body["max_bytes_by_kind"]["doc"] == 50 * 1024 * 1024


def test_oversize_image_returns_413(client, headers):
    r = _upload(client, headers, "big.png", "image", b"\x00" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "asset_too_large"
    assert client.get("/api/assets").json()["count"] == 0


def test_unknown_kind_is_422(client, headers):
    r = _upload(client, headers, "clip.mp4", "video", b"\x00" * 8)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "asset_kind_invalid"


def test_delete_cascades_file_and_row(client, headers, session_maker, assets_root):
    asset = _upload(client, headers, "a.png", "image", PNG_BYTES).json()["asset"]
    assert list((assets_root / "owner").iterdir())
    d = client.delete(f"/api/assets/{asset['id']}", headers=headers)
    assert d.status_code == 200
    assert d.json()["file_removed"] is True
    assert list((assets_root / "owner").iterdir()) == []
    assert session_maker().execute(select(Asset)).scalars().all() == []
    actions = [e.action for e in session_maker().execute(select(AuditEvent)).scalars().all()]
    assert "asset.deleted" in actions


# --------------------------------------------------------------------------- #
# 音乐通道（未配 key 也必须真实产出）
# --------------------------------------------------------------------------- #
def test_music_module_produces_real_playable_wav_without_any_key(client, headers):
    r = client.post(
        "/api/assets/generate/music", json={"mood": "calm", "seconds": 2}, headers=headers
    )
    assert r.status_code == 200, r.text
    asset = r.json()["asset"]
    assert asset["kind"] == "music" and asset["mime"] == "audio/wav"
    assert asset["meta"]["provider"] == "local_synth"
    assert asset["meta"]["engine"] == "chiptune-synth-1.0"

    raw = client.get(asset["raw_url"])
    assert raw.status_code == 200
    assert raw.content[:4] == b"RIFF" and raw.content[8:12] == b"WAVE"
    assert len(raw.content) > 44_000  # 真的有音频数据，不是占位


def test_music_can_be_mounted_as_cabin_bgm(client, headers):
    asset = client.post(
        "/api/assets/generate/music", json={"mood": "bright", "seconds": 2}, headers=headers
    ).json()["asset"]
    m = client.put(f"/api/assets/{asset['id']}/mount", json={"role": "bgm"}, headers=headers)
    assert m.status_code == 200
    mounted = client.get("/api/assets/cabin/mounted?role=bgm").json()
    assert mounted["count"] == 1
    assert mounted["assets"][0]["id"] == asset["id"]


def test_music_channel_status_is_honestly_configured(client, headers):
    body = client.get("/api/assets/channels").json()
    by_channel = {c["channel"]: c for c in body["channels"]}
    assert by_channel["image"]["configured"] is False
    assert "未接入" in by_channel["image"]["detail"]
    assert by_channel["tts"]["configured"] is False
    assert body["music"]["configured"] is True
    assert body["music"]["provider"] == "local_synth"
    assert {m["id"] for m in body["moods"]} >= {"calm", "bright", "dreamy"}


# --------------------------------------------------------------------------- #
# 图片通道（未配置诚实 503；配置后真实出图）
# --------------------------------------------------------------------------- #
def test_image_channel_503_when_not_configured(client, headers, monkeypatch):
    monkeypatch.delenv("FY_IMAGE_BASE_URL", raising=False)
    r = client.post("/api/assets/generate/image", json={"prompt": "一只猫"}, headers=headers)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "image_provider_not_configured"
    assert client.get("/api/assets").json()["count"] == 0


def test_configure_channel_requires_base_url_and_never_echoes_key(client, headers):
    bad = client.post(
        "/api/assets/channels/image/configure", json={"base_url": ""}, headers=headers
    )
    assert bad.status_code == 422

    ok = client.post(
        "/api/assets/channels/image/configure",
        json={"base_url": "http://127.0.0.1:7860/v1", "api_key": "sk-secret", "model": "sdxl"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["configured"] is True
    assert ok.json()["source"] == "credentials"
    assert "sk-secret" not in ok.text


def test_generate_image_with_mock_provider_stores_real_png(client, headers, monkeypatch):
    """用 httpx.MockTransport 打桩（不打真网）验证生成 → 入库 → raw 可取回。"""
    from find_yourself.api.routes import assets as assets_route

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]})

    original_init = assets_route.ImageGenerationService.__init__

    def patched_init(self, **kwargs):  # noqa: ANN001
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, **kwargs)

    monkeypatch.setattr(assets_route.ImageGenerationService, "__init__", patched_init)
    set_credentials(
        "image", {"base_url": "http://127.0.0.1:7860/v1", "api_key": "sk-x", "model": "sdxl"}
    )

    r = client.post(
        "/api/assets/generate/image",
        json={"prompt": "像素风小屋", "size": "512x512"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    asset = r.json()["asset"]
    assert seen["url"] == "http://127.0.0.1:7860/v1/images/generations"
    assert seen["auth"] == "Bearer sk-x"
    assert asset["kind"] == "image" and asset["mime"] == "image/png"
    assert asset["meta"]["model"] == "sdxl"
    assert asset["meta"]["prompt_chars"] == len("像素风小屋")
    assert asset["meta"]["provider"] == "openai_compatible_images"
    # 本机端点 → 零成本是事实，且标注了来源
    assert asset["meta"]["cost_usd"] == "0"

    raw = client.get(asset["raw_url"])
    assert raw.status_code == 200 and raw.content == PNG_BYTES


def test_generate_image_rejects_empty_prompt(client, headers):
    set_credentials("image", {"base_url": "http://127.0.0.1:7860/v1", "api_key": ""})
    r = client.post("/api/assets/generate/image", json={"prompt": "   "}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "asset_prompt_required"


def test_cloud_price_unknown_refuses_to_charge_zero(client, headers, monkeypatch):
    """冻结契约 §7：云端单价未知必须拒绝调用，而不是按 0 记账。"""
    from find_yourself.api.routes import assets as assets_route

    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:  # 不该被调用
        calls.append(str(request.url))
        return httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]})

    original_init = assets_route.ImageGenerationService.__init__

    def patched_init(self, **kwargs):  # noqa: ANN001
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, **kwargs)

    monkeypatch.setattr(assets_route.ImageGenerationService, "__init__", patched_init)
    monkeypatch.delenv("FY_IMAGE_PRICE_USD", raising=False)
    set_credentials("image", {"base_url": "https://api.example.com/v1", "api_key": "sk-y"})

    r = client.post("/api/assets/generate/image", json={"prompt": "猫"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "asset_price_unknown"
    assert calls == []  # 价格门槛之前就拦住，没有出网


def test_upstream_error_is_surfaced_not_faked(client, headers, monkeypatch):
    from find_yourself.api.routes import assets as assets_route

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    original_init = assets_route.ImageGenerationService.__init__

    def patched_init(self, **kwargs):  # noqa: ANN001
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, **kwargs)

    monkeypatch.setattr(assets_route.ImageGenerationService, "__init__", patched_init)
    set_credentials("image", {"base_url": "http://127.0.0.1:7860/v1", "api_key": ""})

    r = client.post("/api/assets/generate/image", json={"prompt": "猫"}, headers=headers)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "image_provider_http_error"
    assert client.get("/api/assets").json()["count"] == 0


def test_speech_channel_503_when_not_configured(client, headers):
    r = client.post("/api/assets/generate/speech", json={"text": "你好"}, headers=headers)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "tts_not_configured"


# --------------------------------------------------------------------------- #
# 小屋联动 + 隔离 + 鉴权
# --------------------------------------------------------------------------- #
def test_image_can_be_mounted_on_cabin_wall_and_served(client, headers):
    asset = _upload(client, headers, "挂画.png", "image", PNG_BYTES).json()["asset"]
    assert client.put(
        f"/api/assets/{asset['id']}/mount", json={"role": "wall"}, headers=headers
    ).status_code == 200
    mounted = client.get("/api/assets/cabin/mounted?role=wall").json()
    assert mounted["count"] == 1
    served = client.get(mounted["assets"][0]["raw_url"])
    assert served.status_code == 200 and served.content == PNG_BYTES


def test_mount_role_validation_via_api(client, headers):
    music = client.post(
        "/api/assets/generate/music", json={"mood": "calm", "seconds": 2}, headers=headers
    ).json()["asset"]
    r = client.put(f"/api/assets/{music['id']}/mount", json={"role": "wall"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "mount_kind_mismatch"


def test_cross_owner_cannot_read_others_raw(client, headers, session_maker):
    """真·跨 owner：owner-2 的会话拿不到 owner-1 的字节（404，也不泄露存在性）。"""
    asset = _upload(client, headers, "secret.png", "image", PNG_BYTES).json()["asset"]

    # 直接在库里给另一个 owner 开一条真实会话（服务端签发，非请求体自封）。
    from find_yourself.services.audit import AuditService
    from find_yourself.services.auth import AuthService

    session = session_maker()
    auth = AuthService(session, AuditService(session), environment="test")
    other = auth.create_owner_session("owner-2")
    session.commit()
    other_cookie = other.plaintext_token
    session.close()

    owner_cookie = client.cookies.get("fy_session")
    client.cookies.set("fy_session", other_cookie)
    try:
        r = client.get(f"/api/assets/{asset['id']}/raw")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "asset_not_found"
        assert client.get("/api/assets").json()["count"] == 0
    finally:
        client.cookies.set("fy_session", owner_cookie)

    assert client.get(f"/api/assets/{asset['id']}/raw").status_code == 200


def test_owner_id_is_never_client_controllable(client, headers):
    """query 里的 owner_id 无授权效力（冻结契约 §2：客户端提交的 owner_id 不具授权效力）。"""
    r = client.post(
        "/api/assets?name=a.png&kind=image&owner_id=someone-else",
        content=PNG_BYTES,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["asset"]["owner_id"] == "owner"


def test_endpoints_require_authentication(client):
    assert client.get("/api/assets").status_code == 401
    assert client.get("/api/assets/channels").status_code == 401
    assert client.get("/api/assets/x/raw").status_code == 401


def test_writes_require_csrf(client, headers):
    """已登录（有会话 Cookie）但不带 X-CSRF-Token → 403，而不是放行。"""
    assert client.get("/api/assets").status_code == 200  # 先确认会话已建立
    r = client.post(
        "/api/assets?name=a.png&kind=image",
        content=PNG_BYTES,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 403
    assert client.post("/api/assets/generate/music", json={"mood": "calm"}).status_code == 403


def test_missing_asset_is_404_with_stable_code(client, headers):
    r = client.get("/api/assets/does-not-exist/raw")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "asset_not_found"
