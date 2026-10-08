"""W3 知识源适配器单测（ima 真实 HTTP 契约 + 百度网盘骨架）。

* ima：用 ``httpx.MockTransport`` 打桩（**不打真网**），验证 list/fetch/search、
  429/5xx 退避重试、凭证缺失时 ``health_check`` 如实报「未接入」；
* 百度网盘 v1 骨架：``list_sources`` / ``fetch_document`` / 配置一律抛
  ``baidu_pan_not_implemented``，``health_check.available`` 恒 False——
  绝不用空列表或假条目冒充「已接入」。
* secret store：凭证只在内存，不入库、不落盘、可遗忘。
"""

from __future__ import annotations

import httpx
import pytest

from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.knowledge import (
    KnowledgeService,
    list_source_status,
    secret_store,
)
from find_yourself.services.knowledge.sources import build_source
from find_yourself.services.knowledge.sources.base import (
    SourceRef,
    UnsupportedCapability,
    retry_call,
)
from find_yourself.services.knowledge.sources.baidu_pan import BaiduPanSource
from find_yourself.services.knowledge.sources.ima import ImaSource

KB_ID = "kb-1"


def _transport(payload_by_path: dict[str, object], *, fail_times: int = 0, status: int = 500):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if len(calls) <= fail_times:
            return httpx.Response(status, json={"error": "upstream busy"})
        return httpx.Response(200, json=payload_by_path.get(path, {}))

    return httpx.MockTransport(handler), calls


def _source(payloads: dict[str, object], **kwargs) -> tuple[ImaSource, list[str]]:
    transport, calls = _transport(payloads, **kwargs)
    client = httpx.Client(transport=transport)
    src = ImaSource(api_key="k", base_url="https://ima.example", client=client)
    return src, calls


# --- ima -------------------------------------------------------------------- #

def test_ima_unconfigured_reports_not_connected_honestly():
    src = ImaSource(api_key="", base_url="")
    health = src.health_check()
    assert health["available"] is False
    assert health["configured"] is False
    assert "未接入" in health["detail"]
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "ima_not_configured"


def test_ima_list_sources_reads_knowledge_bases():
    src, calls = _source({"/api/knowledge_bases": {"data": [
        {"id": KB_ID, "name": "自建库", "updated_at": "2026-10-01T00:00:00Z"}
    ]}})
    refs = src.list_sources()
    assert refs == [SourceRef(source_id="ima", external_id=KB_ID, name="自建库",
                              kind="knowledge_base", updated_at="2026-10-01T00:00:00Z")]
    assert calls == ["/api/knowledge_bases"]


def test_ima_fetch_document_yields_full_text_items():
    src, _ = _source({f"/api/knowledge_bases/{KB_ID}/items": {"items": [
        {"id": "i1", "title": "小屋规则", "content": "家具遵守碰撞规则"},
        {"id": "i2", "title": "订阅库条目", "preview": "只有 300 字预览"},
    ]}})
    docs = list(src.fetch_document(SourceRef("ima", KB_ID, "自建库")))
    assert [d.external_id for d in docs] == ["i1", "i2"]
    assert docs[0].text == "家具遵守碰撞规则"
    # 订阅类只给预览：必须打标记，不能假装拿到全文。
    assert docs[1].metadata["preview_only"] is True


def test_ima_retries_5xx_then_succeeds():
    src, calls = _source(
        {"/api/knowledge_bases": {"data": [{"id": KB_ID, "name": "自建库"}]}},
        fail_times=2,
        status=503,
    )
    refs = src.list_sources()
    assert refs[0].external_id == KB_ID
    assert len(calls) == 3


def test_ima_api_error_is_explicit_not_silent():
    transport, _ = _transport({}, fail_times=9, status=401)
    src = ImaSource(api_key="bad", base_url="https://ima.example",
                    client=httpx.Client(transport=transport))
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "ima_api_error"
    assert "401" in err.value.message


def test_ima_health_probe_reports_degraded_on_failure():
    transport, _ = _transport({}, fail_times=9, status=500)
    src = ImaSource(api_key="k", base_url="https://ima.example",
                    client=httpx.Client(transport=transport))
    health = src.health_check(probe=True)
    assert health["available"] is False
    assert health["degraded"] is True
    assert "探测失败" in health["detail"]


def test_retry_call_does_not_retry_validation_errors():
    slept: list[float] = []
    with pytest.raises(ValidationFailed):
        retry_call(
            lambda: (_ for _ in ()).throw(ValidationFailed("bad_key", "key 无效")),
            attempts=3,
            sleep=slept.append,
        )
    assert slept == []


def test_unsupported_capability_is_explicit():
    src = BaiduPanSource()
    with pytest.raises(ValidationFailed):
        src.search_metadata(SourceRef("baidu_pan", "x", "y"), "q")
    assert issubclass(UnsupportedCapability, Exception)


# --- 百度网盘骨架 ----------------------------------------------------------- #

def test_baidu_pan_skeleton_never_fakes_results():
    src = BaiduPanSource()
    health = src.health_check()
    assert health["available"] is False and health["configured"] is False
    assert "未接入" in health["detail"]
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "baidu_pan_not_implemented"
    with pytest.raises(ValidationFailed):
        src.fetch_document(SourceRef("baidu_pan", "1", "dir"))
    with pytest.raises(ValidationFailed) as err:
        src.oauth_authorize_url()
    assert err.value.code == "baidu_pan_not_implemented"


# --- 注册表 / 凭证 ---------------------------------------------------------- #

def test_source_status_lists_both_adapters_with_baidu_not_connected():
    secret_store.forget("ima")
    secret_store.forget("local_files")
    statuses = {s["source_id"]: s for s in list_source_status()}
    # P3 · 备轨上线：注册表现在有第三个源 local_files（可离线自测的本地目录源）。
    assert set(statuses) == {"ima", "baidu_pan", "local_files"}
    assert statuses["ima"]["available"] is False
    assert statuses["baidu_pan"]["available"] is False
    # 未配置 root_dir 时如实报「未接入」，绝不假装可读。
    assert statuses["local_files"]["available"] is False
    assert statuses["local_files"]["credentials_present"] == {"root_dir": False}
    # W6 增补 E（主控裁决 2026-10-04）：凭证已从「进程内存」迁到 hub 的 Fernet
    # 加密存储，重启不丢。这条断言标注的是**当时的诚实行为**，行为演进后标注
    # 同步翻转——storage / persist_restart 必须永远匹配真实存储位置。
    assert statuses["ima"]["storage"] == "hub_fernet"
    assert statuses["ima"]["persist_restart"] is True
    # B1 · G2（2026-10-07）：ima 凭证字段扩为 App ID / API Key / Secret Key /
    # Base URL 四件套（设置页入口），断言随字段集同步。
    assert statuses["ima"]["credentials_present"] == {
        "app_id": False, "api_key": False, "secret_key": False, "base_url": False,
    }


def test_configure_source_rejects_empty_and_baidu(session):
    audit_svc = __import__(
        "find_yourself.services.audit", fromlist=["AuditService"]
    ).AuditService(session)
    kb = KnowledgeService(session, audit_svc)
    with pytest.raises(ValidationFailed) as err:
        kb.configure_source("ima", {"api_key": "", "base_url": ""})
    assert err.value.code == "empty_credentials"
    with pytest.raises(ValidationFailed) as err:
        kb.configure_source("baidu_pan", {"app_key": "x"})
    assert err.value.code == "baidu_pan_not_implemented"
    with pytest.raises(NotFound):
        kb.configure_source("dropbox", {"api_key": "x"})


def test_secret_store_keeps_credentials_in_memory_only(session):
    secret_store.set("ima", {"api_key": "abc", "base_url": "https://ima.example"})
    assert secret_store.has("ima") is True
    assert secret_store.masked("ima") == {"api_key": True, "base_url": True}
    src = build_source("ima")
    assert src.is_configured() is True
    status = [s for s in list_source_status() if s["source_id"] == "ima"][0]
    assert "abc" not in str(status)  # 状态里绝不回显密钥
    assert secret_store.forget("ima") is True
    assert secret_store.has("ima") is False

# ===========================================================================
# P3 · 能力协商 + 备轨 local_files + person_kb 硬编码治理
# ===========================================================================
from find_yourself.services.knowledge.sources.base import (
    capability_flags,
    ensure_capability,
)
from find_yourself.services.knowledge.sources.local_files import LocalFilesSource


@pytest.fixture()
def kb_root(tmp_path, monkeypatch):
    root = tmp_path / "kb-files"
    root.mkdir()
    (root / "notes.md").write_text("# 笔记\n\n本地知识正文。", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "diary.txt").write_text("2026-10-05 天气晴。", encoding="utf-8")
    (root / "page.html").write_text("<p>旧网页</p>", encoding="utf-8")
    (root / "code.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.delenv("FY_LOCAL_KB_ROOT", raising=False)
    secret_store.forget("local_files")
    return root


# ---- 能力协商 ---------------------------------------------------------------
def test_capability_flags_read_write_list_shape(kb_root):
    flags = capability_flags(LocalFilesSource(root_dir=str(kb_root)))
    # 只读协议的诚实边界：read/list 支持，write 恒 False。
    assert flags["read"] is True
    assert flags["list"] is True
    assert flags["write"] is False
    assert flags["search"] is False  # 本地源不做服务端检索


def test_ensure_capability_ima_search_supported():
    src = build_source("ima")
    ensure_capability(src, "search")  # ima 声明 searchable=True → 通过


def test_ensure_capability_unsupported_search_raises(kb_root):
    """门禁核心：请求不支持的能力 → 抛 UnsupportedCapability，绝不返回空。"""
    src = LocalFilesSource(root_dir=str(kb_root))
    with pytest.raises(UnsupportedCapability) as err:
        ensure_capability(src, "search")
    assert err.value.code == "capability_not_supported"


def test_ensure_capability_write_always_unsupported_even_for_ima():
    """只读协议的诚实声明：连 ima 也没有 write 能力。"""
    with pytest.raises(UnsupportedCapability) as err:
        ensure_capability(build_source("ima"), "write")
    assert err.value.code == "capability_not_supported"


def test_ensure_capability_unknown_name_raises():
    """拼错能力名必须炸在调用点，而不是被当成「源支持」。"""
    with pytest.raises(UnsupportedCapability) as err:
        ensure_capability(build_source("ima"), "teleport")
    assert err.value.code == "unknown_capability"


def test_ensure_capability_incremental_unsupported_for_all_v1_sources(kb_root):
    """v1 三源都不支持增量游标：按能力路由请求增量时必须显式失败，不得静默全量。"""
    with pytest.raises(UnsupportedCapability):
        ensure_capability(LocalFilesSource(root_dir=str(kb_root)), "incremental")
    with pytest.raises(UnsupportedCapability):
        ensure_capability(build_source("ima"), "incremental")
    with pytest.raises(UnsupportedCapability):
        ensure_capability(build_source("baidu_pan"), "incremental")


def test_source_search_metadata_raises_unsupported_not_empty(kb_root):
    """绕过协商闸门直接调 search_metadata 也必须显式抛（双保险）。"""
    src = LocalFilesSource(root_dir=str(kb_root))
    ref = SourceRef(source_id=src.source_id, external_id="notes.md", name="notes.md")
    with pytest.raises(UnsupportedCapability):
        src.search_metadata(ref, "query", limit=5)


# ---- 备轨 local_files -------------------------------------------------------
def test_local_files_unconfigured_health_honest(monkeypatch):
    monkeypatch.delenv("FY_LOCAL_KB_ROOT", raising=False)
    secret_store.forget("local_files")
    src = build_source("local_files")
    info = src.health_check()
    assert info["available"] is False and info["configured"] is False
    with pytest.raises(ValidationFailed):
        src.require_configured()


def test_local_files_missing_root_reports_degraded(tmp_path):
    src = LocalFilesSource(root_dir=str(tmp_path / "nope"))
    info = src.health_check()
    assert info["configured"] is True and info["available"] is False
    assert info["degraded"] is True


def test_local_files_list_sources_only_text_files(kb_root):
    src = LocalFilesSource(root_dir=str(kb_root))
    refs = src.list_sources()
    ids = {r.external_id for r in refs}
    # 只列 ingest 白名单内的文本扩展：.py/.html 都不列（列出却导入必失败=不诚实）。
    assert ids == {"notes.md", "sub/diary.txt"}
    assert all(r.kind == "file" for r in refs)


def test_local_files_fetch_document_reads_full_text(kb_root):
    src = LocalFilesSource(root_dir=str(kb_root))
    docs = list(src.fetch_document(
        SourceRef(source_id=src.source_id, external_id="sub/diary.txt", name="diary.txt")))
    assert len(docs) == 1
    assert docs[0].text == "2026-10-05 天气晴。"
    assert docs[0].metadata["path"] == "sub/diary.txt"


def test_local_files_fetch_missing_file_explicit_failure(kb_root):
    src = LocalFilesSource(root_dir=str(kb_root))
    with pytest.raises(ValidationFailed) as err:
        list(src.fetch_document(
            SourceRef(source_id=src.source_id, external_id="ghost.md", name="ghost.md")))
    assert err.value.code == "local_files_document_missing"


def test_local_files_rejects_traversal_refs(kb_root):
    src = LocalFilesSource(root_dir=str(kb_root))
    # 两种分隔符都要覆盖，否则会漏掉目标平台的穿越形态。
    for bad in ["../outside.md", "a/../../b.md", "..\\..\\outside.md", "a\\..\\..\\b.md", "C:/abs.md", ""]:
        with pytest.raises(ValidationFailed) as err:
            list(src.fetch_document(
                SourceRef(source_id=src.source_id, external_id=bad, name="x")))
        assert err.value.code == "local_files_bad_ref"


def test_local_files_non_utf8_file_explicit_failure(kb_root):
    (kb_root / "binary.md").write_bytes(b"\xff\xfe\x00binary")
    src = LocalFilesSource(root_dir=str(kb_root))
    with pytest.raises(ValidationFailed) as err:
        list(src.fetch_document(
            SourceRef(source_id=src.source_id, external_id="binary.md", name="binary.md")))
    assert err.value.code == "local_files_document_not_text"


def test_build_source_local_files_from_env(kb_root, monkeypatch):
    monkeypatch.setenv("FY_LOCAL_KB_ROOT", str(kb_root))
    src = build_source("local_files")
    assert src.is_configured() is True
    assert [r.name for r in src.list_sources()]  # 真的能列出


def test_build_source_local_files_from_secret_store(kb_root):
    secret_store.set("local_files", {"root_dir": str(kb_root)})
    try:
        src = build_source("local_files")
        assert src.is_configured() is True
        status = [s for s in list_source_status() if s["source_id"] == "local_files"][0]
        assert status["available"] is True
        # root_dir 是本地目录路径（非机密），detail 可展示；凭证契约只要求不回显值。
        assert status["credentials_present"] == {"root_dir": True}
    finally:
        secret_store.forget("local_files")


def test_local_files_sync_via_service_roundtrip(session, kb_root):
    """端到端：备轨源经 KnowledgeService.sync_source 真实入库。"""
    from find_yourself.services.audit import AuditService

    secret_store.set("local_files", {"root_dir": str(kb_root)})
    try:
        kb = KnowledgeService(session, AuditService(session))
        summary = kb.sync_source(
            __import__("find_yourself.services.actor", fromlist=["Actor"]).Actor.owner("owner-p3"),
            owner_id="owner-p3", source_id="local_files",
        )
        assert summary["imported"] >= 2
        assert summary["failed"] == 0
    finally:
        secret_store.forget("local_files")


# ---- person_kb 硬编码治理 -----------------------------------------------------
def test_person_kb_unconfigured_reports_honestly(monkeypatch):
    """不再默认硬编码 D:\person-kb\kb.db：未配置时如实报不可用并给出环境变量名。"""
    from find_yourself.adapters.person_kb import PERSON_KB_DB_ENV, PersonKbAdapter

    monkeypatch.delenv(PERSON_KB_DB_ENV, raising=False)
    adapter = PersonKbAdapter()
    assert adapter.is_configured() is False
    stats = adapter.inspect_stats()
    assert stats["available"] is False
    assert PERSON_KB_DB_ENV in stats["detail"]


def test_person_kb_env_path_reads_real_sqlite(tmp_path, monkeypatch):
    """环境变量注入的路径真实可读（只读打开，零写入）。"""
    import sqlite3 as _sq

    from find_yourself.adapters.person_kb import PERSON_KB_DB_ENV, PersonKbAdapter

    db_file = tmp_path / "kb.db"
    conn = _sq.connect(str(db_file))
    conn.execute("CREATE TABLE sessions (id TEXT, platform TEXT, title TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT, content TEXT, created_at TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'chatgpt', '会话一', '2026-01-01')")
    conn.execute("INSERT INTO messages VALUES ('m1', 's1', 'user', '你好', '2026-01-01')")
    conn.commit()
    conn.close()

    monkeypatch.setenv(PERSON_KB_DB_ENV, str(db_file))
    adapter = PersonKbAdapter()
    stats = adapter.inspect_stats()
    assert stats["available"] is True
    assert stats["sessions_count"] == 1
    convs = list(adapter.iter_conversations())
    assert len(convs) == 1 and convs[0]["messages"][0]["content"] == "你好"
