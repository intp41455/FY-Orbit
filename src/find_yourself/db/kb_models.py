"""W3 本地知识库（document RAG）模型。

与 ``services/memory.py`` 的「画像记忆」是**两套独立体系**：memory 存的是结构化
画像条目，kb 存的是用户导入的原始文档切片。本模块只拥有 ``kb_documents`` /
``kb_chunks`` 两张表，注册在共享 ``Base`` 元数据上，不修改 ``db/models.py``
（该文件在飞，由其他任务拥有）。

演进路径（任务书裁决）：v1 = SQLite + 轻量向量（关键词倒排 + 哈希嵌入），
切 PostgreSQL 后在 ``kb_chunks`` 旁加 pgvector 列并启用 HNSW，检索改为
「向量近邻 + tsvector」RRF 归并；表结构本身不需要重写。

全文索引 ``kb_chunks_fts``（SQLite FTS5 虚表）不建模：它由 migration 与
``services.knowledge.search`` 惰性创建，见该模块说明。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, SHA256, TZDateTime, utcnow

KB_STATUSES = ("indexing", "ready", "failed")


class KBDocument(Base):
    """一份被导入的知识库文档（.md/.txt/.pdf/.docx 或适配器拉取的条目）。"""

    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    # local | ima | baidu_pan | ...
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    # 适配器来源的外部条目 id（本地文件为空串）
    external_id: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="indexing")
    # 失败原因（用户可见，绝不静默）；成功时为空串
    error: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('indexing', 'ready', 'failed')", name="kb_document_status"
        ),
        Index("ix_kb_documents_owner_created", "owner_id", "created_at"),
        Index("ix_kb_documents_owner_source", "owner_id", "source"),
    )


class KBChunk(Base):
    """文档切片。``owner_id`` 全程强制，检索签名也强制要求它。"""

    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        ID, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(SHA256, nullable=False, default="")

    __table_args__ = (
        Index("ix_kb_chunks_owner_doc_seq", "owner_id", "doc_id", "seq"),
        Index("ix_kb_chunks_doc", "doc_id"),
    )