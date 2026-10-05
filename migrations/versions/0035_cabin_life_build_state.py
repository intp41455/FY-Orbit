"""Alembic migration 0035: B 包室内建造与外键收口 (``life_saves.build_state`` & FK).

收口两处结构缺失（任务书 B1 & H6 / H7）：
1. **B1**: ``life_saves.owner_id`` 增加到 ``users.id`` 的外键约束 (``fk_life_saves_owner_id_users``)，
   避免注销用户留下孤儿存档；
2. **H6**: ``life_saves`` 增加 ``build_state`` JSON 列 (``server_default='{}'``)，
   承载室内家具摆放与建造布局 (``build.Layout``)，时间戳/默认值不引入假数据。

迁移鲁棒性：
* 走 ``op.batch_alter_table``（copy-and-move 模式，兼容 SQLite）；
* ``sa.inspect(bind)`` 防重复执行与幂等；
* 往返双路径测试覆盖。

Revision ID: 0035_cabin_life_build_state
Revises: 0034_cabin_life_save
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0035_cabin_life_build_state"
down_revision: str | None = "0034_cabin_life_save"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "life_saves"
FK_NAME = "fk_life_saves_owner_id_users"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(TABLE):
        # 表尚不存在时跳过（由 0034 或 Base.metadata 负责全量建表）
        return

    columns = {c["name"] for c in insp.get_columns(TABLE)}
    fks = {fk.get("name") for fk in insp.get_foreign_keys(TABLE)}

    need_col = "build_state" not in columns
    need_fk = FK_NAME not in fks

    if not need_col and not need_fk:
        return

    with op.batch_alter_table(TABLE) as batch_op:
        if need_col:
            batch_op.add_column(
                sa.Column("build_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
            )
        if need_fk and insp.has_table("users"):
            batch_op.create_foreign_key(
                FK_NAME,
                "users",
                ["owner_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(TABLE):
        return

    columns = {c["name"] for c in insp.get_columns(TABLE)}
    fks = {fk.get("name") for fk in insp.get_foreign_keys(TABLE)}

    has_col = "build_state" in columns
    has_fk = FK_NAME in fks

    if not has_col and not has_fk:
        return

    with op.batch_alter_table(TABLE) as batch_op:
        if has_fk:
            batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        if has_col:
            batch_op.drop_column("build_state")
