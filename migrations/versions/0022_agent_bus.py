"""Alembic migration 0022: Agent 通信总线房间共享上下文（W7）。

Introduces ``bus_context`` — the only **persisted** part of the W7 bus:

* ``owner_id`` + ``room`` — 房间归属。可见性只由服务端的 owner 判定，客户端
  提交的 owner 一律无效（FROZEN_CONTRACT §2 / actor.py）。
* ``kind`` 由 CHECK 约束限定为 ``file_ref`` / ``text``，与
  ``db/team_models.py`` 的 ``BUS_CONTEXT_KINDS`` 一致。
* ``ref`` 只存引用目标（路径 / artifact id / URL），不存文件正文；``content``
  只存截断后的文本片段。
* ``added_by`` 记录登记者身份（``owner:<id>`` / ``agent:<role>``），由服务端
  从 Actor 推导，不接受请求体传入。

**消息本体不落库**：``runtime/agent_bus.py`` 是进程内环形缓冲（每房间 500 条，
进程重启即丢失），这是 v1 的明确范围；需要跨进程时把该模块的 publish /
history / subscribe 换成 Redis Stream 原语即可，本表不受影响。

Revision note（主控裁决 v2 · 2026-10-04）：任务书原文写的是 migration 0017，
但编号必须单调 —— 编写时链头已被 W11(0020) / W8(0021) 占用，编号 0017 会让
后续任务取号撞车。按裁决整体改号为 0022，``down_revision`` 保持 0021 不变。

> 与本迁移对接的下游任务请把自己的 ``down_revision`` 指向 ``0022_agent_bus``。
> W9 的 ``0019_personal_assets`` 已按协同裁决顺延为 ``0023_personal_assets``。

Idempotency: table creation is guarded by ``sa.inspect(bind)``, following the
0005-0016 idiom.

Revision ID: 0022_agent_bus
Revises: 0021_guest_account_tiers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_agent_bus"
down_revision: str | None = "0021_guest_account_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("bus_context"):
        op.create_table(
            "bus_context",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), index=True),
            sa.Column("room", sa.String(length=200)),
            sa.Column("kind", sa.String(length=32), default="text"),
            sa.Column("title", sa.String(length=200), default=""),
            sa.Column("ref", sa.Text(), default=""),
            sa.Column("content", sa.Text(), default=""),
            sa.Column("added_by", sa.String(length=200), default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("kind IN ('file_ref','text')", name="bus_context_kind"),
        )
        op.create_index("ix_bus_context_owner_room", "bus_context", ["owner_id", "room"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("bus_context"):
        op.drop_index("ix_bus_context_owner_room", table_name="bus_context")
        op.drop_table("bus_context")
