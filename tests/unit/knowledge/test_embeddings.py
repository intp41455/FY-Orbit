"""补齐包2 · A-向量库-07①：Embedding 可插拔层单测。

验收点：
* hash embedding **确定性**（跨实例/跨调用同向量；用 hashlib 而非随机化 hash）；
* 维度可配（构造参数与环境变量）；相关文本相似度 > 无关文本；
* OpenAI 兼容 provider：无 key 自动禁用；有 key 走 mock transport 可用；
* 注册表：按名构建、默认降级链（无 key 的远程 → hash 兜底）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from find_yourself.services.knowledge.embeddings import (
    EmbeddingUnavailable,
    HashingEmbedding,
    OpenAICompatibleEmbedding,
    create_embedding,
    get_embedding,
    list_embeddings,
    register_embedding,
    resolve_default_embedding,
)


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5 or 1.0
    db = sum(x * x for x in b) ** 0.5 or 1.0
    return num / (da * db)


# --- hash embedding ---------------------------------------------------------- #

def test_hash_embedding_is_deterministic_across_instances():
    e1, e2 = HashingEmbedding(dim=256), HashingEmbedding(dim=256)
    v1 = e1.embed_query("知识库检索与向量召回")
    v2 = e2.embed_query("知识库检索与向量召回")
    v3 = e1.embed_query("知识库检索与向量召回")
    assert v1 == v2 == v3  # 逐位相等：blake2b 稳定散列，不依赖 PYTHONHASHSEED
    assert len(v1) == 256
    assert e1.is_available() is True


def test_hash_embedding_dim_configurable_via_arg_and_env(monkeypatch):
    assert len(HashingEmbedding(dim=64).embed_query("文本")) == 64
    monkeypatch.setenv("FIND_YOURSELF_KB_HASH_DIM", "128")
    assert len(HashingEmbedding().embed_query("文本")) == 128
    with pytest.raises(ValueError):
        HashingEmbedding(dim=4)  # 低于下限直接拒绝，不静默夹取


def test_hash_embedding_orders_related_above_unrelated():
    emb = HashingEmbedding(dim=256)
    q = emb.embed_query("区块链共识算法的原理是什么")
    related = emb.embed_query("共识算法由多数派节点确认。")
    unrelated = emb.embed_query("今天晚饭吃什么好呢")
    assert _cosine(q, related) > _cosine(q, unrelated)
    assert _cosine(q, related) > 0.0
    # 空文本 → 零向量（相似度 0，由检索层下限过滤，不会伪造召回）
    assert all(v == 0.0 for v in emb.embed_query(""))


def test_hash_embedding_distinct_texts_have_distinct_vectors():
    emb = HashingEmbedding(dim=256)
    assert emb.embed_query("苹果") != emb.embed_query("香蕉")


# --- OpenAI 兼容远程嵌入 ------------------------------------------------------- #

def _embedding_response(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": i, "embedding": v, "object": "embedding"}
                for i, v in enumerate(vectors)
            ],
            "model": "test-model",
        },
        request=httpx.Request("POST", "https://fake/v1/embeddings"),
    )


def test_openai_embedding_disabled_without_key(monkeypatch):
    monkeypatch.delenv("FIND_YOURSELF_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleEmbedding()
    assert provider.is_available() is False
    assert provider.ensure_dim() == 0
    with pytest.raises(EmbeddingUnavailable):
        provider.embed_query("文本")


def test_openai_embedding_embeds_via_mock_transport(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        texts = json.loads(req.content)["input"]
        table = {"a": [0.1, 0.2, 0.3], "b": [0.4, 0.5, 0.6]}
        return _embedding_response([table.get(t, [0.0, 0.0, 0.0]) for t in texts])

    provider = OpenAICompatibleEmbedding(
        api_key="sk-test",
        base_url="https://fake/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )
    assert provider.is_available() is True
    out = provider.embed_documents(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]  # index 保序
    assert provider.embed_query("a") == [0.1, 0.2, 0.3]
    assert provider.dim == 3  # 首次成功调用后维度自动确定


def test_openai_embedding_env_fallback_key(monkeypatch):
    monkeypatch.delenv("FIND_YOURSELF_EMBEDDING_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert OpenAICompatibleEmbedding().is_available() is True


# --- 注册表 ------------------------------------------------------------------- #

def test_registry_lists_builtin_and_builds_by_name():
    assert "hash" in list_embeddings() and "openai" in list_embeddings()
    assert get_embedding("hash").name == "hash"
    with pytest.raises(KeyError, match="unknown embedding provider"):
        get_embedding("nope")


def test_resolve_default_embedding_falls_back_to_hash_when_remote_unavailable(monkeypatch):
    monkeypatch.delenv("FIND_YOURSELF_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # 环境指定远程 provider，但无 key → 不可用 → hash 兜底（「无 key 自动禁用」）
    monkeypatch.setenv("FIND_YOURSELF_KB_EMBEDDING", "openai")
    resolved = resolve_default_embedding()
    assert resolved.name == "hash"
    assert resolved.is_available() and resolved.dim > 0
    # 显式点名 + 不可知名 → 同样兜底，绝不抛错中断检索
    assert resolve_default_embedding("no_such_provider").name == "hash"


def test_register_custom_embedding_provider():
    class _MyEmbedding(HashingEmbedding):
        name = "my-embedding"

    register_embedding("my-embedding", _MyEmbedding, override=True)
    try:
        assert create_embedding("my-embedding").name == "my-embedding"
    finally:
        from find_yourself.services.knowledge.embeddings import registry

        registry._FACTORY_REGISTRY.pop("my-embedding", None)
