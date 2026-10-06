"""补齐包2 · A-向量库-01：向量后端注册表单测。

验收点：
* 注册表可查（内置 sqlite_vec 已接入）；
* 自定义后端（LanceDB/Chroma/FAISS/Qdrant 的 P2 注册位）两行接入；
* 默认后端可用环境变量切换；未知后端显式报错，不静默回退。
"""

from __future__ import annotations

import pytest

from find_yourself.services.knowledge.vectorstores import (
    DEFAULT_VECTOR_BACKEND,
    VectorStore,
    create_vector_store,
    get_vector_store_class,
    list_vector_stores,
    register_vector_store,
    vector_backend_status,
)


def test_builtin_sqlite_vec_backend_is_registered():
    assert "sqlite_vec" in list_vector_stores()
    assert get_vector_store_class("sqlite_vec").__name__ == "SqliteVecStore"
    assert DEFAULT_VECTOR_BACKEND == "sqlite_vec"


def test_backend_status_is_queryable():
    status = vector_backend_status()
    assert "sqlite_vec" in status
    entry = status["sqlite_vec"]
    assert entry["class"] == "SqliteVecStore"
    assert entry["default"] is True
    # sqlite-vec 是 pyproject 硬依赖，正常安装下扩展包必须在
    assert entry["extension_installed"] is True


def test_custom_backend_registration_two_liner():
    """P2 注册位示例（LanceDB/Chroma/FAISS/Qdrant 落地形态）：类 + 注册即接入。"""

    class _StubRemoteStore(VectorStore):
        name = "stub_remote"

        def __init__(self, uri: str = "", *, dim: int = 0, session=None, **kwargs):
            self.uri, self.dim, self.session = uri, dim, session

        def is_available(self):
            return True

        def add(self, records, *, owner_id):
            return 0

        def delete(self, chunk_ids, *, owner_id):
            return 0

        def query(self, embedding, *, owner_id, top_k=8, filters=None):
            return []

        def count(self, *, owner_id=None):
            return 0

        def list_chunk_ids(self, *, owner_id):
            return set()

    register_vector_store("stub_remote", _StubRemoteStore)
    try:
        assert "stub_remote" in list_vector_stores()
        # create_vector_store 把 kwargs 原样透传给构造器
        store = create_vector_store("stub_remote", uri="/tmp/vec", dim=8)
        assert isinstance(store, _StubRemoteStore)
        assert store.uri == "/tmp/vec" and store.dim == 8
    finally:
        # 注册表是进程级状态：清理，避免影响其他用例
        from find_yourself.services.knowledge.vectorstores import registry

        registry._REGISTRY.pop("stub_remote", None)


def test_duplicate_registration_requires_override():
    from find_yourself.services.knowledge.vectorstores import registry

    with pytest.raises(ValueError, match="already registered"):
        register_vector_store("sqlite_vec", get_vector_store_class("sqlite_vec"))
    # override=True 允许替换（测试/热插拔场景）
    original = registry._REGISTRY["sqlite_vec"]
    try:
        register_vector_store("sqlite_vec", original, override=True)
    finally:
        registry._REGISTRY["sqlite_vec"] = original


def test_unknown_backend_fails_loudly_and_env_switches_default(monkeypatch):
    with pytest.raises(KeyError, match="unknown vector store"):
        create_vector_store("does_not_exist")
    with pytest.raises(KeyError):
        # 环境变量指向未知后端 → 显式失败（不静默回退，避免假装切换成功）
        monkeypatch.setenv("FIND_YOURSELF_KB_VECTOR_BACKEND", "does_not_exist")
        create_vector_store()
