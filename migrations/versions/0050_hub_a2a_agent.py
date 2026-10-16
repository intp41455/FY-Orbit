"""Alembic migration 0050: ``hub_connections.kind`` 增加外部 A2A Agent。

G7 出站配置面：补第 8 个 kind（``a2a_agent``），让用户能在 Hub 里登记一个
**外部 A2A Agent**。DB CHECK 约束必须同步放行新值，否则登记出站 Agent 的
INSERT 会被数据库层拒绝（这正是 0049 为 ``market_source`` 踩过并解决过的问题）。

为什么用 batch copy-and-move（**逐字照抄 0049/0021 范式**）：SQLite 不支持
ALTER CHECK。``copy_from`` 传显式表（``reflected=False``），CHECK 名逐字沿用，
让迁移后的库与 ``create_all`` 建出来的库约束名一致。

上一版翻车记录（照抄它的教训，别再犯）：漏声明
``ix_hub_connection_owner_kind`` 复合索引 → 降级后索引**静默消失**（不报错、
只是查询变慢），随后 0024 的 downgrade 直接 ``no such index`` 炸掉。
所以下面 ``_table()`` 里索引与全部列索引是**逐字**照抄 0049 的，不做"优化"。

幂等护栏（照抄 0049 的 ``_has_new_check`` 写法）：
* 表不存在则跳过——全新库由 ``create_all`` 按 ORM 元数据建表，CHECK 已含 8 值；
* CHECK 已含 ``a2a_agent`` 则跳过。

Revision ID: 0050_hub_a2a_agent
Revises: 0049_hub_market_source
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_hub_a2a_agent"
down_revision: str | None = "0049_hub_market_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "hub_connections"
KIND_CHECK = "hub_connection_kind"
#: 升级前的 7 个值（= 0049 的 NEW_VALUES）。顺序即 0049 落库的顺序，逐字沿用：
#: CHECK 子句里值的顺序会写进 sqlite_master，降级-升级往返后必须与 0049 之后
#: 的库保持一致，否则往返测试比对 schema 时会出现假漂移。
OLD_VALUES = (
    "openai_chat",
    "anthropic",
    "mcp_server",
    "http_webhook",
    "knowledge_source",
    "tool_plugin",
    "market_source",
)
NEW_VALUES = OLD_VALUES + ("a2a_agent",)
STATE_VALUES = ("active", "disabled", "needs_credentials", "error")


def _table(kind_values: tuple[str, ...]) -> sa.Table:
    kinds = ",".join(f"'{v}'" for v in kind_values)
    states = ",".join(f"'{v}'" for v in STATE_VALUES)
    return sa.Table(
        TABLE,
        sa.MetaData(),
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=200), index=True),
        sa.Column("name", sa.String(length=120)),
        sa.Column("kind", sa.String(length=32), index=True),
        sa.Column("group", sa.String(length=16), default="tool"),
        sa.Column("preset_id", sa.String(length=48), default=""),
        sa.Column("icon", sa.String(length=16), default="\U0001f50c"),
        sa.Column("description", sa.Text(), default=""),
        sa.Column("endpoint_config", sa.JSON(), default={}),
        sa.Column("secret_config", sa.JSON(), default={}),
        sa.Column("secret_fields", sa.JSON(), default=[]),
        sa.Column("capabilities", sa.JSON(), default=[]),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=24), default="active"),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_ok", sa.Boolean(), nullable=True),
        sa.Column("last_health_detail", sa.Text(), default=""),
        sa.Column("last_health_latency_ms", sa.Integer(), nullable=True),
        sa.Column("preference", sa.Integer(), default=0),
        sa.Column("version", sa.Integer(), default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN ({kinds})", name=KIND_CHECK),
        sa.CheckConstraint(f"state IN ({states})", name="hub_connection_state"),
        sa.CheckConstraint("preference >= 0 AND preference <= 10",
                           name="hub_preference_range"),
        sa.CheckConstraint("version >= 1", name="hub_connection_version_positive"),
        sa.UniqueConstraint("owner_id", "name", name="uq_hub_connection_owner_name"),
        # 复合索引必须逐字重声明：batch copy-and-move 以本表结构为准重建，
        # 漏写它 = 降级后索引静默消失，0024 的 downgrade 随即 no such index。
        sa.Index("ix_hub_connection_owner_kind", "owner_id", "kind"),
    )


def _has_new_check(bind) -> bool:
    for ck in sa.inspect(bind).get_check_constraints(TABLE):
        if ck["name"] == KIND_CHECK and "a2a_agent" in (ck.get("sqltext") or ""):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(TABLE):
        return
    if _has_new_check(bind):
        return
    with op.batch_alter_table(
        TABLE,
        copy_from=_table(NEW_VALUES),
        recreate="always",
    ):
        pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(TABLE):
        return
    # 残留 a2a_agent 行时拒绝降级（删用户数据不如不降级；先在应用层删除连接再降）。
    remaining = bind.execute(sa.text(
        f"SELECT COUNT(*) FROM {TABLE} WHERE kind = 'a2a_agent'")).scalar()
    if remaining:
        raise RuntimeError(
            f"拒绝降级：hub_connections 仍有 {remaining} 个 a2a_agent 连接，"
            "请先在应用层删除后再降级")
    if _has_new_check(bind):
        with op.batch_alter_table(
            TABLE,
            copy_from=_table(OLD_VALUES),
            recreate="always",
        ):
            pass