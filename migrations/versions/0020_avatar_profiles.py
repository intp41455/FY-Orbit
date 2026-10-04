"""Alembic migration 0020: W11 个性化像素角色档案。

单表 ``avatar_profiles`` —— 一行 = 一个 owner 的当前专属小人：

* ``owner_id`` UNIQUE：``POST /api/avatar/generate`` 是 upsert，重复调不堆角色；
  这也是小屋「专属小人」一对一绑定的根据。
* ``portrait``：用户**逐项同意后**的画像输入（引擎据此确定性映射，缺项走中性默认
  并在响应 ``advisory`` 里诚实标注「待补画像」）。
* ``params`` / ``base_signature`` / ``overrides``：引擎产物。
* ``fingerprint``（底稿指纹，谁的画像） / ``params_fingerprint``（呈现指纹，
  微调会变）——分享卡短码用后者。
* **矩阵不落库**：24×32×8 层矩阵由 ``params`` 确定性重算（引擎无 random / uuid /
  time 依赖），落库只会制造「库里那份」与「算出来那份」不一致的隐性 bug。
* ``state`` CHECK 限定 ``draft`` / ``confirmed``：草稿不得被小屋或分享卡消费。
* ``likeness_score`` CHECK 限定 1~10 或 NULL（未评价）。
* ``version`` 是乐观锁，供 ``PUT /api/avatar/confirm`` 消费。

> 集成提示：本 revision 接在 0015_knowledge_base（W3）之后——那��是当前 head。
> 若之后有任务往这条链上加 revision，必须把 ``down_revision`` 顺延到最新 head，
> 否则 alembic 会报多 head。

Revision ID: 0020_avatar_profiles
Revises: 0015_knowledge_base
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020_avatar_profiles"
down_revision: str | None = "0016_cabin_gameplay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("avatar_profiles"):
        op.create_table(
            "avatar_profiles",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("portrait", sa.JSON(), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("base_signature", sa.JSON(), nullable=False),
            sa.Column("overrides", sa.JSON(), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("params_fingerprint", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("engine_version", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("likeness_score", sa.Integer(), nullable=True),
            sa.Column("likeness_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("is_house_avatar", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id", name="uq_avatar_profile_owner"),
            sa.CheckConstraint("state IN ('draft', 'confirmed')", name="avatar_state"),
            sa.CheckConstraint(
                "likeness_score IS NULL OR (likeness_score >= 1 AND likeness_score <= 10)",
                name="avatar_likeness_range",
            ),
            sa.CheckConstraint("version >= 1", name="avatar_version_positive"),
        )
        op.create_index("ix_avatar_profiles_owner", "avatar_profiles", ["owner_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("avatar_profiles"):
        op.drop_table("avatar_profiles")
