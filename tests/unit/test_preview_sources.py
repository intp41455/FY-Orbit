"""Unit tests: P1-A 实时预览窗 — 统一预览源注册协议.

Covers the registration protocol over the real HTTP surface:

- static sources: register a workspace-relative html/md file, list, version
  probe (mtime+size hash), content endpoint with the mandatory CSP sandbox +
  nosniff headers, and absolute-path non-exposure;
- path safety: traversal / absolute / escape attempts are rejected by the
  workspace sandbox before any registration happens;
- honesty: unsupported extensions, missing files and unknown sources fail with
  explicit errors — never a faked renderable preview;
- unregister (DELETE) marks the source offline.
"""

from __future__ import annotations

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

LOCAL_TOKEN = "dev-token-secret-p1a"


# --- Test-only SQLite TZ shim (same as tests/api/conftest.py; no core change) -
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


@pytest.fixture()
def ws_id(client: TestClient, headers: dict[str, str], tmp_path: Path) -> str:
    root = tmp_path / "p1a-site"
    root.mkdir()
    (root / "index.html").write_text(
        "<html><body><h1>P1A_OK</h1><script>window.__x=1</script></body></html>", encoding="utf-8"
    )
    (root / "notes.md").write_text("# 计划\n\n- 项一\n", encoding="utf-8")
    (root / "secret.py").write_text("x = 1\n", encoding="utf-8")
    r = client.post(
        "/api/workbench/workspaces",
        json={"project_name": "P1A 预览工程", "authorized_root": str(root)},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _register(client: TestClient, headers: dict[str, str], ws_id: str, path: str):
    return client.post(
        f"/api/workbench/workspaces/{ws_id}/preview-sources",
        json={"kind": "static", "path": path},
        headers=headers,
    )


# ----------------------------------------------------------------------
# Registration + listing
# ----------------------------------------------------------------------
def test_register_static_source_and_list(client: TestClient, headers: dict[str, str], ws_id: str):
    r = _register(client, headers, ws_id, "index.html")
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["kind"] == "static"
    assert src["path"] == "index.html"  # workspace-relative, never absolute
    assert src["media_type"] == "text/html"
    assert src["version"]  # mtime+size hash
    assert src["content_url"].endswith("/content")
    assert "tmp" not in src["path"] and "\\" not in src["path"]

    lst = client.get(f"/api/workbench/workspaces/{ws_id}/preview-sources", headers=headers)
    assert lst.status_code == 200
    assert lst.json()["count"] == 1
    assert lst.json()["items"][0]["id"] == src["id"]


def test_register_md_source_is_plain_text_honest(client: TestClient, headers: dict[str, str], ws_id: str):
    r = _register(client, headers, ws_id, "notes.md")
    assert r.status_code == 201, r.text
    assert r.json()["media_type"] == "text/plain"  # md 原文直出，不伪装渲染


def test_register_unsupported_extension_rejected(client: TestClient, headers: dict[str, str], ws_id: str):
    r = _register(client, headers, ws_id, "secret.py")
    assert r.status_code == 422


def test_register_missing_file_rejected(client: TestClient, headers: dict[str, str], ws_id: str):
    r = _register(client, headers, ws_id, "nope/missing.html")
    assert r.status_code == 422


# ----------------------------------------------------------------------
# Path safety
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    ["../outside.html", "..\\..\\evil.html", "/etc/passwd.html", "a/../../escape.md"],
)
def test_path_traversal_rejected(
    client: TestClient, headers: dict[str, str], ws_id: str, bad: str
):
    r = _register(client, headers, ws_id, bad)
    assert r.status_code in (403, 422), (bad, r.status_code, r.text)


def test_register_requires_auth(client: TestClient, ws_id: str):
    r = client.post(
        f"/api/workbench/workspaces/{ws_id}/preview-sources",
        json={"kind": "static", "path": "index.html"},
    )
    assert r.status_code in (401, 403)


# ----------------------------------------------------------------------
# Content endpoint + security headers
# ----------------------------------------------------------------------
def test_content_serves_html_with_csp_sandbox_headers(
    client: TestClient, headers: dict[str, str], ws_id: str
):
    src = _register(client, headers, ws_id, "index.html").json()
    r = client.get(f"/api/workbench/preview-sources/{src['id']}/content", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "sandbox allow-scripts"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "P1A_OK" in r.text
    # 绝不暴露绝对路径：响应体与响应头都不得出现 tmp/authorized_root 痕迹
    assert "tmp" not in r.text.lower()
    assert "text/html" in r.headers["content-type"]


def test_content_md_is_raw_text(client: TestClient, headers: dict[str, str], ws_id: str):
    src = _register(client, headers, ws_id, "notes.md").json()
    r = client.get(f"/api/workbench/preview-sources/{src['id']}/content", headers=headers)
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "# 计划" in r.text


def test_content_requires_auth(client: TestClient, headers: dict[str, str], ws_id: str):
    src = _register(client, headers, ws_id, "index.html").json()
    # Drop the session cookie: an anonymous iframe/request must not read content.
    client.cookies.clear()
    r = client.get(f"/api/workbench/preview-sources/{src['id']}/content")
    assert r.status_code in (401, 403)


def test_content_of_unknown_source_is_404(client: TestClient, headers: dict[str, str]):
    r = client.get("/api/workbench/preview-sources/psrc-does-not-exist/content", headers=headers)
    assert r.status_code == 404


def test_content_of_deleted_file_is_explicit_error(
    client: TestClient, headers: dict[str, str], ws_id: str, tmp_path: Path
):
    target = tmp_path / "p1a-site" / "gone.html"
    target.write_text("<p>bye</p>", encoding="utf-8")
    src = _register(client, headers, ws_id, "gone.html").json()
    target.unlink()
    r = client.get(f"/api/workbench/preview-sources/{src['id']}/content", headers=headers)
    assert r.status_code == 404  # 诚实报错，不假装渲染成功


# ----------------------------------------------------------------------
# Version probe (hot refresh)
# ----------------------------------------------------------------------
def test_version_changes_when_file_changes(
    client: TestClient, headers: dict[str, str], ws_id: str, tmp_path: Path
):
    src = _register(client, headers, ws_id, "index.html").json()
    v1 = client.get(
        f"/api/workbench/preview-sources/{src['id']}/version", headers=headers
    ).json()["version"]
    assert v1 == src["version"]

    # Rewrite the file with different content → mtime/size change → new version.
    f = tmp_path / "p1a-site" / "index.html"
    f.write_text("<html><body><h1>P1A_OK_V2</h1></body></html>", encoding="utf-8")
    v2 = client.get(
        f"/api/workbench/preview-sources/{src['id']}/version", headers=headers
    ).json()["version"]
    assert v2 != v1

    # Content endpoint reflects the fresh bytes for the reload.
    body = client.get(f"/api/workbench/preview-sources/{src['id']}/content", headers=headers)
    assert "P1A_OK_V2" in body.text


def test_version_of_unknown_source_is_404(client: TestClient, headers: dict[str, str]):
    r = client.get("/api/workbench/preview-sources/psrc-does-not-exist/version", headers=headers)
    assert r.status_code == 404


# ----------------------------------------------------------------------
# Unregister
# ----------------------------------------------------------------------
def test_unregister_marks_source_offline(client: TestClient, headers: dict[str, str], ws_id: str):
    src = _register(client, headers, ws_id, "index.html").json()
    r = client.delete(f"/api/workbench/preview-sources/{src['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["state"] == "offline"

    lst = client.get(f"/api/workbench/workspaces/{ws_id}/preview-sources", headers=headers)
    states = {i["id"]: i["state"] for i in lst.json()["items"]}
    assert states[src["id"]] == "offline"

    # Content is no longer served once offline.
    body = client.get(f"/api/workbench/preview-sources/{src['id']}/content", headers=headers)
    assert body.status_code == 404


def test_unregister_requires_csrf(client: TestClient, headers: dict[str, str], ws_id: str):
    src = _register(client, headers, ws_id, "index.html").json()
    r = client.delete(f"/api/workbench/preview-sources/{src['id']}")  # no CSRF header
    assert r.status_code in (401, 403)


# ----------------------------------------------------------------------
# Process sources delegate to the existing PreviewService
# ----------------------------------------------------------------------
def test_register_process_source_returns_loopback_url(
    client: TestClient, headers: dict[str, str], ws_id: str, tmp_path: Path
):
    import socket
    import sys

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    root = tmp_path / "p1a-site"
    r = client.post(
        f"/api/workbench/workspaces/{ws_id}/preview-sources",
        json={
            "kind": "process",
            "command": [sys.executable, "-m", "http.server", str(port),
                        "--bind", "127.0.0.1", "--directory", str(root)],
            "target_port": port,
            "process_kind": "http",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    src = r.json()
    try:
        assert src["kind"] == "process"
        assert src["url"].startswith("http://127.0.0.1:")
        assert src["preview_session_id"]
        assert "content_url" not in src

        ver = client.get(
            f"/api/workbench/preview-sources/{src['id']}/version", headers=headers
        )
        assert ver.status_code == 200
        assert ver.json()["kind"] == "process"
        assert ver.json()["preview_session_id"] == src["preview_session_id"]
    finally:
        client.delete(f"/api/workbench/preview-sources/{src['id']}", headers=headers)
