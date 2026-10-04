"""Alembic migration 0024: 超级中台适配器中心连接表（W6）。

Introduces ``hub_connections`` — the single registry for every pluggable object
the hub can talk to (AI 服务 / 知识源 / 工具 / MCP), all normalised onto one
``HubAdapter`` protocol:

* ``owner_id`` — 归属。可见性只由服务端从 Actor 判定，请求体里的 owner 一律无效。
* ``kind`` 由 CHECK 约束限定为六个枚举值，与 ``services/hub/__init__.py`` 的
  ``HUB_KINDS`` 同源（模型文件直接 import 它，杜绝两边漂移）。
* ``endpoint_config`` 只存**非敏感**配置；凭证单独进 ``secret_config``，
  形如 ``{"api_key": {"enc": "enc:v1:...", "mask": "sk-a****23"}}``——
  Fernet 密文 + 展示掩码，**明文永不入库**。
* ``state`` 由 CHECK 约束限定；``needs_credentials`` 用于 manifest 导入后缺凭证的
  诚实状态（不可被路由命中）。
* ``capabilities`` 是探活时**真实发现**的结果（MCP 列工具、模型列 /models），
  不是手写清单。

Revision note: 任务书指定编号 0016，但 0016 已被 W2（``0016_cabin_gameplay``）
占用。主控 2026-10-04 裁决：统一改号为 **0024**（与 W7 的 0022_agent_bus、
W9 的 0023_personal_assets 一起重新顺序化），``down_revision`` 指向 0023。
编号顺序不影响 Alembic 的依赖解析（它按 revision 图走），但单调递增的编号能让
``alembic heads`` 只有一条链、避免多人并行时误判分叉。

Idempotency: table creation is guarded by ``sa.inspect(bind)``, following the
0005-0023 idiom.

Revision ID: 0024_hub_connections
Revises: 0023_personal_assets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_hub_connections"
down_revision: str | None = "0023_personal_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HUB_KINDS = (
    "openai_chat",
    "anthropic",
    "mcp_server",
    "http_webhook",
    "knowledge_source",
    "tool_plugin",
)
CONNECTION_STATES = ("active", "disabled", "needs_credentials", "error")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("hub_connections"):
        op.create_table(
            "hub_connections",
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
            sa.CheckConstraint(
                "kind IN ('openai_chat','anthropic','mcp_server','http_webhook',"
                "'knowledge_source','tool_plugin')",
                name="hub_connection_kind",
            ),
            sa.CheckConstraint(
                "state IN ('active','disabled','needs_credentials','error')",
                name="hub_connection_state",
            ),
            sa.CheckConstraint("preference >= 0 AND preference <= 10", name="hub_preference_range"),
            sa.CheckConstraint("version >= 1", name="hub_connection_version_positive"),
            sa.UniqueConstraint("owner_id", "name", name="uq_hub_connection_owner_name"),
        )
        op.create_index(
            "ix_hub_connection_owner_kind", "hub_connections", ["owner_id", "kind"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("hub_connections"):
        op.drop_index("ix_hub_connection_owner_kind", table_name="hub_connections")
        op.drop_table("hub_connections")
