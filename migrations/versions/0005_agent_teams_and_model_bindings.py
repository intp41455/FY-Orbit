"""Alembic migration 0005: agent teams + per-node model bindings (19 号规格).

Adds the versioned contract tables required by
``19_单Agent内部团队与逐节点模型配置实施规格.md`` §6:

- team_definitions                (TeamDefinition)
- model_bindings                  (ModelBinding, requested vs effective model)
- agent_instances                 (AgentInstance, one independent session each)
- agent_control_capabilities      (AgentControlCapabilities)
- control_requests                (ControlRequest)
- team_events                     (TeamEvent, monotonic seq)

Revision ID: 0005_agent_teams
Revises: 0004_workbench
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_agent_teams"
down_revision: str | None = "0004_workbench"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "team_definitions" not in existing:
        op.create_table(
            "team_definitions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), index=True),
            sa.Column("name", sa.String(length=200)),
            sa.Column("mode", sa.String(length=32), default="system_managed"),
            sa.Column("state", sa.String(length=32), default="draft"),
            sa.Column("canvas_instance_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("root_task_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("coordinator_role", sa.String(length=64), default="coordinator"),
            sa.Column("members", sa.JSON(), default=list),
            sa.Column("role_bindings", sa.JSON(), default=dict),
            sa.Column("default_binding", sa.JSON(), default=dict),
            sa.Column("budget_ref", sa.JSON(), default=dict),
            sa.Column("permission_ref", sa.JSON(), default=dict),
            sa.Column("plan_version", sa.Integer(), default=1),
            sa.Column("version", sa.Integer(), default=1),
            sa.Column("last_change_reason", sa.Text(), default=""),
            sa.Column("last_changed_by", sa.String(length=200), default=""),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "mode IN ('system_managed', 'product_native')", name="ck_team_definitions_team_mode"
            ),
            sa.CheckConstraint(
                "state IN ('draft', 'validating', 'ready', 'running', 'paused', "
                "'completed', 'failed', 'cancelled')",
                name="ck_team_definitions_team_state",
            ),
            sa.CheckConstraint("plan_version >= 1", name="ck_team_definitions_team_plan_version_positive"),
            sa.CheckConstraint("version >= 1", name="ck_team_definitions_team_version_positive"),
        )

    if "model_bindings" not in existing:
        op.create_table(
            "model_bindings",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("team_definitions.id", ondelete="CASCADE"),
                index=True,
            ),
            sa.Column("agent_instance_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("scope", sa.String(length=16), default="node"),
            sa.Column("scope_key", sa.String(length=64), default=""),
            sa.Column("provider_id", sa.String(length=64), default=""),
            sa.Column("requested_model", sa.String(length=160), default=""),
            sa.Column("effective_model", sa.String(length=160), nullable=True),
            sa.Column("effective_confidence", sa.String(length=16), default="not_executed"),
            sa.Column("model_revision", sa.String(length=120), nullable=True),
            sa.Column("credential_ref", sa.String(length=200), default=""),
            sa.Column("credential_configured", sa.Boolean(), default=False),
            sa.Column("endpoint_ref", sa.String(length=200), default=""),
            sa.Column("params", sa.JSON(), default=dict),
            sa.Column("capability_snapshot", sa.JSON(), default=dict),
            sa.Column("pricing_version", sa.String(length=64), default=""),
            sa.Column("budget_reserved_usd", sa.Numeric(12, 6), default=0),
            sa.Column("frozen", sa.Boolean(), default=False),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "scope IN ('global', 'team', 'role', 'node')",
                name="ck_model_bindings_binding_scope",
            ),
            sa.CheckConstraint(
                "effective_confidence IN ('exact', 'unknown', 'auto', 'not_executed')",
                name="ck_model_bindings_binding_confidence",
            ),
        )

    if "agent_instances" not in existing:
        op.create_table(
            "agent_instances",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("team_definitions.id", ondelete="CASCADE"),
                index=True,
            ),
            sa.Column("role", sa.String(length=64), index=True),
            sa.Column("title", sa.String(length=200), default=""),
            sa.Column("agent_host", sa.String(length=64), default="find_yourself"),
            sa.Column("provider_id", sa.String(length=64), default=""),
            sa.Column("state", sa.String(length=32), default="draft"),
            sa.Column("session_id", sa.String(length=64), unique=True, index=True),
            sa.Column("parent_task_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("root_task_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("subtask_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("model_binding_id", sa.String(length=64), nullable=True),
            sa.Column("requested_model", sa.String(length=160), default=""),
            sa.Column("effective_model", sa.String(length=160), nullable=True),
            sa.Column("effective_confidence", sa.String(length=16), default="not_executed"),
            sa.Column("depends_on", sa.JSON(), default=list),
            sa.Column("run_batch", sa.Integer(), default=0),
            sa.Column("depth", sa.Integer(), default=0),
            sa.Column("steps", sa.Integer(), default=0),
            sa.Column("max_steps", sa.Integer(), default=8),
            sa.Column("budget_reserved_usd", sa.Numeric(12, 6), default=0),
            sa.Column("budget_spent_usd", sa.Numeric(12, 6), default=0),
            sa.Column("blocked_reason", sa.Text(), default=""),
            sa.Column("current_goal", sa.Text(), default=""),
            sa.Column("plan_version", sa.Integer(), default=1),
            sa.Column("last_event_seq", sa.Integer(), default=0),
            sa.Column("version", sa.Integer(), default=1),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "state IN ('draft', 'starting', 'running', 'blocked', 'paused', "
                "'waiting_rework', 'completed', 'failed', 'cancelled', "
                "'unknown_needs_reconciliation')",
                name="ck_agent_instances_agent_state",
            ),
            sa.CheckConstraint("run_batch >= 0", name="ck_agent_instances_agent_run_batch_non_negative"),
            sa.UniqueConstraint("team_id", "role", name="uq_agent_team_role"),
        )

    if "agent_control_capabilities" not in existing:
        op.create_table(
            "agent_control_capabilities",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("agent_host", sa.String(length=64), unique=True, index=True),
            sa.Column("provider_id", sa.String(length=64), default=""),
            sa.Column("capabilities", sa.JSON(), default=dict),
            sa.Column("supports_per_member_model", sa.Boolean(), default=False),
            sa.Column("usage_metering", sa.String(length=16), default="unknown"),
            sa.Column("probe_source", sa.String(length=64), default="static"),
            sa.Column("reason", sa.Text(), default=""),
            sa.Column("probed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "usage_metering IN ('verified', 'unsupported', 'unknown')",
                name="ck_agent_control_capabilities_capability_usage_metering",
            ),
        )

    if "control_requests" not in existing:
        op.create_table(
            "control_requests",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("team_definitions.id", ondelete="CASCADE"),
                index=True,
            ),
            sa.Column("agent_instance_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("operation", sa.String(length=32)),
            sa.Column("idempotency_key", sa.String(length=120), unique=True),
            sa.Column("expected_version", sa.Integer(), default=0),
            sa.Column("target_version", sa.Integer(), nullable=True),
            sa.Column("operator", sa.String(length=200), default=""),
            sa.Column("scope", sa.JSON(), default=dict),
            sa.Column("reason", sa.Text(), default=""),
            sa.Column("state", sa.String(length=32), default="pending"),
            sa.Column("result", sa.JSON(), default=dict),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "operation IN ('pause', 'resume', 'reassign', 'rework', 'cancel', "
                "'switch_model', 'spawn')",
                name="ck_control_requests_control_operation",
            ),
            sa.CheckConstraint(
                "state IN ('pending', 'applied', 'rejected', "
                "'unknown_needs_reconciliation', 'superseded')",
                name="ck_control_requests_control_state",
            ),
        )

    if "team_events" not in existing:
        op.create_table(
            "team_events",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "team_id",
                sa.String(length=64),
                sa.ForeignKey("team_definitions.id", ondelete="CASCADE"),
                index=True,
            ),
            sa.Column("seq", sa.Integer()),
            sa.Column("event_type", sa.String(length=64)),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("agent_instance_id", sa.String(length=64), nullable=True),
            sa.Column("run_batch", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(length=64), default="system"),
            sa.Column("details", sa.JSON(), default=dict),
            sa.Column("evidence_refs", sa.JSON(), default=list),
            sa.Column("created_at", sa.DateTime(timezone=True)),
        )
        op.create_index(
            "ix_team_event_team_seq", "team_events", ["team_id", "seq"], unique=True
        )
        op.create_index(
            "ix_team_event_member",
            "team_events",
            ["team_id", "agent_instance_id", "seq"],
        )


def downgrade() -> None:
    for index, table in (
        ("ix_team_event_member", "team_events"),
        ("ix_team_event_team_seq", "team_events"),
    ):
        try:
            op.drop_index(index, table_name=table)
        except Exception:
            pass
    for table in (
        "team_events",
        "control_requests",
        "agent_control_capabilities",
        "agent_instances",
        "model_bindings",
        "team_definitions",
    ):
        try:
            op.drop_table(table)
        except Exception:
            # Idempotent downgrade: a partially applied upgrade must still reverse.
            pass