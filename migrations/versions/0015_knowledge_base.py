"""Alembic migration 0015: W3 本地知识库（document RAG）。

两张新表：

* ``kb_documents`` —— 被导入的文档元数据 + 状态机（indexing/ready/failed，
  CHECK 约束）+ 失败原因（用户可见，不静默）。
* ``kb_chunks`` —— 文档切片（doc_id 级联删除、owner_id 强制、seq 顺序、
  content_hash 去重/溯源）。

全文索引：SQLite 下惰性创建 FTS5 虚表 ``kb_chunks_fts``（trigram 分词，中文子串
可命中；owner_id/doc_id 为 UNINDEXED 列，召回时可按 owner 过滤）。创建失败
（SQLite 未编译 FTS5）时静默跳过，检索侧自动回退 LIKE 倒排——两条路径都必须可用，
所以这不是致命错误。PostgreSQL 分支留 TODO：切库时改为 ``tsvector`` 列 + GIN 索引
（见 ``services/knowledge/search.py`` 演进路径说明）。

> 集成提示：本 revision 接在 0013_cabin_interiors（W1）之后。若之后有任务再往这条链上
> 加 0014/0016，必须把 ``down_revision`` 顺延到最新 head，否则 alembic 会报多 head。

Revision ID: 0015_knowledge_base
Revises: 0013_cabin_interiors
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_knowledge_base"
down_revision: str | None = "0013_cabin_interiors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_fts5() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # TODO(W3): PostgreSQL -> `ALTER TABLE kb_chunks ADD COLUMN tsv tsvector`
        # + GIN index，检索走 tsvector / pgvector RRF 归并。
        return
    insp = sa.inspect(bind)
    if insp.has_table("kb_chunks_fts"):
        return
    try:
        op.execute(
            "CREATE VIRTUAL TABLE kb_chunks_fts USING fts5("
            "chunk_id UNINDEXED, doc_id UNINDEXED, owner_id UNINDEXED, content, "
            "tokenize='trigram')"
        )
    except Exception:  # noqa: BLE001 — SQLite 未编译 FTS5：回退 LIKE，不致命
        pass


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("kb_documents"):
        op.create_table(
            "kb_documents",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("name", sa.String(length=500), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="local"),
            sa.Column("external_id", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="indexing"),
            sa.Column("error", sa.String(length=1000), nullable=False, server_default=""),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "status IN ('indexing', 'ready', 'failed')", name="kb_document_status"
            ),
        )
        op.create_index("ix_kb_documents_owner_created", "kb_documents", ["owner_id", "created_at"])
        op.create_index("ix_kb_documents_owner_source", "kb_documents", ["owner_id", "source"])

    if not insp.has_table("kb_chunks"):
        op.create_table(
            "kb_chunks",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "doc_id",
                sa.String(length=64),
                sa.ForeignKey("kb_documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
        )
        op.create_index("ix_kb_chunks_owner_doc_seq", "kb_chunks", ["owner_id", "doc_id", "seq"])
        op.create_index("ix_kb_chunks_doc", "kb_chunks", ["doc_id"])

    _create_fts5()


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("kb_chunks_fts") and bind.dialect.name == "sqlite":
        op.execute("DROP TABLE kb_chunks_fts")
    if insp.has_table("kb_chunks"):
        op.drop_table("kb_chunks")
    if insp.has_table("kb_documents"):
        op.drop_table("kb_documents")