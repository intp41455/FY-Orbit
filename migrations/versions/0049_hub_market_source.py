"""Alembic migration 0049: ``hub_connections.kind`` 增加市场远程源。

I-12 裁决：加「远程源适配器」，复用 ``hub_connections`` 做成第 7 个 kind
（``market_source``），不新造体系。DB CHECK 约束必须同步放行新值，
否则登记远程源的 INSERT 会被数据库层拒绝。

为什么用 batch copy-and-move（0021 同范式）：SQLite 不支持 ALTER
CHECK。``copy_from`` 传显式表（reflected=False），CHECK 名逐字沿用，
让迁移后的库与 ``create_all`` 建出来的库约束名一致。

幂等护栏：表不存在则跳过（全新库由 create_all 按 ORM 元数据建表，
CHECK 已含 7 值）；已含新值的 CHECK 则跳过。

Revision ID: 0049_hub_market_source
Revises: 0048_dsl_draft_version
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_hub_market_source"
down_revision: str | None = "0048_dsl_draft_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "hub_connections"
KIND_CHECK = "hub_connection_kind"
OLD_VALUES = (
    "openai_chat",
    "anthropic",
    "mcp_server",
    "http_webhook",
    "knowledge_source",
    "tool_plugin",
)
NEW_VALUES = OLD_VALUES + ("market_source",)
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
    )


def _has_new_check(bind) -> bool:
    for ck in sa.inspect(bind).get_check_constraints(TABLE):
        if ck["name"] == KIND_CHECK and "market_source" in (ck.get("sqltext") or ""):
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
    # 残留 market_source 行时拒绝降级（删用户数据不如不降级；先在应用层删除连接再降）。
    remaining = bind.execute(sa.text(
        f"SELECT COUNT(*) FROM {TABLE} WHERE kind = 'market_source'")).scalar()
    if remaining:
        raise RuntimeError(
            f"拒绝降级：hub_connections 仍有 {remaining} 个 market_source 连接，"
            "请先在应用层删除后再降级")
    if _has_new_check(bind):
        with op.batch_alter_table(
            TABLE,
            copy_from=_table(OLD_VALUES),
            recreate="always",
        ):
            pass
