"""包6 · A-命理画像-01 · 命理公共知识真调知识库 RAG（离线可测的真实链路）。

AC：「云盘资料 → RAG → 命理画像」。云盘资料以 ``source='baidu_pan'`` 入库
（与连接器同步产物同构），经 ``KnowledgeSearchService`` 混合检索召回，
作为解读引用。个人记忆路（MemoryService）语义原样保留。
"""

from __future__ import annotations

import pytest

from find_yourself.charts.retrieval import DualPathRetrievalService
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.knowledge.ingest import KnowledgeIngestService

OWNER = "owner-1"
CLOUD_DOC = """# 云盘同步：子平真诠摘录

八字用神，专求月令。以日干配月支，而生克不同，格局分焉。

财官印食为四吉神，煞伤劫刃为四凶神。五行贵在中和。
"""


@pytest.fixture()
def svc(session):
    return DualPathRetrievalService(session, memory_service=None)


@pytest.fixture()
def cloud_doc(session, owner):
    """模拟百度网盘同步入库的一份资料（source='baidu_pan'）。"""
    ingest = KnowledgeIngestService(session, AuditService(session))
    doc = ingest.ingest_text(
        owner, owner_id=OWNER, name="云盘·子平真诠摘录.md",
        text=CLOUD_DOC, source="baidu_pan",
    )
    assert doc.status == "ready"
    return doc


def test_public_knowledge_with_owner_pulls_real_cloud_rag_hits(svc, owner, cloud_doc):
    citations = svc.retrieve_public_knowledge("八字 用神", "bazi", actor=owner)
    assert citations, "云盘资料已入库，检索必须有召回"
    top = citations[0]
    assert top["confidence"] == "kb_rag"
    assert top["path"] == "cloud_rag"  # source=baidu_pan 的资料 → 云盘 RAG 路
    assert top["source"] == "baidu_pan"
    assert top["title"] == "云盘·子平真诠摘录.md"
    assert "用神" in top["snippet"] or "月令" in top["snippet"]
    assert top["degraded"] is False
    assert top["chunk_id"] and top["doc_id"] == cloud_doc.id


def test_public_knowledge_local_source_labeled_local_rag(svc, owner, session):
    ingest = KnowledgeIngestService(session, AuditService(session))
    ingest.ingest_text(owner, owner_id=OWNER, name="本地笔记.md", text="八字用神，专求月令。")
    citations = svc.retrieve_public_knowledge("八字 用神", "bazi", owner_id=OWNER)
    assert citations
    assert citations[0]["path"] == "local_rag"


def test_public_knowledge_with_owner_and_no_hits_is_honestly_empty(svc, owner):
    """有 owner 上下文但没召回 → 空列表（诚实），绝不降级到精选常量冒充检索。"""
    citations = svc.retrieve_public_knowledge("量子色动力学", "bazi", actor=owner)
    assert citations == []


def test_public_knowledge_without_owner_context_falls_back_marked_degraded(svc):
    """无 owner（如 charts 路由未接线）→ 精选常量引用，但如实标注降级。"""
    citations = svc.retrieve_public_knowledge("八字", "bazi")
    assert citations
    assert all(c["confidence"] == "curated_fallback" for c in citations)
    assert all(c["degraded"] is True and c["path"] == "curated_fallback" for c in citations)


def test_fallback_citations_keep_classic_sources(svc):
    citations = svc.retrieve_public_knowledge("占星", "western")
    titles = {c["title"] for c in citations}
    assert any("NASA" in t for t in titles)
    assert all(c["url"] for c in citations)


def test_owner_id_kwarg_equivalent_to_actor(svc, owner, cloud_doc):
    by_actor = svc.retrieve_public_knowledge("八字 用神", "bazi", actor=owner)
    by_owner = svc.retrieve_public_knowledge("八字 用神", "bazi", owner_id=OWNER)
    assert [c["chunk_id"] for c in by_actor] == [c["chunk_id"] for c in by_owner]


def test_service_bound_owner_id_via_constructor(session):
    svc = DualPathRetrievalService(session, memory_service=None, owner_id=OWNER)
    citations = svc.retrieve_public_knowledge("不存在的主题词", "bazi")
    # 绑定了 owner → 走真实检索（无召回 → 空列表），而不是精选降级
    assert citations == []


def test_personal_memory_path_unchanged_without_memory_service(session, owner):
    svc = DualPathRetrievalService(session, memory_service=None)
    assert svc.retrieve_personal_memory(owner, domain="personal", query="traits") == []


def test_public_retrieval_never_leaks_other_owner_docs(session, owner, cloud_doc):
    other = Actor.owner("owner-2")
    ingest = KnowledgeIngestService(session, AuditService(session))
    ingest.ingest_text(other, owner_id="owner-2", name="他人文档.md", text="八字用神专属内容。")
    citations = DualPathRetrievalService(session).retrieve_public_knowledge(
        "八字 用神", "bazi", actor=other,
    )
    # owner 谓词先于排序：只能召回 owner-2 自己的文档
    assert citations
    assert all(c["title"] != "云盘·子平真诠摘录.md" for c in citations)
