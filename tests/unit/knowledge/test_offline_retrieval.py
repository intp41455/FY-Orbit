"""A-离线优先-03 · 离线模式下的嵌入选择（钉住「禁远程回退」）。

`tests/unit/knowledge/test_hybrid_search.py::test_full_chain_runs_with_network_blocked`
已经从「断网时全链路仍跑通」一侧证明过离线可用性。本文件补的是另一侧：
**离线模式下必须主动选本地嵌入**，而不是「先试远程、失败再退回」——
后者在真断网时是拿超时换结论，用户等 30 秒才知道要走本地。

两条判据：

1. 离线（默认）时**不调用** ``resolve_default_embedding``（那个函数会按环境变量
   选到远程 provider），直接用注册表里的 ``hash``；
2. 显式联网（``FY_OFFLINE_MODE=0``）时仍然尊重环境变量选择——本地优先不等于
   把用户配的远程嵌入也一起废掉。
"""

from __future__ import annotations

import socket

import pytest

import find_yourself.db.kb_models  # noqa: F401
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.knowledge import search as kb_search
from find_yourself.services.knowledge.embeddings import HashingEmbedding
from find_yourself.services.knowledge.ingest import KnowledgeIngestService
from find_yourself.services.knowledge.search import KnowledgeSearchService

CORPUS = {
    "共识.md": "# 共识\n\n共识算法由多数派节点确认，出现故障时可能分叉。\n",
    "背包.md": "# 背包\n\n背包容量不足时，可以购买扩容道具提升背包空间。\n",
}


def _seed_corpus(session, owner_id: str = "owner-offline") -> None:
    for name, text in CORPUS.items():
        KnowledgeIngestService(session, AuditService(session)).ingest_text(
            Actor.owner(owner_id), owner_id=owner_id, name=name, text=text
        )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(kb_search.RETRIEVAL_MODE_ENV, raising=False)
    monkeypatch.delenv("FIND_YOURSELF_KB_EMBEDDING", raising=False)
    yield


def test_offline_never_consults_the_remote_embedding_selector(session, monkeypatch):
    """离线时连「远程嵌入选择器」都不该被调用——一次都不行。"""

    def boom(*_a, **_kw):
        raise AssertionError("离线模式不允许走远程嵌入选择路径")

    monkeypatch.setattr(kb_search, "resolve_default_embedding", boom)

    _seed_corpus(session)
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-offline"), owner_id="owner-offline",
        query="共识算法分叉", top_k=3, include_debug=True,
    )
    assert hits["results"], "离线检索必须有结果"
    assert hits["debug"]["vector_hits"], "向量路（本地 hash + sqlite-vec）必须真的被用上"
    assert hits["debug"]["warnings"] == [], hits["debug"]["warnings"]


def test_offline_search_uses_the_registry_local_embedding(session, monkeypatch):
    """选的是注册表里的 hash 工厂，不是绕过注册表自己 new 一个类。"""
    seen: list[str] = []
    original = kb_search.create_embedding

    def spy(name=None):
        seen.append(str(name))
        return original(name)

    monkeypatch.setattr(kb_search, "create_embedding", spy)

    _seed_corpus(session, "owner-2")
    KnowledgeSearchService(session).search(
        Actor.owner("owner-2"), owner_id="owner-2", query="背包扩容", top_k=3,
    )
    assert seen == ["hash"], seen
    assert isinstance(original("hash"), HashingEmbedding)


def test_network_blocked_offline_search_still_finds_local_documents(session, monkeypatch):
    """三重保障：断网 + 远程嵌入被环境变量点名 + 离线 → 仍从本地库召回。"""
    monkeypatch.setenv("FIND_YOURSELF_KB_EMBEDDING", "openai")

    def _no_network(*_a, **_kw):
        raise AssertionError("离线链路不允许任何网络访问")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    _seed_corpus(session, "owner-3")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-3"), owner_id="owner-3",
        query="背包容量不足怎么办", top_k=3,
    )
    assert hits and hits[0]["doc_name"] == "背包.md"
