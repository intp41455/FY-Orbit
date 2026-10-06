"""补齐包2 · A-向量库-07/08：混合检索（向量召回 + 双路融合 + Rerank）单测。

验收点：
* 双路召回 → 融合（RRF/加权可配置）→ 可对比（include_debug A/B 数据）；
* 召回率对比：词法护栏误杀的近义表述，向量路/混合路能救回（附对比表）；
* 向后兼容：不带新参数的调用形状与 v1 完全一致；
* 降级诚实：向量路不可用 → 词法结果 + warnings；无命中 → 空列表；
* 向量影子索引惰性对账：增量回填 + stale 清理；owner 隔离与文档过滤不破。
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
    LexicalReranker,
    NoopReranker,
    create_reranker,
    ensure_vector_index,
    list_rerankers,
    rrf_fuse,
    score_chunk,
    weighted_fuse,
)
from find_yourself.services.knowledge.vectorstores import (
    SqliteVecStore,
    create_vector_store,
    register_vector_store,
)
from find_yourself.services.knowledge.embeddings import HashingEmbedding

# 语料：query 用近义表述（词法覆盖率 < MIN_QUERY_COVERAGE=0.34 → 词法路必须 miss，
# hash 嵌入共享词形 → 向量路命中），验证双路互补。
CORPUS = {
    "共识.md": "# 共识\n\n共识算法由多数派节点确认，出现故障时可能分叉。\n",
    "背包.md": "# 背包\n\n背包容量不足时，可以购买扩容道具提升背包空间。\n",
    "采集.md": "# 采集\n\n在地图上采集材料之后，可以在工作台合成新的道具。\n",
    "天气.md": "# 天气\n\n明天的天气预报说傍晚有雨，出行请带伞。\n",
}
PARAPHRASE_CASES = [
    ("区块链共识算法的原理是什么", "共识.md"),
    ("背包的容量太小了怎么才能扩容", "背包.md"),
]
EXACT_CASES = [
    ("采集玩法", "采集.md"),
    ("天气预报出行带伞", "天气.md"),
]


def _seed(session, owner_id: str, name: str, text: str):
    return KnowledgeIngestService(session, AuditService(session)).ingest_text(
        Actor.owner(owner_id), owner_id=owner_id, name=name, text=text
    )


def _seed_corpus(session, owner_id: str = "owner-1"):
    for name, text in CORPUS.items():
        _seed(session, owner_id, name, text)


def _recall_at_k(hits: list[dict], doc_name: str, k: int) -> bool:
    return any(h.get("doc_name") == doc_name for h in hits[:k])


# --- 向后兼容 ------------------------------------------------------------------ #

def test_legacy_call_shape_is_preserved(session):
    """不带新参数：返回 list[dict]，字段与 v1 完全一致（包6/既有消费方零改动）。"""
    _seed_corpus(session)
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", top_k=5
    )
    assert isinstance(hits, list) and hits
    item = hits[0]
    for key in ("chunk_id", "doc_id", "doc_name", "source", "seq", "content",
                "content_hash", "score", "matched_terms"):
        assert key in item
    assert item["doc_name"] == "采集.md"
    assert item["score"] > 0 and item["matched_terms"]
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_mode_lexical_matches_v1_semantics(session):
    _seed_corpus(session)
    service = KnowledgeSearchService(session)
    lexical = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", mode="lexical"
    )
    # 纯词法模式：不带任何向量融合字段（v1 形状原样），且命中词法护栏内内容
    assert lexical
    assert all("vector_score" not in h and "lexical_score" not in h for h in lexical)
    assert all(h["score"] > 0 and h["matched_terms"] for h in lexical)


# --- 召回率对比（词法 vs 向量 vs 混合） ------------------------------------------- #

def test_recall_comparison_table_lexical_vs_vector_vs_hybrid(session, capsys):
    """核心 A/B：词法护栏（MIN_QUERY_COVERAGE）误杀的近义表述，向量/混合路救回。

    断言：
    1. 精确关键词 query：词法与混合都命中（混合不劣化已达部分）；
    2. 近义改写 query：词法 miss、向量 hit、混合 hit（互补成立）；
    3. 对比表打印到 stdout（交付报告引用该数据）。
    """
    _seed_corpus(session)
    service = KnowledgeSearchService(session)
    actor = Actor.owner("owner-1")
    rows = []
    for label, cases, k in (("exact", EXACT_CASES, 3), ("paraphrase", PARAPHRASE_CASES, 3)):
        for query, target in cases:
            lex = service.search(actor, owner_id="owner-1", query=query, top_k=k, mode="lexical")
            vec = service.search(actor, owner_id="owner-1", query=query, top_k=k, mode="vector")
            hyb = service.search(actor, owner_id="owner-1", query=query, top_k=k, mode="hybrid")
            rows.append({
                "query": query, "type": label, "target": target,
                "lexical": int(_recall_at_k(lex, target, k)),
                "vector": int(_recall_at_k(vec, target, k)),
                "hybrid": int(_recall_at_k(hyb, target, k)),
            })
    print("\n== 混合检索召回率对比（recall@3，owner-1，hash 符号嵌入 dim=512 n_hash=2）==")
    print(f"{'query':<24} {'type':<10} {'lexical':>7} {'vector':>7} {'hybrid':>7}")
    for r in rows:
        print(f"{r['query']:<24} {r['type']:<10} {r['lexical']:>7} {r['vector']:>7} {r['hybrid']:>7}")
    lex_total = sum(r["lexical"] for r in rows)
    vec_total = sum(r["vector"] for r in rows)
    hyb_total = sum(r["hybrid"] for r in rows)
    print(f"{'TOTAL':<35} {lex_total:>7}/{len(rows)} {vec_total:>7}/{len(rows)} {hyb_total:>7}/{len(rows)}")
    assert hyb_total >= lex_total  # 混合不劣化
    for r in rows:
        if r["type"] == "paraphrase":
            assert r["lexical"] == 0, f"词法护栏应当拦截近义改写：{r['query']}"
            assert r["vector"] == 1, f"向量路应救回近义改写：{r['query']}"
            assert r["hybrid"] == 1, f"混合路应救回近义改写：{r['query']}"
        else:
            assert r["lexical"] == 1 and r["hybrid"] == 1


def test_hybrid_fused_items_carry_both_path_scores(session):
    _seed_corpus(session)
    payload = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法",
        mode="hybrid", include_debug=True,
    )
    assert payload["mode"] == "hybrid" and payload["fusion"] == "rrf"
    for hit in payload["results"]:
        assert hit["score"] > 0
        assert "lexical_score" in hit and "vector_score" in hit
        assert hit["lexical_score"] is not None or hit["vector_score"] is not None


# --- 融合公式 ------------------------------------------------------------------- #

def test_rrf_fusion_math():
    lex = [
        {"chunk_id": "a", "content": "x", "score": 10.0},
        {"chunk_id": "b", "content": "y", "score": 1.0},
    ]
    vec = [
        {"chunk_id": "b", "content": "y", "score": 0.9},
        {"chunk_id": "c", "content": "z", "score": 0.5},
    ]
    fused = rrf_fuse(lex, vec, k=1)
    by_id = {h["chunk_id"]: h for h in fused}
    # a: 1/(1+1)=0.5；b: 1/(1+2)+1/(1+1)≈0.833；c: 1/(1+2)≈0.333
    assert by_id["b"]["score"] > by_id["a"]["score"] > by_id["c"]["score"]
    assert by_id["a"]["lexical_score"] == 10.0 and by_id["a"]["vector_score"] is None
    assert by_id["b"]["lexical_score"] == 1.0 and by_id["b"]["vector_score"] == 0.9
    assert [h["chunk_id"] for h in fused] == ["b", "a", "c"]


def test_weighted_fusion_math():
    lex = [
        {"chunk_id": "a", "content": "x", "score": 10.0},
        {"chunk_id": "b", "content": "y", "score": 1.0},
    ]
    vec = [
        {"chunk_id": "b", "content": "y", "score": 0.9},
        {"chunk_id": "c", "content": "z", "score": 0.45},
    ]
    fused = weighted_fuse(lex, vec, weights=(1.0, 1.0))
    by_id = {h["chunk_id"]: h for h in fused}
    # 词法归一：a=1, b=0；向量归一：b=1, c=0 → a=1.0, b=1.0, c=0.0
    assert by_id["a"]["score"] == pytest.approx(1.0)
    assert by_id["b"]["score"] == pytest.approx(1.0)
    assert by_id["c"]["score"] == pytest.approx(0.0)
    # 等分并列 → 长块优先、id 升序
    assert [h["chunk_id"] for h in fused][:2] == ["a", "b"]


def test_fusion_formula_is_configurable_via_env_and_args(session, monkeypatch):
    _seed_corpus(session)
    service = KnowledgeSearchService(session)
    actor = Actor.owner("owner-1")
    rrf_payload = service.search(actor, owner_id="owner-1", query="采集玩法",
                                 mode="hybrid", fusion="rrf", include_debug=True)
    weighted_payload = service.search(actor, owner_id="owner-1", query="采集玩法",
                                      mode="hybrid", fusion="weighted", include_debug=True)
    assert rrf_payload["fusion"] == "rrf" and weighted_payload["fusion"] == "weighted"
    # 环境变量同样生效
    monkeypatch.setenv("FIND_YOURSELF_KB_FUSION", "weighted")
    env_payload = service.search(actor, owner_id="owner-1", query="采集玩法",
                                 mode="hybrid", include_debug=True)
    assert env_payload["fusion"] == "weighted"
    with pytest.raises(ValidationFailed):
        service.search(actor, owner_id="owner-1", query="采集玩法", fusion="bogus")
    with pytest.raises(ValidationFailed):
        service.search(actor, owner_id="owner-1", query="采集玩法", mode="bogus")


def test_rrf_k_and_weights_change_ranking():
    lex = [{"chunk_id": "a", "content": "x", "score": 5.0}]
    vec = [{"chunk_id": "b", "content": "y", "score": 0.8}]
    balanced = rrf_fuse(lex, vec, k=60, weights=(1.0, 1.0))
    lexical_biased = rrf_fuse(lex, vec, k=60, weights=(100.0, 1.0))
    assert balanced[0]["chunk_id"] == "a" == lexical_biased[0]["chunk_id"]
    vec_biased = rrf_fuse(lex, vec, k=1, weights=(0.0, 1.0))
    assert [h["chunk_id"] for h in vec_biased] == ["b", "a"]  # 词法权重 0 → 只剩向量序


# --- Rerank A/B ------------------------------------------------------------------ #

def test_rerank_registry_and_lexical_reranker_reorders():
    assert "noop" in list_rerankers() and "lexical" in list_rerankers()
    hits = [
        {"chunk_id": "noise", "content": "完全不相关的内容", "score": 0.9},
        {"chunk_id": "target", "content": "采集玩法是核心玩法，采集玩法很重要。", "score": 0.5},
    ]
    out = LexicalReranker().rerank("采集玩法", hits)
    assert [h["chunk_id"] for h in out][0] == "target"  # 词法重排把命中块提上来
    assert out[0]["rerank_score"] > 0
    # 成员守恒：重排不增删
    assert {h["chunk_id"] for h in out} == {"noise", "target"}
    assert NoopReranker().rerank("q", hits) == hits


def test_rerank_ab_compare_via_include_debug(session):
    """重排前后对比：debug 记录 before/after 序列，成员守恒。"""
    _seed_corpus(session)
    payload = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法",
        mode="hybrid", reranker=LexicalReranker(), include_debug=True,
    )
    rr = payload["debug"]["rerank"]
    assert rr["name"] == "lexical"
    assert rr["before"] and rr["after"]
    assert set(rr["before"]) == set(rr["after"])  # 保成员重排
    assert isinstance(rr["changed"], bool)


# --- 降级与诚实性 ------------------------------------------------------------------ #

def test_hybrid_degrades_to_lexical_when_backend_unavailable(session, monkeypatch):
    class _BrokenStore(SqliteVecStore):
        name = "broken"

        def is_available(self):
            return False

    register_vector_store("broken", _BrokenStore, override=True)
    monkeypatch.setenv("FIND_YOURSELF_KB_VECTOR_BACKEND", "broken")
    _seed_corpus(session)
    payload = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法",
        mode="hybrid", include_debug=True,
    )
    assert payload["debug"]["backend"] == "broken"
    assert "vector_backend_unavailable" in payload["debug"]["warnings"]
    # 词法路结果仍在：降级不是失败，也不伪造空
    assert payload["results"]
    assert all(h["matched_terms"] for h in payload["results"])


def test_full_chain_runs_with_network_blocked(session, monkeypatch):
    """断网全链路：入库 -> 影子索引 -> 双路召回 -> RRF 融合 -> 词法重排，全程零外部服务。

    这是「离线优先」的硬证明：默认后端（sqlite-vec 内嵌）+ 默认嵌入（hash）都是
    纯本地实现，任何 socket 建连尝试都直接抛错，全链路仍必须跑通并给出正确结果。
    """
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("离线链路不允许任何网络访问")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    # 远程嵌入即便配了 key 也不得被启用：断网下必须仍然走 hash
    monkeypatch.delenv("FIND_YOURSELF_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _seed_corpus(session)
    report = ensure_vector_index(session, "owner-1")
    assert report["indexed"] == 4 and report["purged"] == 0
    assert report["backend"] == "sqlite_vec" and report["embedding"] == "hash"

    payload = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="区块链共识算法的原理是什么",
        top_k=3, mode="hybrid", fusion="rrf", reranker=create_reranker("lexical"),
        include_debug=True,
    )
    assert payload["debug"]["warnings"] == []  # 无降级告警 = 全链路本地跑通
    assert payload["debug"]["vector_hits"], "断网下向量路仍须有召回"
    assert payload["results"][0]["doc_name"] == "共识.md"
    # 重排契约：保成员（只换顺序不增删条目），断网下同样成立
    rr = payload["debug"]["rerank"]
    assert rr["name"] == "lexical" and set(rr["before"]) == set(rr["after"])


def test_hybrid_no_hit_returns_empty_not_fabricated(session):
    _seed(session, "owner-1", "小屋玩法.md", "# 小屋玩法\n\n背景探险包含采集玩法与日常任务。\n")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="量子纠缠退相干时间"
    )
    assert hits == []  # 向量相似度低于下限 + 词法护栏 → 诚实空列表


def test_hybrid_respects_owner_isolation(session):
    _seed(session, "owner-1", "共识.md", CORPUS["共识.md"])
    _seed(session, "owner-2", "背包.md", CORPUS["背包.md"])
    service = KnowledgeSearchService(session)
    mine = service.search(Actor.owner("owner-1"), owner_id="owner-1",
                          query="区块链共识算法的原理是什么")
    theirs = service.search(Actor.owner("owner-2"), owner_id="owner-2",
                            query="区块链共识算法的原理是什么")
    assert mine and all(h["doc_name"] == "共识.md" for h in mine)
    assert theirs == []  # owner-2 只有背包文档，无论词法还是向量都不可见 owner-1 内容


def test_hybrid_respects_document_ids_scoping(session):
    _seed_corpus(session)
    docs = KnowledgeIngestService(session, AuditService(session)).list_documents(
        Actor.owner("owner-1"), owner_id="owner-1"
    )
    target = next(d for d in docs if d.name == "共识.md")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1",
        query="区块链共识算法的原理是什么", document_ids=[target.id],
    )
    assert hits and all(h["doc_id"] == target.id for h in hits)


# --- 向量影子索引 ------------------------------------------------------------------ #

def test_ensure_vector_index_is_incremental_and_purges_stale(session):
    _seed_corpus(session, "owner-1")
    store = create_vector_store(session=session, dim=HashingEmbedding().dim)
    embedding = HashingEmbedding(dim=HashingEmbedding().dim)
    report1 = ensure_vector_index(session, "owner-1", store=store, embedding=embedding)
    assert report1["indexed"] > 0 and report1["total"] > 0
    assert report1["backend"] == "sqlite_vec" and report1["embedding"] == "hash"
    ids_after_first = store.list_chunk_ids(owner_id="owner-1")
    # 幂等：再跑一次不重复灌
    report2 = ensure_vector_index(session, "owner-1", store=store, embedding=embedding)
    assert report2["indexed"] == 0 and report2["purged"] == 0
    assert store.list_chunk_ids(owner_id="owner-1") == ids_after_first
    # 删文档 → stale 行被清掉
    doc = next(
        d for d in KnowledgeIngestService(session, AuditService(session)).list_documents(
            Actor.owner("owner-1"), owner_id="owner-1"
        )
        if d.name == "天气.md"
    )
    KnowledgeIngestService(session, AuditService(session)).delete_document(
        Actor.owner("owner-1"), doc.id
    )
    report3 = ensure_vector_index(session, "owner-1", store=store, embedding=embedding)
    assert report3["purged"] > 0
    remaining = store.list_chunk_ids(owner_id="owner-1")
    assert len(remaining) == len(ids_after_first) - report3["purged"]


def test_vector_floor_is_env_tunable(session, monkeypatch):
    _seed_corpus(session)
    monkeypatch.setenv("FIND_YOURSELF_KB_VECTOR_FLOOR", "0.99")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", mode="vector"
    )
    assert hits == []  # 下限抬高 → 全部按噪声丢弃（诚实空）
