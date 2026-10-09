"""W3 知识库 · 检索单测（owner 隔离 + 混合召回 + 确定性排序）。

核心断言（任务书 §2.3 / §4）：
* ``owner_id`` 是签名必填，缺失直接 422 语义错误（memory.py:195 的教训）；
* 跨 owner 的切片**永远**不可见，且过滤发生在排序之前；
* ``status='failed'`` 的文档切片不进召回（宁可少召回，也不返回坏内容）；
* 中文关键词命中并返回来源文档名 / source / 得分 / 命中词；
* 同一 query 的排序确定（分数降序 → 长块优先 → id 升序）。
"""

from __future__ import annotations

import pytest

import find_yourself.db.kb_models  # noqa: F401
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.ingest import KnowledgeIngestService
from find_yourself.services.knowledge.search import (
    KnowledgeSearchService,
    score_chunk,
    tokenize,
)

CABIN_MD = "# 小屋玩法\n\n背景探险包含采集玩法与日常任务，背包上限 20 格。\n"
SECRET_MD = "# 内部备忘\n\n这是 owner-2 的私密文档，包含口令 rainbow-42。\n"
EMPTY_PDF = b"%PDF-1.7 broken"


def _seed(session, owner_id: str, name: str, text: str):
    return KnowledgeIngestService(session, AuditService(session)).ingest_text(
        Actor.owner(owner_id), owner_id=owner_id, name=name, text=text
    )


def _search(session, owner_id: str, query: str, top_k: int = 5):
    return KnowledgeSearchService(session).search(
        Actor.owner(owner_id), owner_id=owner_id, query=query, top_k=top_k
    )


# --- 分词与打分 ------------------------------------------------------------- #

def test_tokenize_emits_cjk_unigrams_bigrams_and_latin():
    toks = tokenize("知识库 RAG")
    assert "知" in toks and "知识" in toks and "rag" in toks


def test_score_chunk_prefers_earlier_and_more_matches():
    early, hits = score_chunk("知识库是本地优先的检索底座。" * 3, ["知识库", "检索"])
    late, _ = score_chunk("前面是铺垫文字。" * 40 + "知识库检索", ["知识库", "检索"])
    assert early > late
    assert hits == ["知识库", "检索"]


def test_score_chunk_is_zero_without_any_term():
    assert score_chunk("无关内容", ["知识库"]) == (0.0, [])


# --- 检索 ------------------------------------------------------------------- #

def test_search_finds_chinese_keyword_with_source(session):
    _seed(session, "owner-1", "小屋玩法.md", CABIN_MD)
    hits = _search(session, "owner-1", "采集玩法")
    assert len(hits) == 1
    hit = hits[0]
    assert hit["doc_name"] == "小屋玩法.md"
    assert hit["source"] == "local"
    assert hit["score"] > 0
    assert "采集玩法" in hit["content"]
    assert hit["matched_terms"]


def test_search_never_leaks_other_owner_documents(session):
    _seed(session, "owner-1", "小屋玩法.md", CABIN_MD)
    _seed(session, "owner-2", "内部备忘.md", SECRET_MD)
    mine = _search(session, "owner-1", "rainbow-42")
    assert mine == []
    theirs = _search(session, "owner-2", "rainbow-42")
    assert len(theirs) == 1 and "口令" in theirs[0]["content"]


def test_search_requires_owner_id_in_signature(session):
    with pytest.raises(ValidationFailed) as err:
        KnowledgeSearchService(session).search(
            Actor.owner("owner-1"), owner_id="", query="知识库"
        )
    assert err.value.code == "owner_required"


def test_search_rejects_blank_query(session):
    with pytest.raises(ValidationFailed) as err:
        _search(session, "owner-1", "   ")
    assert err.value.code == "query_required"


def test_failed_document_chunks_are_not_searchable(session):
    doc = KnowledgeIngestService(session, AuditService(session)).ingest_bytes(
        Actor.owner("owner-1"), owner_id="owner-1", name="bad.pdf", data=EMPTY_PDF
    )
    assert doc.status == "failed"
    assert _search(session, "owner-1", "bad pdf") == []


def test_search_respects_top_k_and_is_deterministic(session):
    for i in range(4):
        _seed(session, "owner-1", f"doc{i}.md", f"# 文档{i}\n\n知识库检索要点第{i}条说明。\n")
    first = _search(session, "owner-1", "知识库检索", top_k=2)
    second = _search(session, "owner-1", "知识库检索", top_k=2)
    assert len(first) == 2
    assert [h["chunk_id"] for h in first] == [h["chunk_id"] for h in second]
    assert first[0]["score"] >= first[1]["score"]


def test_search_can_scope_to_document_ids(session):
    _seed(session, "owner-1", "a.md", "# A\n\n知识库甲文档。\n")
    _seed(session, "owner-1", "b.md", "# B\n\n知识库乙文档。\n")
    docs = KnowledgeIngestService(session, AuditService(session)).list_documents(
        Actor.owner("owner-1"), owner_id="owner-1"
    )
    target = next(d for d in docs if d.name == "a.md")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="知识库",
        top_k=5, document_ids=[target.id],
    )
    assert hits and all(h["doc_id"] == target.id for h in hits)


def test_search_no_hit_returns_empty_not_fabricated(session):
    _seed(session, "owner-1", "小屋玩法.md", CABIN_MD)
    assert _search(session, "owner-1", "区块链共识算法") == []
