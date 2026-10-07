"""A-代码智能-01 · LSP 协议桥与服务层测试。

真实语言服务端（pylsp/typescript-language-server）属环境可选件——未安装时
相关端点必须诚实 503，协议与安全逻辑用注入传输全量测试。
"""

from __future__ import annotations

import json
import threading

import pytest

from find_yourself.services.lsp import (
    LSPConnection,
    LSPError,
    LSPServerManager,
    LSPService,
    PathOutsideAllowedRoots,
    decode_messages,
    encode_message,
)


# ---------------------------------------------------------------------------
# 协议桥：分帧编解码（纯函数）
# ---------------------------------------------------------------------------

def test_encode_decode_roundtrip():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    frame = encode_message(payload)
    assert frame.startswith(b"Content-Length: ")
    msgs, rest = decode_messages(frame)
    assert msgs == [payload] and rest == b""


def test_decode_handles_partial_and_sticky_frames():
    p1 = {"id": 1}
    p2 = {"id": 2}
    f1, f2 = encode_message(p1), encode_message(p2)
    # 半包：f1 只来一半 → 无报文；补齐 + f2 整帧 + f2 半帧 → 2 条 + 剩余
    msgs, rest = decode_messages(f1[:5])
    assert msgs == [] and rest == f1[:5]
    msgs, rest = decode_messages(f1 + f2[:10])
    assert msgs == [p1] and rest == f2[:10]
    msgs, rest = decode_messages(rest + f2[10:])
    assert msgs == [p2] and rest == b""


def test_decode_drops_frame_without_content_length():
    msgs, rest = decode_messages(b"garbage\r\n\r\n" + encode_message({"id": 9}))
    assert msgs == [{"id": 9}] and rest == b""   # 坏帧跳过不卡死


# ---------------------------------------------------------------------------
# 协议桥：锁步请求 / 通知 / 握手（内存传输）
# ---------------------------------------------------------------------------

class ScriptedServer:
    """按脚本应答的假 LSP 服务端（内存流，双向直连）。"""

    def __init__(self, script: list[dict]):
        self._inbox = bytearray()
        self._outbox = bytearray()
        self._script = list(script)
        self._lock = threading.Lock()

    def _drain_requests(self) -> None:
        messages, rest = decode_messages(bytes(self._inbox))
        self._inbox[:] = rest
        for m in messages:
            if "id" in m and "method" in m:
                # 弹出脚本中的对应应答（按 method 匹配），并一并推出
                # 脚本里排在该应答后的**主动通知**（无 _for 且无 id 的条目）
                for i, s in enumerate(self._script):
                    if s.get("_for") in (None, m["method"]):
                        self._script.pop(i)
                        reply = {k: v for k, v in s.items() if not k.startswith("_")}
                        if "id" not in reply:
                            reply["id"] = m["id"]
                        self._outbox += encode_message(reply)
                        while self._script and "_for" not in self._script[0] \
                                and "id" not in self._script[0] \
                                and "method" in self._script[0]:
                            note = self._script.pop(0)
                            self._outbox += encode_message(note)
                        break
            elif "method" in m:
                pass  # 客户端通知不回

    def read(self, n: int = 65536) -> bytes:
        self._drain_requests()
        with self._lock:
            out = bytes(self._outbox[:n])
            del self._outbox[:n]
        return out

    def write(self, data: bytes) -> None:
        self._inbox += data


def _connect(script: list[dict]) -> tuple[LSPConnection, ScriptedServer]:
    server = ScriptedServer(script)
    conn = LSPConnection(write_fn=server.write, read_fn=server.read)
    return conn, server


def test_initialize_handshake_and_request():
    conn, _ = _connect([
        {"_for": "initialize", "result": {"capabilities": {"hoverProvider": True}}},
        {"_for": "textDocument/definition",
         "result": {"uri": "file:///x/a.py", "range": {"start": {"line": 3, "character": 0}}}},
    ])
    caps = conn.initialize(root_uri="file:///x")
    assert caps["capabilities"]["hoverProvider"] is True
    # initialized 通知已发出（写侧可见）
    loc = conn.request("textDocument/definition",
                       {"textDocument": {"uri": "file:///x/a.py"}, "position": {"line": 0, "character": 0}})
    assert loc["uri"] == "file:///x/a.py"


def test_request_error_raises_lsp_error():
    conn, _ = _connect([
        {"_for": "initialize", "result": {"capabilities": {}}},
        {"_for": "textDocument/definition",
         "error": {"code": -32602, "message": "bad params"}},
    ])
    conn.initialize(root_uri="file:///x")
    with pytest.raises(LSPError) as ei:
        conn.request("textDocument/definition", {})
    assert ei.value.code == -32602 and "bad params" in ei.value.message


def test_request_timeout_is_honest():
    conn, _ = _connect([])     # 服务端永不回
    with pytest.raises(TimeoutError):
        conn.request("textDocument/definition", {})


def test_diagnostics_notifications_collected():
    conn, _ = _connect([
        {"_for": "initialize", "result": {"capabilities": {}}},
        {"_for": "textDocument/diagnostic", "result": {}},
        # 服务端随后推的 publishDiagnostics（无 id）
        {"method": "textDocument/publishDiagnostics",
         "params": {"diagnostics": [{"severity": 1, "message": "boom",
                                     "range": {"start": {"line": 2, "character": 0}}}],
                    "uri": "file:///x/a.py"}},
    ])
    conn.initialize(root_uri="file:///x")
    conn.request("textDocument/diagnostic", {"textDocument": {"uri": "file:///x/a.py"}})
    reports = conn.drain_diagnostics()
    assert reports and reports[0]["params"]["diagnostics"][0]["message"] == "boom"
    assert conn.drain_diagnostics() == []      # 取后清空


# ---------------------------------------------------------------------------
# 服务层：路径安全 + manager 诚实探测
# ---------------------------------------------------------------------------

def test_manager_status_reports_installed_honestly():
    status = LSPServerManager().status()
    assert set(status) >= {"python", "typescript"}
    for info in status.values():
        assert isinstance(info["installed"], bool)   # 探测结果如实（环境可能未装）


def test_service_rejects_path_outside_roots(tmp_path):
    svc = LSPService(LSPServerManager(), allowed_roots=[str(tmp_path)])
    with pytest.raises(PathOutsideAllowedRoots):
        svc.definition(path="C:/Windows/win.ini", line=0, character=0)
    with pytest.raises(PathOutsideAllowedRoots):
        svc.definition(path=str(tmp_path.parent / "escape.py"), line=0, character=0)


def test_service_definition_via_fake_connection(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("def hello():\n    pass\n", encoding="utf-8")
    calls: list[str] = []

    def factory(_argv_or_language, **_kw) -> LSPConnection:
        calls.append(str(_argv_or_language))
        conn, _ = _connect([
            {"_for": "initialize", "result": {"capabilities": {}}},
            {"_for": "textDocument/definition",
             "result": {"uri": "file:///" + str(tmp_path / "b.py").replace("\\", "/"),
                        "range": {"start": {"line": 5, "character": 4}}}},
        ])
        return conn

    svc = LSPService(LSPServerManager(), allowed_roots=[str(tmp_path)], read_file=lambda p: target.read_text(encoding="utf-8"))
    svc.manager.get_connection = factory  # type: ignore[method-assign] —— 注入 fake
    out = svc.definition(path=str(target), line=0, character=4)
    assert out["count"] == 1
    assert out["locations"][0]["line"] == 5
    assert calls == ["python"]                      # 按扩展名路由到 python 通道


# ---------------------------------------------------------------------------
# API 层：servers 诚实状态 + 未安装 503 降级
# ---------------------------------------------------------------------------

def _client(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from find_yourself.api.app import create_app
    from find_yourself.config import Settings
    from find_yourself.db.base import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool, future=True)
    Base.metadata.create_all(eng)
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret-lsp",
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    app = create_app(session_maker=sessionmaker(bind=eng, expire_on_commit=False, future=True),
                     settings=settings)
    from find_yourself.services.lsp.manager import _force_loopback_client  # noqa
    return None  # placeholder replaced below


def test_lsp_api_servers_and_not_installed(tmp_path):
    """servers 端点如实报状态；definition 对未安装服务端显式 503。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from find_yourself.api.app import create_app
    from find_yourself.config import Settings
    from find_yourself.db.base import Base
    from find_yourself.services.lsp.service import LSPService
    from find_yourself.services.lsp.manager import LSPServerManager
    from find_yourself.api.routes import lsp as lsp_route

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool, future=True)
    Base.metadata.create_all(eng)
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret-lsp",
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    app = create_app(session_maker=sessionmaker(bind=eng, expire_on_commit=False, future=True),
                     settings=settings)
    from find_yourself.api.routes.lsp import router as _lsp_router
    from find_yourself.api.errors import register_exception_handlers
    app.include_router(_lsp_router)          # 测试内挂载（替身主控统一挂载动作）
    register_exception_handlers(app)
    # 让 API 路由的 allowed_roots 指向 tmp_path（越界拒绝可测）
    lsp_route._allowed_roots[:] = [str(tmp_path)]
    lsp_route._service.allowed_roots[:] = [str(tmp_path)]

    async def _force_loopback(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await app(scope, receive, send)

    with TestClient(_force_loopback) as client:
        assert client.post(
            "/auth/register",
            json={"email": "lsp@t.test", "password": "Lsp-test-pass-12345",
                  "consent_accepted": True}).status_code == 200
        r_login = client.post("/auth/login",
                              json={"email": "lsp@t.test", "password": "Lsp-test-pass-12345"})
        headers = {"X-CSRF-Token": r_login.json()["csrf_token"]}

        r = client.get("/api/lsp/servers", headers=headers)
        assert r.status_code == 200
        servers = r.json()["servers"]
        assert "python" in servers and isinstance(servers["python"]["installed"], bool)

        inside = tmp_path / "m.py"
        inside.write_text("x = 1\n", encoding="utf-8")
        r2 = client.post("/api/lsp/definition", json={
            "path": str(inside), "line": 0, "character": 0}, headers=headers)
        # 环境大概率未装 pylsp → 503 诚实降级；若装了则 200（两种都合法）
        assert r2.status_code in (200, 503)
        if r2.status_code == 503:
            assert r2.json()["error"]["code"] == "lsp_server_not_installed"

        r3 = client.post("/api/lsp/definition", json={
            "path": "C:/Windows/win.ini", "line": 0, "character": 0}, headers=headers)
        assert r3.status_code == 403
        assert r3.json()["error"]["code"] == "path_outside_allowed_roots"
