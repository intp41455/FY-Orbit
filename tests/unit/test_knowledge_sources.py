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
    statuses = {s["source_id"]: s for s in list_source_status()}
    assert set(statuses) == {"ima", "baidu_pan"}
    assert statuses["ima"]["available"] is False
    assert statuses["baidu_pan"]["available"] is False
    # W6 增补 E（主控裁决 2026-10-04）：凭证已从「进程内存」迁到 hub 的 Fernet
    # 加密存储，重启不丢。这条断言标注的是**当时的诚实行为**，行为演进后标注
    # 同步翻转——storage / persist_restart 必须永远匹配真实存储位置。
    assert statuses["ima"]["storage"] == "hub_fernet"
    assert statuses["ima"]["persist_restart"] is True
    assert statuses["ima"]["credentials_present"] == {"api_key": False, "base_url": False}


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