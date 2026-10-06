"""Embedding 可插拔接口（补齐包2 · A-向量库-07）。

职责：把「文本 → 定长向量」抽象成引擎无关接口，检索层（``search.py`` 混合
检索）只面向接口编程，不感知具体 provider。

已内置实现：

* :class:`~.hash_embedding.HashingEmbedding` —— 确定性哈希嵌入，离线零依赖
  （默认 / 测试用，维度可配）；
* :class:`~.openai_compatible.OpenAICompatibleEmbedding` —— OpenAI
  ``/v1/embeddings`` 兼容的远程嵌入，key 读环境变量，**无 key 自动禁用**
  （``is_available() is False``，检索层自动降级词法路）。

注册表见 :mod:`.registry`。

边界约定：本包**不得**在模块顶层 import ``find_yourself.services.knowledge.search``
（search 在运行期 import 本包，顶层互引会成环）；嵌入 tokenization 与检索
分词刻意独立（粒度不同：嵌入用字符 n-gram 特征哈希，检索用倒排词）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingUnavailable(RuntimeError):
    """远程嵌入不可用 / 调用失败。

    检索层捕获后降级（向量路记 warning 并跳过），**绝不中断主检索**——
    与 FTS5 不可用回退 LIKE 同款降级哲学。
    """


class EmbeddingProvider(ABC):
    """文本嵌入接口。

    实现要求：

    * ``embed_query`` / ``embed_documents`` 对同一输入必须返回同一向量
      （确定性），或在该实现的 docstring 里显式声明非确定性来源；
    * ``dim`` 必须在可用时给出确定维度（本地向量后端建表用固定维度）。
    """

    #: 注册名（registry key）
    name: str = "base"
    #: 向量维度；远程 provider 未探测前允许为 0（registry 负责 ``ensure_dim``）
    dim: int = 0

    @abstractmethod
    def is_available(self) -> bool:
        """当前进程内是否可用。

        远程 provider 无 key / 本地缺依赖 → ``False``；调用方据此降级，
        **不允许**在检索主路径上抛异常来表达「不可用」。
        """

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（索引用）。空输入返回空列表。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """单条查询嵌入。"""

    def ensure_dim(self) -> int:
        """返回确定维度；未定时探测一次并缓存（远程 provider 覆写）。

        探测失败返回 0（调用方据此判定该 provider 不能用于本地向量建表）。
        """
        return self.dim
