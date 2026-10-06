"""Alembic migration 0039: RAG 方案模板 + A/B 存档 + 知识图谱数据层 — 补齐包6.

四张新表：

* ``kb_rag_presets``    —— 用户保存的 RAG 方案模板（切片/检索/重排参数 JSON，
  owner+name 唯一）。内置 4 预设在 ``services/knowledge/rag_presets.py`` 代码里，
  不入库。
* ``kb_rag_ab_runs``    —— 调试器 A/B 对比存档（query + 各臂 preset_id + 截断
  payload JSON，证据不是全文备份）。
* ``kb_graph_nodes``    —— 知识图谱节点（实体名 + 类型 + 权重 + **首次出现切片
  指针**；owner+name_key 唯一保证构建幂等）。
* ``kb_graph_edges``    —— 知识图谱边（关系类型 + 权重 + **证据切片指针**；
  owner+src+dst+relation 唯一，重复证据 weight+1）。

架构铁律：图谱**只存实体/关系/指针，原文不搬运**——证据文本永远按
``evidence_chunk_id`` 现取 ``kb_chunks.content``，图存储里没有原文副本。

ORM 同构模型分别注册在 ``services/knowledge/rag_presets.py`` 与
``services/knowledge/graph.py``（不导入 ``db/models.py``，与 0037 同款设计）；
conftest 的测试模块显式导入这两个模块后，``Base.metadata.create_all`` 亦能
看到同构约束。

编号说明：任务书原定「0038 已用、只许用 0039」，但当前链头实为
``0037_capability_grants``（树上无 0038 落盘）——按 0037 同款裁决（在链头追加
**一个**新迁移、不碰他人迁移），``down_revision`` 接实际链头 0037，编号按任务书
 mandate 用 **0039**（0038 留给并行包，若其落在 0039 之后需自行顺延，链门禁
``tests/unit/test_migration_chain_gate.py`` 会强制单头）。

Revision ID: 0039_rag_presets_and_graph
Revises: 0037_capability_grants
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_rag_presets_and_graph"
down_revision: str | None = "0038_dsl_flow_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("kb_rag_presets"):
        op.create_table(
            "kb_rag_presets",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("params", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id", "name", name="uq_kb_rag_presets_owner_name"),
        )

    if not insp.has_table("kb_rag_ab_runs"):
        op.create_table(
            "kb_rag_ab_runs",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("query", sa.String(length=500), nullable=False),
            sa.Column("preset_ids", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_kb_rag_ab_runs_owner_created", "kb_rag_ab_runs", ["owner_id", "created_at"]
        )

    if not insp.has_table("kb_graph_nodes"):
        op.create_table(
            "kb_graph_nodes",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("name_key", sa.String(length=320), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="concept"),
            sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("first_chunk_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("first_doc_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id", "name_key", name="uq_kb_graph_nodes_owner_key"),
        )
        op.create_index(
            "ix_kb_graph_nodes_owner_weight", "kb_graph_nodes", ["owner_id", "weight"]
        )

    if not insp.has_table("kb_graph_edges"):
        op.create_table(
            "kb_graph_edges",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("src_node_id", sa.String(length=64), nullable=False),
            sa.Column("dst_node_id", sa.String(length=64), nullable=False),
            sa.Column("relation", sa.String(length=64), nullable=False),
            sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("evidence_chunk_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("evidence_doc_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "owner_id", "src_node_id", "dst_node_id", "relation",
                name="uq_kb_graph_edges_owner_triple",
            ),
        )
        op.create_index("ix_kb_graph_edges_owner_src", "kb_graph_edges", ["owner_id", "src_node_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("kb_graph_edges"):
        op.drop_table("kb_graph_edges")
    if insp.has_table("kb_graph_nodes"):
        op.drop_table("kb_graph_nodes")
    if insp.has_table("kb_rag_ab_runs"):
        op.drop_table("kb_rag_ab_runs")
    if insp.has_table("kb_rag_presets"):
        op.drop_table("kb_rag_presets")
