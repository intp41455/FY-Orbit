"""Alembic migration 0023: W9 个人资产库（``assets`` 表）。

单表：``assets`` —— owner 隔离的个人资产元数据。**文件本体不入库**，一律落
``FY_ASSETS_DIR``（默认 ``.runtime/assets/<owner_id>/``），DB 只存相对路径
``storage_path``；``/api/assets/{id}/raw`` 鉴权后代理，前端永远拿不到绝对路径。

字段与任务书 §1.1 一致：owner_id / kind(image|audio|music|doc) / name /
storage_path / mime / size / meta(JSON) / created_at / version。
``kind`` 用 CHECK 约束（冻结契约 §3.2：状态字段必须是数据库约束，不接受任意字符串）。

> 集成提示：主控 2026-10-04 裁决「迁移编号必须单调（head 编号 = 链尾编号）」，本迁移
> 整体改号为 0023，``down_revision`` 指向 W7 改号后的 ``0022_agent_bus``（引用关系与
> 编号解耦）。若之后有任务再往这条链上加 revision，同样必须把 ``down_revision``
> 顺延到最新 head，否则 alembic 会报多 head。

Revision ID: 0023_personal_assets
Revises: 0022_agent_bus
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_personal_assets"
down_revision: str | None = "0022_agent_bus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("assets"):
        op.create_table(
            "assets",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=500), nullable=False),
            # 相对 FY_ASSETS_DIR 的 POSIX 路径；绝不存绝对路径。
            sa.Column("storage_path", sa.String(length=1000), nullable=False),
            sa.Column("mime", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("meta", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "kind IN ('image', 'audio', 'music', 'doc')", name="asset_kind"
            ),
        )
        op.create_index("ix_assets_owner_created", "assets", ["owner_id", "created_at"])
        op.create_index("ix_assets_owner_kind", "assets", ["owner_id", "kind"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("assets"):
        op.drop_table("assets")