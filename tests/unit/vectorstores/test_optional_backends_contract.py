"""P10 · A-向量库-03~06：四个可选向量后端的**契约一致性**测试套件。

设计要点：**同一套断言跑全部后端**。只有这样才能证明四个后端在
``base.VectorStore`` 契约下行为一致——而不是各自「看起来能跑」。

覆盖的契约点（与 ``test_sqlite_vec_backend.py`` 对齐）：

* 注册表可查（四个后端已接入 + 依赖状态可读）
* 近邻查询按相似度降序、``score ∈ [0,1]``、``top_k`` 生效
* **owner 隔离**：跨 owner 的向量不进候选（物理或谓词级）
* ``doc_ids`` 元数据过滤
* 非支持的过滤键**响亮报错**（VectorFilterUnsupported）
* ``delete`` 按 owner 作用域
* ``upsert`` 幂等（同 chunk_id 不产生重复行）
* 维度不符 / 空 owner **抛异常**（bug 不是降级）
* 维度漂移 → 派生索引重建
* 依赖缺失 → ``is_available() False``、读空、写 0、**不抛异常**
* 空后端上查询返回空（只读路径不副作用建表）

后端的构造参数各不相同（``session`` / ``path`` / ``uri`` / ``index_dir``），
用 :func:`_make_store` 做适配；每个后端跑在自己的 ``tmp_path`` 上，互不污染。
"""

from __future__ import annotations

import importlib

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.vectorstores import (
    OPTIONAL_BACKEND_DEPS,
    VectorFilterUnsupported,
    VectorRecord,
    create_vector_store,
    get_vector_store_class,
    list_vector_stores,
    vector_backend_status,
)

DIM = 8
VEC_A = [1.0, 0.0, 0, 0, 0, 0, 0, 0]


# --------------------------------------------------------------------------- #
# 后端适配层
# --------------------------------------------------------------------------- #
#: 后端名 -> (模块名, 类名, 是否可用)
BACKENDS: dict[str, tuple[str, str]] = {
    "lancedb": ("lancedb_backend", "LanceDBStore"),
    "chroma": ("chroma_backend", "ChromaStore"),
    "faiss": ("faiss_backend", "FaissStore"),
    "qdrant": ("qdrant_backend", "QdrantStore"),
}


def _backend_available(name: str) -> bool:
    return bool(OPTIONAL_BACKEND_DEPS.get(name))


def _make_store(name: str, tmp_path, dim: int = DIM):
    """按后端类型构造 store（统一 dim，隔离到 tmp_path）。"""
    if name == "lancedb":
        from find_yourself.services.knowledge.vectorstores import LanceDBStore

        return LanceDBStore(dim=dim, uri=str(tmp_path / "lancedb"))
    if name == "chroma":
        from find_yourself.services.knowledge.vectorstores import ChromaStore

        return ChromaStore(dim=dim, path=str(tmp_path / "chroma"))
    if name == "faiss":
        from find_yourself.services.knowledge.vectorstores import FaissStore

        return FaissStore(dim=dim, index_dir=str(tmp_path / "faiss"))
    if name == "qdrant":
        from find_yourself.services.knowledge.vectorstores import QdrantStore

        return QdrantStore(dim=dim, path=str(tmp_path / "qdrant"))
    raise AssertionError(f"unknown backend {name}")


def _rec(chunk_id: str, doc_id: str, vec: list[float], **meta) -> VectorRecord:
    return VectorRecord(chunk_id=chunk_id, doc_id=doc_id, embedding=vec, metadata=meta)


# --------------------------------------------------------------------------- #
# 注册表（不需要后端依赖，独立断言）
# --------------------------------------------------------------------------- #
def test_all_four_backends_are_registered():
    """四个后端均已注册（import 即生效），且类名/模块可查。"""
    known = set(list_vector_stores())
    for name, (mod_name, cls_name) in BACKENDS.items():
        assert name in known, f"{name} not registered; known={sorted(known)}"
        cls = get_vector_store_class(name)
        assert cls.__name__ == cls_name
        assert cls.__module__.endswith(f"vectorstores.{mod_name}")


def test_backend_status_reports_dependency_state():
    """状态表报告每个后端的依赖可用性，且不破坏 sqlite_vec 的扩展位。"""
    status = vector_backend_status()
    assert status["sqlite_vec"]["default"] is True
    assert "extension_installed" in status["sqlite_vec"]
    for name in BACKENDS:
        entry = status[name]
        assert entry["class"] == BACKENDS[name][1]
        assert isinstance(entry["dependency_installed"], bool)
        assert entry["dependency_installed"] == _backend_available(name)


def test_create_vector_store_by_name_uses_registry(tmp_path):
    """``create_vector_store`` 按名构造，kwargs 原样透传到后端构造器。"""
    for name in BACKENDS:
        if not _backend_available(name):
            continue
        store = create_vector_store(name, dim=DIM, **_ctor_kwargs(name, tmp_path))
        assert store.name == name
        assert store.dim == DIM


def test_default_storage_names_do_not_collide_across_backends():
    """四个可选后端的默认存储名必须互不相同（含与 sqlite_vec 的默认表名）。

    **防回归**：初版 lancedb 与 sqlite_vec 都用裸 ``kb_chunks_vec``，部署方把
    LanceDB 的 uri 指到 kb 同目录时两表同名，运维无法区分归属。四个后端默认名
    均须带自身身份前缀。
    """
    from find_yourself.services.knowledge.vectorstores import (
        ChromaStore as _CHR,
    )
    from find_yourself.services.knowledge.vectorstores import (
        FaissStore as _FAI,
    )
    from find_yourself.services.knowledge.vectorstores import (
        LanceDBStore as _LDB,
    )
    from find_yourself.services.knowledge.vectorstores import (
        QdrantStore as _QDR,
    )
    from find_yourself.services.knowledge.vectorstores import sqlite_vec_backend
    from find_yourself.services.knowledge.vectorstores.chroma_backend import (
        DEFAULT_COLLECTION_PREFIX,
    )
    from find_yourself.services.knowledge.vectorstores.faiss_backend import (
        DEFAULT_INDEX_DIR,
    )
    from find_yourself.services.knowledge.vectorstores.lancedb_backend import (
        DEFAULT_TABLE as LANCEDB_TABLE,
    )
    from find_yourself.services.knowledge.vectorstores.qdrant_backend import (
        DEFAULT_COLLECTION,
        DEFAULT_LOCAL_PATH,
    )

    # 显式引用，防 import 被优化掉
    assert {_LDB.name, _CHR.name, _FAI.name, _QDR.name} == {
        "lancedb",
        "chroma",
        "faiss",
        "qdrant",
    }

    names = {
        "sqlite_vec": sqlite_vec_backend.DEFAULT_TABLE,
        "lancedb": LANCEDB_TABLE,
        "chroma": DEFAULT_COLLECTION_PREFIX,
        "qdrant": DEFAULT_COLLECTION,
        "faiss": DEFAULT_INDEX_DIR,
        "qdrant_path": DEFAULT_LOCAL_PATH,
    }
    # 无重复
    assert len(set(names.values())) == len(names), f"默认存储名冲突: {names}"
    # 表/collection 名带自身身份前缀
    assert LANCEDB_TABLE.startswith("lancedb")
    assert DEFAULT_COLLECTION_PREFIX.startswith("chroma")
    assert DEFAULT_COLLECTION.startswith("qdrant")
    assert sqlite_vec_backend.DEFAULT_TABLE != LANCEDB_TABLE


def _ctor_kwargs(name: str, tmp_path) -> dict:
    if name == "lancedb":
        return {"uri": str(tmp_path / "lancedb")}
    if name == "chroma":
        return {"path": str(tmp_path / "chroma")}
    if name == "faiss":
        return {"index_dir": str(tmp_path / "faiss")}
    if name == "qdrant":
        return {"path": str(tmp_path / "qdrant")}
    return {}


# --------------------------------------------------------------------------- #
# 契约一致性：每个后端跑同一套断言
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_knn_returns_nearest_first_with_scores(name, tmp_path):
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    assert store.is_available(), f"{name} should be available"
    store.add(
        [
            _rec("far", "d1", [0.0, 1.0, 0, 0, 0, 0, 0, 0]),
            _rec("near", "d1", [1.0, 0.0, 0, 0, 0, 0, 0, 0]),
            _rec("mid", "d1", [0.7, 0.7, 0, 0, 0, 0, 0, 0]),
        ],
        owner_id="o1",
    )
    hits = store.query(VEC_A, owner_id="o1", top_k=3)
    assert [h.chunk_id for h in hits] == ["near", "mid", "far"], f"{name} ordering wrong"
    assert hits[0].score == pytest.approx(1.0, abs=1e-2)
    assert hits[2].score < hits[1].score < hits[0].score
    assert all(0.0 <= h.score <= 1.0 for h in hits)
    assert all(h.distance >= 0.0 for h in hits)


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_query_respects_top_k(name, tmp_path):
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    vecs = [
        [1.0, 0.0, 0, 0, 0, 0, 0, 0],
        [0.8, 0.6, 0, 0, 0, 0, 0, 0],
        [0.6, 0.8, 0, 0, 0, 0, 0, 0],
        [0.4, 0.9, 0, 0, 0, 0, 0, 0],
        [0.2, 0.98, 0, 0, 0, 0, 0, 0],
    ]
    store.add([_rec(f"c{i}", "d1", v) for i, v in enumerate(vecs)], owner_id="o1")
    assert len(store.query(VEC_A, owner_id="o1", top_k=2)) == 2
    assert len(store.query(VEC_A, owner_id="o1", top_k=9)) <= 5


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_owner_isolation(name, tmp_path):
    """跨 owner 的向量不进候选——这是 base 契约的第一条（授权谓词先于排序）。"""
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    store.add([_rec("mine", "d1", VEC_A)], owner_id="o1")
    store.add([_rec("theirs", "d9", VEC_A)], owner_id="o2")
    mine = store.query(VEC_A, owner_id="o1", top_k=10)
    theirs = store.query(VEC_A, owner_id="o2", top_k=10)
    assert [h.chunk_id for h in mine] == ["mine"], f"{name} leaked other owner: {mine}"
    assert [h.chunk_id for h in theirs] == ["theirs"]
    assert store.count(owner_id="o1") == 1
    assert store.count(owner_id="o2") == 1


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_metadata_filter_by_doc_ids(name, tmp_path):
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    store.add(
        [
            _rec("a", "docA", [1.0, 0, 0, 0, 0, 0, 0, 0]),
            _rec("b", "docB", [0.9, 0.1, 0, 0, 0, 0, 0, 0]),
        ],
        owner_id="o1",
    )
    hits = store.query(VEC_A, owner_id="o1", top_k=5, filters={"doc_ids": ["docB"]})
    assert [h.chunk_id for h in hits] == ["b"], f"{name} filter leaked: {hits}"


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_unsupported_filter_key_fails_loudly(name, tmp_path):
    """非法过滤键必须抛错，不静默忽略（不假装过滤了）。"""
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    with pytest.raises(VectorFilterUnsupported):
        store.query(VEC_A, owner_id="o1", filters={"evil_key": ["x"]})


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_delete_is_owner_scoped(name, tmp_path):
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    store.add([_rec("c1", "d1", VEC_A)], owner_id="o1")
    store.add([_rec("c1", "d1", VEC_A)], owner_id="o2")
    assert store.delete(["c1"], owner_id="o1") == 1
    assert store.count(owner_id="o1") == 0
    assert store.count(owner_id="o2") == 1, f"{name} delete leaked across owners"


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_upsert_is_idempotent_no_duplicates(name, tmp_path):
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    store.add([_rec("c1", "d1", VEC_A)], owner_id="o1")
    store.upsert([_rec("c1", "d1", VEC_A)], owner_id="o1")  # 同 id 重写
    store.upsert([_rec("c2", "d1", [0.5, 0.5, 0, 0, 0, 0, 0, 0])], owner_id="o1")
    assert store.count(owner_id="o1") == 2, f"{name} upsert duplicated rows"
    assert store.list_chunk_ids(owner_id="o1") == {"c1", "c2"}


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_dim_mismatch_and_empty_owner_raise(name, tmp_path):
    """传参错误是 bug 不是降级 → 必须抛异常。"""
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    with pytest.raises(ValidationFailed, match="dim 4 != table dim 8"):
        store.add([_rec("bad", "d1", [1.0] * 4)], owner_id="o1")
    with pytest.raises(ValidationFailed, match="dim 16 != table dim 8"):
        store.query([1.0] * 16, owner_id="o1")
    with pytest.raises(ValidationFailed, match="requires an owner_id"):
        store.query([1.0] * 8, owner_id="")


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_dim_drift_rebuilds_derived_index(name, tmp_path):
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store8 = _make_store(name, tmp_path, dim=8)
    store8.add([_rec("c1", "d1", [1.0] * 8)], owner_id="o1")
    assert store8.count(owner_id="o1") == 1
    store16 = _make_store(name, tmp_path, dim=16)
    assert store16.count(owner_id="o1") == 0  # 旧索引已被重建
    assert store16.add([_rec("c1", "d1", [1.0] * 16)], owner_id="o1") == 1
    assert store16.count(owner_id="o1") == 1
    assert store8.count(owner_id="o1") == 0  # 旧维度 store 不再匹配


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_graceful_degradation_when_dependency_missing(name, tmp_path, monkeypatch):
    """依赖缺失 → is_available False；读空、写 0，绝不抛异常中断检索。"""
    mod_name, _cls_name = BACKENDS[name]
    mod = importlib.import_module(
        f"find_yourself.services.knowledge.vectorstores.{mod_name}"
    )
    flag = {
        "lancedb": "HAS_LANCEDB",
        "chroma": "HAS_CHROMADB",
        "faiss": "HAS_FAISS",
        "qdrant": "HAS_QDRANT",
    }[name]
    monkeypatch.setattr(mod, flag, False)
    store = _make_store(name, tmp_path)
    assert store.is_available() is False
    assert store.query(VEC_A, owner_id="o1") == []
    assert store.add([_rec("c1", "d1", VEC_A)], owner_id="o1") == 0
    assert store.count(owner_id="o1") == 0
    assert store.list_chunk_ids(owner_id="o1") == set()


@pytest.mark.parametrize("name", sorted(BACKENDS), ids=sorted(BACKENDS))
def test_query_on_empty_backend_returns_empty(name, tmp_path):
    """全新后端、尚无任何写入 → 查询返回空（只读路径不副作用建索引）。"""
    if not _backend_available(name):
        pytest.skip(f"{name} dependency not installed")
    store = _make_store(name, tmp_path)
    assert store.is_available()
    assert store.query(VEC_A, owner_id="o1") == []
    assert store.count() == 0
