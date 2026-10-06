"""Alembic migration 0037: 统一能力网关授权表 (``capability_grants``) — 补齐包1.

Creates ``capability_grants`` — the four-tuple authorization store
(**能力 × 资源 × 时限 × 可撤回**, A-能力网关-04) backing
``find_yourself.services.capability.grants.GrantStore``:

* 默认最小必要：表里只存显式授予/显式拒绝，无匹配 allow 即默认拒绝；
* 显式拒绝（``effect='deny'``）在裁决时覆盖一切 allow（多路取最严）;
* 越级授予以 ``escalation=1`` + ``level`` 显式表达（A-能力网关-02：越级需显式授权）;
* 撤销即时生效（``revoked_at``）；深级别授予的短时效约束在
  broker.grant 的策略层强制（本表不重复约束，避免两处真相）。

编号说明：任务书原定「只许用 0034」，但 0034/0035/0036 已被 B 包/特性开关
（``0034_cabin_life_save`` / ``0035_cabin_life_build_state`` / ``0036_feature_flags``，
均已提交）占用，链头已是 0036 —— 按任务书意图（在链头追加**一个**新迁移、
不碰他人迁移）顺延为 **0037**。

与 0034 同款设计：ORM 模型（``CapabilityGrantRow``）放在
``services/capability/grants.py`` 而**不**导入 ``db/models.py``，
因此 ``0001`` 的 ``Base.metadata.create_all`` 永远看不到这张表，
本迁移是它的唯一建表者。索引名走 ``Base.metadata`` 的 NAMING_CONVENTION
（``ix_capability_grants_*``），与 ORM ``index=True`` 生成的名字一致。

Revision ID: 0037_capability_grants
Revises: 0036_feature_flags
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0037_capability_grants"
down_revision: str | None = "0036_feature_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "capability_grants"

# 与 ORM（services/capability/grants.py::CapabilityGrantRow）一致；
# 约束/索引名传**短名**（NAMING_CONVENTION 会在 op DDL 上展开一次）。
_INDEXES = (
    ("ix_capability_grants_subject", ["subject"]),
    ("ix_capability_grants_capability", ["capability"]),
    ("ix_capability_grants_task_id", ["task_id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("subject", sa.String(length=200), nullable=False),
            sa.Column("capability", sa.String(length=120), nullable=False),
            sa.Column("effect", sa.String(length=8), nullable=False),
            sa.Column("resource_kind", sa.String(length=16), nullable=False),
            # 资源模式默认 "*"：四元组的资源元是可选收紧项，缺省即不限资源。
            sa.Column("resource_pattern", sa.String(length=400), nullable=False, server_default="*"),
            sa.Column("level", sa.String(length=8), nullable=True),
            sa.Column("escalation", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("task_id", sa.String(length=200), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revocable", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(length=200), nullable=False),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in _INDEXES:
            op.create_index(name, TABLE, columns)
        return

    # 表已存在（create_all 抢先建过，或本迁移重跑）：幂等补索引后返回。
    present = {ix["name"] for ix in insp.get_indexes(TABLE)}
    for name, columns in _INDEXES:
        if name not in present:
            op.create_index(name, TABLE, columns)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(TABLE):
        return
    op.drop_table(TABLE)
