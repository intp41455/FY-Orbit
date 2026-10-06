"""Embedding 可插拔层（补齐包2 · A-向量库-07①）。

公开面：

* :class:`.base.EmbeddingProvider` —— 接口；:class:`.base.EmbeddingUnavailable`
* :class:`.hash_embedding.HashingEmbedding` —— 确定性离线默认
* :class:`.openai_compatible.OpenAICompatibleEmbedding` —— 远程，无 key 禁用
* :mod:`.registry` —— ``register_embedding`` / ``create_embedding`` /
  ``resolve_default_embedding`` / ``list_embeddings``
"""

from .base import EmbeddingProvider, EmbeddingUnavailable
from .hash_embedding import HashingEmbedding, hash_tokenize
from .openai_compatible import OpenAICompatibleEmbedding
from .registry import (
    DEFAULT_EMBEDDING,
    EMBEDDING_ENV,
    create_embedding,
    get_embedding,
    list_embeddings,
    register_embedding,
    resolve_default_embedding,
)

__all__ = [
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "HashingEmbedding",
    "OpenAICompatibleEmbedding",
    "hash_tokenize",
    "DEFAULT_EMBEDDING",
    "EMBEDDING_ENV",
    "create_embedding",
    "get_embedding",
    "list_embeddings",
    "register_embedding",
    "resolve_default_embedding",
]
