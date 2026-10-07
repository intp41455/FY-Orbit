"""A-离线优先-01/03 · REST 层断网验收（判据 1 / 2 / 3 / 4）。

「物理断网」在这里用**进程级 socket 替身**模拟：DNS 解析与 TCP 连接一律失败。
凡是被测路径真的去碰网络，就一定会在这里露出来（不报错、不空转才是判据）。

判据对照：

* 判据 1 · 零配置首启：默认 settings 无任何模型 key，服务照常起、知识库可检索；
* 判据 2 · 断网可用：①笔记新建/读回不丢（刷新后仍在）②本地检索有结果
  ③远程 LLM 调用点给出**明确降级提示**（503 + `offline_mode_remote_blocked`）；
* 判据 3 · `FY_OFFLINE_MODE` 默认 True；
* 判据 4 · `FY_CAPABILITY_PROFILE` 缺省 novice 且知识库检索缺省可离线。
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from find_yourself.api.app import create_app
from find_yourself.config import Settings

from helpers import login_owner

OWNER_SUB = "owner-sub-123"


@pytest.fixture()
def _no_network(monkeypatch):
    """物理断网替身：DNS 与 TCP 全失败。任何真出网的代码都会在这里炸出来。"""

    def no_dns(_host):
        raise socket.gaierror("simulated offline: name resolution failed")

    def no_connect(_addr, *_a, **_kw):
        raise OSError("simulated offline: network is unreachable")

    monkeypatch.setattr(socket, "gethostbyname", no_dns)
    monkeypatch.setattr(socket, "create_connection", no_connect)
    return True


def _app_client(session_maker, oidc, settings) -> TestClient:
    app = create_app(session_maker=session_maker, settings=settings, oidc=oidc)

    async def _loopback(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)  # 本机 dev-token 门只认 loopback
        return await app(scope, receive, send)

    return TestClient(_loopback)


# --------------------------------------------------------------------------- #
# 判据 3 · 默认离线 + 状态端点
# --------------------------------------------------------------------------- #

def test_offline_status_endpoint_says_default_offline(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/offline/status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offline_mode"] is True           # 判据 3：默认离线
    assert body["remote_blocked"] is True
    assert body["env"] == "FY_OFFLINE_MODE"
    assert body["network_reachable"] is None      # 没探测就不假装探测过
    assert body["remote_block_reason"], "必须给出人话原因，不能只给布尔值"


def test_offline_status_requires_auth(client: TestClient):
    assert client.get("/api/offline/status").status_code == 401


# --------------------------------------------------------------------------- #
# 判据 1 · 零配置首启（无任何 API key）
# --------------------------------------------------------------------------- #

def test_zero_config_boot_and_kb_roundtrip_without_network(client: TestClient,
                                                           _no_network):
    """零配置 + 断网：服务照常、知识库可写入可检索（hash 嵌入 + 本地向量库）。"""
    headers = login_owner(client)

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200

    r = client.post("/api/kb/documents", params={"name": "离线笔记.md"},
                    content="离线优先意味着断网也能检索本地知识库。".encode("utf-8"),
                    headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["document"]["status"] == "ready", r.json()

    r = client.post("/api/kb/search", json={"query": "离线优先", "top_k": 5},
                    headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1, r.json()


# --------------------------------------------------------------------------- #
# 判据 2 ① · 断网下新建/编辑笔记：保存不丢、刷新后仍在
# --------------------------------------------------------------------------- #

def test_note_survives_a_full_network_outage(client: TestClient, _no_network):
    headers = login_owner(client)

    created = client.post("/api/stash",
                          json={"title": "断网记录", "content": "断网时写下的一行字"},
                          headers=headers)
    assert created.status_code == 200, created.text
    stash_id = created.json()["record"]["id"]

    # 「刷新后」：换一次独立请求把内容读回来（不是拿响应里的对象糊弄）。
    reread = client.get(f"/api/stash/{stash_id}", headers=headers)
    assert reread.status_code == 200, reread.text
    assert reread.json()["record"]["content"] == "断网时写下的一行字"

    listed = client.get("/api/stash", headers=headers)
    assert listed.status_code == 200
    assert stash_id in {row["id"] for row in listed.json()["records"]}


# --------------------------------------------------------------------------- #
# 判据 2 ③ · 远程 LLM 调用点：明确降级提示（不是模型没配、也不是网络错误）
# --------------------------------------------------------------------------- #

def test_remote_llm_call_is_refused_with_explicit_offline_notice(
        session_maker, oidc, tmp_path):
    """已配好云端模型 + 默认离线 → 503 `offline_mode_remote_blocked` 而不是去连。

    注意与「未配置模型」的区别：这里配置是齐的，唯一原因是**离线门**。
    提示里必须告诉用户「本地能力不受影响」以及「怎么恢复联网」。
    """
    configured = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret",
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
        model_api_key="sk-configured",
        model_base_url="https://api.example/v1",
    )
    with _app_client(session_maker, oidc, configured) as c:
        headers = login_owner(c)
        r = c.post("/api/inference/complete",
                   json={"model": "gpt-4o-mini", "prompt": "hello"}, headers=headers)
        assert r.status_code == 503, r.text
        body = r.json()
        assert body["error"]["code"] == "offline_mode_remote_blocked", body
        message = body["error"]["message"]
        assert "FY_OFFLINE_MODE" in message
        assert "本地能力" in message
        # 没有伪造的回答。
        assert "text" not in body.get("data", {})


def test_offline_does_not_break_local_endpoints(session_maker, oidc, tmp_path,
                                                _no_network):
    """同一个「已配云端」的 app，在断网下本地端点照常：离线不等于残废。"""
    configured = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret",
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
        model_api_key="sk-configured",
        model_base_url="https://api.example/v1",
    )
    with _app_client(session_maker, oidc, configured) as c:
        headers = login_owner(c)
        assert c.get("/health/ready").status_code == 200
        created = c.post("/api/stash", json={"content": "断网也能写"}, headers=headers)
        assert created.status_code == 200


# --------------------------------------------------------------------------- #
# 判据 4 · 零配置兜底：novice 档 + 检索缺省可离线
# --------------------------------------------------------------------------- #

def test_capability_profile_defaults_to_novice():
    s = Settings(environment="test", session_secret="x" * 40,
                 database_url="sqlite://", public_url="http://x")
    assert s.capability_profile == "novice"


def test_kb_retrieval_default_is_offline_capable():
    """检索档位缺省 hybrid，且其向量路缺省用本地 hash 嵌入（不依赖远程服务）。"""
    import os

    from find_yourself.services.knowledge import search as kb_search
    from find_yourself.services.knowledge.embeddings import DEFAULT_EMBEDDING

    assert kb_search.DEFAULT_RETRIEVAL_MODE == "hybrid"
    assert kb_search.RETRIEVAL_MODE_ENV == "FIND_YOURSELF_KB_RETRIEVAL_MODE"
    assert DEFAULT_EMBEDDING == "hash"
    # 缺省即离线：两个环境变量都没设时，走的必须是本地实现。
    assert not os.getenv(kb_search.RETRIEVAL_MODE_ENV)
    assert not os.getenv("FIND_YOURSELF_KB_EMBEDDING")
