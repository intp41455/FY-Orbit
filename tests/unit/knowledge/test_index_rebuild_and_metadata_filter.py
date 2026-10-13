"""补齐需求 N6（对标 FastGPT）：知识库管理侧的 FTS5 索引重建 + 检索期 metadata 过滤。

验收点：
* ``ensure_fts_index``：FTS5 影子索引的惰性对账——缺多少补多少、stale 行清除、
  幂等（与 ``ensure_vector_index`` 同构的返回契约 ``{"indexed","purged","total"}``）；
* metadata 过滤（``sources`` / ``created_after`` / ``created_before``）：词法路与
  向量路**都**在排序之前收窄，被过滤掉的切片不进融合、不占 top_k；
* 诚实边界：FTS5 不可用时返回 ``skipped`` 而非抛异常（检索自动回退 LIKE 仍可用）；
  不传过滤条件时行为与 v1 逐字一致（向后兼容）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as sql_text

import find_yourself.db.kb_models  # noqa: F401
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.ingest import KnowledgeIngestService
from find_yourself.services.knowledge.search import (
    FTS_TABLE,
    KnowledgeSearchService,
    delete_chunks_fts,
    ensure_fts_index,
)

CORPUS = {
    "共识.md": "# 共识\n\n共识算法由多数派节点确认，出现故障时可能分叉。\n",
    "背包.md": "# 背包\n\n背包容量不足时，可以购买扩容道具提升背包空间。\n",
    "采集.md": "# 采集\n\n在地图上采集材料之后，可以在工作台合成新的道具。\n",
    "天气.md": "# 天气\n\n明天的天气预报说傍晚有雨，出行请带伞。\n",
}


def _seed(session, owner_id: str, name: str, text: str, *, source: str = "local"):
    return KnowledgeIngestService(session, AuditService(session)).ingest_text(
        Actor.owner(owner_id), owner_id=owner_id, name=name, text=text, source=source
    )


def _seed_corpus(session, owner_id: str = "owner-1", *, source: str = "local"):
    for name, text in CORPUS.items():
        _seed(session, owner_id, name, text, source=source)


def _fts_ids(session, owner_id: str) -> set[str]:
    return {
        r[0]
        for r in session.execute(
            sql_text(f"SELECT chunk_id FROM {FTS_TABLE} WHERE owner_id = :owner"),
            {"owner": owner_id},
        ).all()
    }


def _docs(session, owner_id: str = "owner-1"):
    return KnowledgeIngestService(session, AuditService(session)).list_documents(
        Actor.owner(owner_id), owner_id=owner_id
    )


# --- FTS5 影子索引重建（ensure_fts_index） --------------------------------- #

def test_ensure_fts_index_is_idempotent_and_purges_stale(session):
    """七段式：空操作 → 断表全量回填 → 幂等 → 删文档 → 清 stale → 新入库不重复灌
    → 只丢局部影子行时精确增量补齐。"""
    _seed_corpus(session, "owner-1")

    # 1) 入库期已增量写好 FTS 影子表：这里必须是空操作（不重复灌）。
    report = ensure_fts_index(session, "owner-1")
    assert report["indexed"] == 0 and report["purged"] == 0
    assert report["total"] == 4 and "skipped" not in report

    # 2) 影子表被清空（迁移丢表 / 人工清库）→ 惰性对账全量回填。
    session.execute(sql_text(f"DELETE FROM {FTS_TABLE}"))
    session.flush()
    assert _fts_ids(session, "owner-1") == set()
    rebuilt = ensure_fts_index(session, "owner-1")
    assert rebuilt["indexed"] == 4 and rebuilt["purged"] == 0
    ids_after_rebuild = _fts_ids(session, "owner-1")
    assert len(ids_after_rebuild) == 4

    # 3) 幂等：再跑一次既不补也不删。
    again = ensure_fts_index(session, "owner-1")
    assert again["indexed"] == 0 and again["purged"] == 0
    assert _fts_ids(session, "owner-1") == ids_after_rebuild

    # 4) 删文档：ingest 的 delete_document 自身已清掉该文档影子行，对账只补漏不误删。
    victim = next(d for d in _docs(session) if d.name == "天气.md")
    victim_ids = {r[0] for r in _chunk_ids_of(session, victim.id)}
    KnowledgeIngestService(session, AuditService(session)).delete_document(
        Actor.owner("owner-1"), victim.id
    )
    after_delete = ensure_fts_index(session, "owner-1")
    assert after_delete["indexed"] == 0 and after_delete["purged"] == 0
    assert _fts_ids(session, "owner-1") == ids_after_rebuild - victim_ids

    # 5) 人为造一条 stale 影子行（模拟历史数据漂移/重导残留）→ 对账必须清掉。
    session.execute(
        sql_text(
            f"INSERT INTO {FTS_TABLE} (chunk_id, doc_id, owner_id, content) "
            "VALUES (:cid, :did, :oid, :content)"
        ),
        {"cid": "ghost-chunk", "did": "ghost-doc", "oid": "owner-1", "content": "残留内容"},
    )
    session.flush()
    assert "ghost-chunk" in _fts_ids(session, "owner-1")
    healed_stale = ensure_fts_index(session, "owner-1")
    assert healed_stale["purged"] == 1 and healed_stale["indexed"] == 0
    assert "ghost-chunk" not in _fts_ids(session, "owner-1")
    base_ids = _fts_ids(session, "owner-1")

    # 6) 新入库文档：入库期已增量写好影子表，对账不得重复灌（indexed 必须为 0）。
    _seed(session, "owner-1", "小屋.md", "# 小屋\n\n小屋可以放置采集台与背包柜。\n")
    grown = ensure_fts_index(session, "owner-1")
    assert grown["indexed"] == 0 and grown["purged"] == 0
    new_ids = {r[0] for r in _chunk_ids_of(session, _doc_id(session, "小屋.md"))}
    assert new_ids and new_ids <= _fts_ids(session, "owner-1")

    # 7) 只丢新文档的影子行 → 增量对账精确补这几条，其余分毫不动。
    assert delete_chunks_fts(session, sorted(new_ids)) is True
    assert _fts_ids(session, "owner-1").isdisjoint(new_ids)
    healed = ensure_fts_index(session, "owner-1")
    assert healed["indexed"] == len(new_ids) and healed["purged"] == 0
    assert _fts_ids(session, "owner-1") == base_ids | new_ids


def _doc_id(session, name: str, owner_id: str = "owner-1") -> str:
    row = session.execute(
        sql_text(
            "SELECT id FROM kb_documents WHERE name = :n AND owner_id = :o"
        ),
        {"n": name, "o": owner_id},
    ).first()
    return row[0]


def _chunk_ids_of(session, doc_id: str) -> list:
    return list(
        session.execute(
            sql_text("SELECT id FROM kb_chunks WHERE doc_id = :doc"), {"doc": doc_id}
        ).all()
    )


def test_ensure_fts_index_requires_owner(session):
    _seed_corpus(session, "owner-1")
    with pytest.raises(ValidationFailed):
        ensure_fts_index(session, "")


def test_ensure_fts_index_is_owner_scoped(session):
    """只补自己 owner 的行：owner-2 的缺口不会被 owner-1 的对账顺手填掉。"""
    _seed_corpus(session, "owner-1")
    _seed(session, "owner-2", "共识.md", CORPUS["共识.md"])
    session.execute(sql_text(f"DELETE FROM {FTS_TABLE} WHERE owner_id = :o"), {"o": "owner-2"})
    session.flush()

    report = ensure_fts_index(session, "owner-1")
    assert report["indexed"] == 0  # owner-1 索引完好，不该动
    assert _fts_ids(session, "owner-2") == set()

    report2 = ensure_fts_index(session, "owner-2")
    assert report2["indexed"] == 1
    assert len(_fts_ids(session, "owner-2")) == 1


def test_ensure_fts_index_reports_skipped_when_fts5_unavailable(session, monkeypatch):
    """FTS5 不可用：返回 skipped 的空操作结果，不抛异常（检索仍可退 LIKE）。"""
    _seed_corpus(session, "owner-1")
    monkeypatch.setattr(
        "find_yourself.services.knowledge.search.ensure_fts_table", lambda _s: False
    )
    report = ensure_fts_index(session, "owner-1")
    assert report["indexed"] == 0 and report["purged"] == 0
    assert report["skipped"] == "fts5_unavailable"
    # 诚实性：索引没建不代表检索失败——词法路照常有结果。
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", mode="lexical"
    )
    assert hits and hits[0]["doc_name"] == "采集.md"


def test_delete_chunks_fts_actually_deletes_rows(session):
    """回归：``IN :ids`` 在 SQLAlchemy 2.x 传普通 tuple 不会展开成 IN 列表，
    语句报错后被裸 ``except`` 吞掉 → 删除静默失效、FTS 留下永不消失的
    幽灵行（文档已删却仍可被 FTS 召回）。必须用 expanding bindparam。"""
    _seed_corpus(session, "owner-1")
    ids = sorted(_fts_ids(session, "owner-1"))
    assert len(ids) == 4

    # 单条
    assert delete_chunks_fts(session, [ids[0]]) is True
    assert _fts_ids(session, "owner-1") == set(ids[1:])
    # 多条（expanding 占位符路径）
    assert delete_chunks_fts(session, ids[1:]) is True
    assert _fts_ids(session, "owner-1") == set()
    # 空输入是安全的空操作
    assert delete_chunks_fts(session, []) is False


def test_fts_search_still_correct_after_rebuild(session):
    """重建后检索结果与重建前一致（影子表是可再生派生数据，重建零风险）。"""
    _seed_corpus(session, "owner-1")
    service = KnowledgeSearchService(session)
    before = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="天气预报出行带伞", top_k=3
    )
    session.execute(sql_text(f"DELETE FROM {FTS_TABLE}"))
    session.flush()
    assert ensure_fts_index(session, "owner-1")["indexed"] == 4
    after = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="天气预报出行带伞", top_k=3
    )
    assert [h["chunk_id"] for h in before] == [h["chunk_id"] for h in after]
    assert after[0]["doc_name"] == "天气.md"


# --- metadata 过滤（sources / 时间窗） --------------------------------------- #

def test_sources_filter_scopes_results(session):
    """按 kb_documents.source 过滤：只保留指定来源的文档切片。"""
    _seed(session, "owner-1", "共识.md", CORPUS["共识.md"], source="local")
    _seed(session, "owner-1", "共识-云盘.md", CORPUS["共识.md"], source="baidu_pan")
    service = KnowledgeSearchService(session)

    every = service.search(Actor.owner("owner-1"), owner_id="owner-1", query="共识算法")
    assert {h["source"] for h in every} == {"local", "baidu_pan"}

    only_pan = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="共识算法", sources=["baidu_pan"]
    )
    assert only_pan and all(h["source"] == "baidu_pan" for h in only_pan)
    assert all(h["doc_name"] == "共识-云盘.md" for h in only_pan)


def test_sources_filter_applies_to_vector_lane_too(session):
    """向量路同样受 metadata 约束：复核发生在构造结果之前，不靠 top_k 截断。"""
    _seed(session, "owner-1", "采集.md", CORPUS["采集.md"], source="local")
    _seed(session, "owner-1", "采集-云盘.md", CORPUS["采集.md"], source="ima")
    service = KnowledgeSearchService(session)

    payload = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", mode="hybrid",
        sources=["ima"], include_debug=True,
    )
    assert payload["results"], "向量/混合路在 metadata 过滤后仍应保召回"
    assert all(h["source"] == "ima" for h in payload["results"])
    # 向量命中集本身也已被收窄：绝不可能出现 local 文档的 chunk_id。
    # （子集而非相等：top_k 截断可能砍掉部分向量命中）
    assert set(payload["debug"]["vector_hits"]) <= {h["chunk_id"] for h in payload["results"]}
    assert payload["debug"]["vector_hits"], "hybrid 模式向量路应确有命中（否则断言无意义）"


def test_sources_filter_unknown_source_returns_empty_not_error(session):
    """未知 source 值：诚实空列表（不静默放宽成「不过滤」）。"""
    _seed_corpus(session, "owner-1")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", sources=["nonexistent"]
    )
    assert hits == []


def test_created_range_filter_scopes_results(session):
    """按 kb_documents.created_at 时间窗过滤：窗口外文档不可见。"""
    old = _seed(session, "owner-1", "旧共识.md", CORPUS["共识.md"])
    fresh = _seed(session, "owner-1", "新共识.md", CORPUS["共识.md"])
    session.execute(
        sql_text("UPDATE kb_documents SET created_at = :ts WHERE id = :id"),
        {"ts": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
         "id": old.id},
    )
    session.execute(
        sql_text("UPDATE kb_documents SET created_at = :ts WHERE id = :id"),
        {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
         "id": fresh.id},
    )
    session.flush()
    service = KnowledgeSearchService(session)

    recent_only = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="共识算法",
        created_after=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert recent_only and all(h["doc_id"] == fresh.id for h in recent_only)

    both = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="共识算法",
        created_after=datetime.now(timezone.utc) - timedelta(days=365),
    )
    assert {h["doc_id"] for h in both} == {old.id, fresh.id}


def test_metadata_filters_compose_and_empty_values_mean_no_filter(session):
    """source + 时间窗可叠加；sources=[] 视为「不过滤」，而不是「匹配空集」。"""
    _seed_corpus(session, "owner-1")
    service = KnowledgeSearchService(session)
    baseline = service.search(Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法")
    empty_list = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法", sources=[]
    )
    assert [h["chunk_id"] for h in empty_list] == [h["chunk_id"] for h in baseline]

    composed = service.search(
        Actor.owner("owner-1"), owner_id="owner-1", query="采集玩法",
        sources=["local"],
        created_after=datetime.now(timezone.utc) - timedelta(days=1),
        created_before=datetime.now(timezone.utc) + timedelta(days=1),
    )
    assert {h["chunk_id"] for h in composed} == {h["chunk_id"] for h in baseline}


def test_metadata_filters_do_not_break_owner_isolation(session):
    """metadata 过滤只能收窄，绝不能放宽 owner 边界。"""
    _seed(session, "owner-1", "共识.md", CORPUS["共识.md"], source="local")
    _seed(session, "owner-2", "共识.md", CORPUS["共识.md"], source="local")
    hits = KnowledgeSearchService(session).search(
        Actor.owner("owner-1"), owner_id="owner-1", query="共识算法", sources=["local"]
    )
    assert hits
    assert {h["doc_id"] for h in hits} <= {d.id for d in _docs(session, "owner-1")}
