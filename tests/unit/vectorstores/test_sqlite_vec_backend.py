"""补齐包2 · A-向量库-02：SQLite + sqlite-vec 内嵌后端单测。

验收点：
* vec0 虚表**运行时 CREATE VIRTUAL TABLE**（不经 alembic、不碰 db/models.py）；
* 近邻查询按相似度降序；owner 分区隔离（跨 owner 物理不可见）；
* metadata 过滤（doc_ids）；delete/upsert 幂等；维度校验；
* sqlite-vec 缺失时优雅降级（is_available=False，读接口空、写接口 0）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.vectorstores import (
    VectorFilterUnsupported,
    VectorRecord,
)
from find_yourself.services.knowledge.vectorstores import sqlite_vec_backend as backend_mod
from find_yourself.services.knowledge.vectorstores.sqlite_vec_backend import SqliteVecStore

pytestmark = pytest.mark.skipif(
    not backend_mod.HAS_SQLITE_VEC, reason="sqlite-vec not installed"
)


def _store(session, dim: int = 8, table: str = "kb_chunks_vec") -> SqliteVecStore:
    return SqliteVecStore(session, dim=dim, table_name=table)


def _rec(chunk_id: str, doc_id: str, vec: list[float], **meta) -> VectorRecord:
    return VectorRecord(chunk_id=chunk_id, doc_id=doc_id, embedding=vec, metadata=meta)


def test_vec0_virtual_table_created_at_runtime(session):
    """vec0 表由后端运行时惰性创建；不依赖任何 migration 产物。"""
    store = _store(session)
    assert store.is_available()
    assert store.add([_rec("c1", "d1", [1.0] * 8)], owner_id="o1") == 1
    row = session.execute(
        __import__("sqlalchemy").text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='kb_chunks_vec'"
        )
    ).scalar()
    assert row is not None
    assert "vec0" in row and "FLOAT[8]" in row
    # cos 距离度量已声明
    assert "cosine" in row


def test_knn_returns_nearest_first_with_scores(session):
    store = _store(session)
    store.add(
        [
            _rec("far", "d1", [0.0, 1.0, 0, 0, 0, 0, 0, 0]),
            _rec("near", "d1", [1.0, 0.0, 0, 0, 0, 0, 0, 0]),
            _rec("mid", "d1", [0.7, 0.7, 0, 0, 0, 0, 0, 0]),
        ],
        owner_id="o1",
    )
    hits = store.query([1.0, 0.0, 0, 0, 0, 0, 0, 0], owner_id="o1", top_k=3)
    assert [h.chunk_id for h in hits] == ["near", "mid", "far"]
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    assert hits[0].distance == pytest.approx(0.0, abs=1e-3)
    assert hits[2].score < hits[1].score < hits[0].score
    # score ∈ [0,1]，distance 原始值保留
    assert all(0.0 <= h.score <= 1.0 for h in hits)


def test_query_respects_top_k(session):
    store = _store(session)
    vecs = [
        [1.0, 0.0, 0, 0, 0, 0, 0, 0],
        [0.8, 0.6, 0, 0, 0, 0, 0, 0],
        [0.6, 0.8, 0, 0, 0, 0, 0, 0],
        [0.4, 0.9, 0, 0, 0, 0, 0, 0],
        [0.2, 0.98, 0, 0, 0, 0, 0, 0],
    ]
    store.add([_rec(f"c{i}", "d1", v) for i, v in enumerate(vecs)], owner_id="o1")
    hits = store.query([1.0, 0, 0, 0, 0, 0, 0, 0], owner_id="o1", top_k=2)
    assert len(hits) == 2
    assert [h.chunk_id for h in hits] == ["c0", "c1"]


def test_owner_partition_isolation_is_physical(session):
    """owner 是 vec0 分区键：跨 owner 的行物理上不进 KNN 候选。"""
    store = _store(session)
    same_vec = [1.0, 0.0, 0, 0, 0, 0, 0, 0]
    store.add([_rec("mine", "d1", same_vec)], owner_id="o1")
    store.add([_rec("theirs", "d9", same_vec)], owner_id="o2")
    mine = store.query(same_vec, owner_id="o1", top_k=10)
    theirs = store.query(same_vec, owner_id="o2", top_k=10)
    assert [h.chunk_id for h in mine] == ["mine"]
    assert [h.chunk_id for h in theirs] == ["theirs"]
    assert store.count(owner_id="o1") == 1 and store.count(owner_id="o2") == 1


def test_metadata_filter_by_doc_ids(session):
    store = _store(session)
    store.add(
        [
            _rec("a", "docA", [1.0, 0, 0, 0, 0, 0, 0, 0]),
            _rec("b", "docB", [0.9, 0.1, 0, 0, 0, 0, 0, 0]),
        ],
        owner_id="o1",
    )
    hits = store.query(
        [1.0, 0, 0, 0, 0, 0, 0, 0], owner_id="o1", top_k=5, filters={"doc_ids": ["docB"]}
    )
    assert [h.chunk_id for h in hits] == ["b"]


def test_unsupported_filter_key_fails_loudly(session):
    store = _store(session)
    with pytest.raises(VectorFilterUnsupported):
        store.query(
            [1.0, 0, 0, 0, 0, 0, 0, 0],
            owner_id="o1",
            filters={"evil_key": ["x"]},
        )


def test_delete_is_owner_scoped(session):
    store = _store(session)
    same_vec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    store.add([_rec("c1", "d1", same_vec)], owner_id="o1")
    store.add([_rec("c1", "d1", same_vec)], owner_id="o2")
    # o1 删自己的 c1，绝不动 o2 的同名 chunk
    assert store.delete(["c1"], owner_id="o1") == 1
    assert store.count(owner_id="o1") == 0
    assert store.count(owner_id="o2") == 1


def test_upsert_is_idempotent_no_duplicates(session):
    store = _store(session)
    vec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    store.add([_rec("c1", "d1", vec)], owner_id="o1")
    store.upsert([_rec("c1", "d1", vec)], owner_id="o1")  # 同 id 重写
    store.upsert([_rec("c2", "d1", [0.5, 0.5, 0, 0, 0, 0, 0, 0])], owner_id="o1")
    assert store.count(owner_id="o1") == 2
    assert store.list_chunk_ids(owner_id="o1") == {"c1", "c2"}


def test_dim_mismatch_raises_not_silently_dropped(session):
    store = _store(session)
    with pytest.raises(ValidationFailed, match="dim 4 != table dim 8"):
        store.add([_rec("bad", "d1", [1.0] * 4)], owner_id="o1")
    with pytest.raises(ValidationFailed, match="dim 16 != table dim 8"):
        store.query([1.0] * 16, owner_id="o1")
    with pytest.raises(ValidationFailed, match="requires an owner_id"):
        store.query([1.0] * 8, owner_id="")


def test_dim_drift_rebuilds_derived_index(session):
    """维度漂移 → 派生索引 DROP 重建（源数据在 kb_chunks，回填由 ensure 负责）。"""
    store8 = _store(session, dim=8)
    store8.add([_rec("c1", "d1", [1.0] * 8)], owner_id="o1")
    assert store8.count(owner_id="o1") == 1
    store16 = _store(session, dim=16)
    assert store16.count(owner_id="o1") == 0  # 旧表已被重建
    assert store16.add([_rec("c1", "d1", [1.0] * 16)], owner_id="o1") == 1
    assert store16.count(owner_id="o1") == 1
    # 旧维度的 store 再查：发现维度不符 → 视为表不匹配，返回空（由调用方对账）
    assert store8.count(owner_id="o1") == 0


def test_graceful_degradation_when_extension_missing(session, monkeypatch):
    """sqlite-vec 包缺失 → is_available False；读空、写 0，绝不抛异常中断检索。"""
    monkeypatch.setattr(backend_mod, "HAS_SQLITE_VEC", False)
    store = _store(session)
    assert store.is_available() is False
    assert store.query([1.0] * 8, owner_id="o1") == []
    assert store.add([_rec("c1", "d1", [1.0] * 8)], owner_id="o1") == 0
    assert store.count(owner_id="o1") == 0
    assert store.list_chunk_ids(owner_id="o1") == set()


def test_empty_query_on_missing_table_returns_empty(session):
    store = _store(session)
    assert store.is_available()
    # 表尚未创建（只读路径不副作用建表）→ 空结果
    assert store.query([1.0] * 8, owner_id="o1") == []
    assert store.count() == 0
