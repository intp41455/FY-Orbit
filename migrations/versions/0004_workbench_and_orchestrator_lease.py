"""Alembic migration 0004: Engineering code workbench + orchestrator lease.

Adds the versioned contract tables required by
``18_工程代码工作台与主协调Agent全流程实施规格.md`` §5:

- workspace_manifests      (WorkspaceManifest)
- file_revisions           (FileRevision)
- workspace_events         (WorkspaceEvent, monotonic seq)
- terminal_sessions        (TerminalSession)
- preview_sessions         (PreviewSession)
- review_decisions         (ReviewDecision)
- orchestrator_leases      (single valid lease per root task + fencing token)

Revision ID: 0004_workbench
Revises: 0003_add_raw_speaker
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_workbench"
down_revision: str | None = "0003_add_raw_speaker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "workspace_manifests" not in existing:
        op.create_table(
            "workspace_manifests",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), index=True),
            sa.Column("project_name", sa.String(length=120)),
            sa.Column("data_domain", sa.String(length=16), default="work"),
            sa.Column("mode", sa.String(length=16), default="local"),
            sa.Column("authorized_root", sa.String(length=1000)),
            sa.Column("exec_identity", sa.String(length=200), default=""),
            sa.Column("branch", sa.String(length=200), default=""),
            sa.Column("task_refs", sa.JSON(), default=list),
            sa.Column("resource_limits", sa.JSON(), default=dict),
            sa.Column("lease_id", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("state", sa.String(length=16), default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("mode IN ('local', 'cloud')", name="ck_workspace_manifests_mode"),
            sa.CheckConstraint("state IN ('active', 'paused', 'archived')", name="ck_workspace_manifests_state"),
            sa.CheckConstraint(
                "data_domain IN ('personal', 'work', 'shared')",
                name="ck_workspace_manifests_data_domain",
            ),
        )

    if "file_revisions" not in existing:
        op.create_table(
            "file_revisions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64),
                      sa.ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True),
            sa.Column("rel_path", sa.String(length=1000), index=True),
            sa.Column("revision", sa.Integer(), default=1),
            sa.Column("content_sha256", sa.String(length=64)),
            sa.Column("size_bytes", sa.Integer(), default=0),
            sa.Column("encoding", sa.String(length=32), default="utf-8"),
            sa.Column("read_only", sa.Boolean(), default=False),
            sa.Column("is_deleted", sa.Boolean(), default=False),
            sa.Column("source_actor", sa.String(length=200), default=""),
            sa.Column("source_task_id", sa.String(length=64), nullable=True),
            sa.Column("before_sha256", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("workspace_id", "rel_path", "revision",
                                name="uq_file_revisions_workspace_path_revision"),
            sa.CheckConstraint("revision >= 1", name="ck_file_revisions_revision_positive"),
        )

    if "workspace_events" not in existing:
        op.create_table(
            "workspace_events",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64),
                      sa.ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True),
            sa.Column("seq", sa.Integer()),
            sa.Column("event_type", sa.String(length=64)),
            sa.Column("rel_path", sa.String(length=1000), nullable=True),
            sa.Column("revision", sa.Integer(), nullable=True),
            sa.Column("source_actor", sa.String(length=200), default=""),
            sa.Column("source_task_id", sa.String(length=64), nullable=True),
            sa.Column("diff", sa.JSON(), default=dict),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Index("ix_ws_evt_workspace_seq", "workspace_id", "seq", unique=True),
        )

    if "terminal_sessions" not in existing:
        op.create_table(
            "terminal_sessions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64),
                      sa.ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True),
            sa.Column("actor_identity", sa.String(length=200)),
            sa.Column("shell", sa.String(length=300)),
            sa.Column("pty_backend", sa.String(length=64), default="pipe"),
            sa.Column("pid", sa.Integer(), nullable=True),
            sa.Column("cols", sa.Integer(), default=120),
            sa.Column("rows", sa.Integer(), default=30),
            sa.Column("state", sa.String(length=16), default="created"),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("timeout_seconds", sa.Float(), default=300.0),
            sa.Column("event_cursor", sa.Integer(), default=0),
            sa.Column("stop_reason", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "state IN ('created', 'running', 'exited', 'stopped', 'timeout', 'failed')",
                name="ck_terminal_sessions_state",
            ),
        )

    if "preview_sessions" not in existing:
        op.create_table(
            "preview_sessions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64),
                      sa.ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True),
            sa.Column("process_pid", sa.Integer(), nullable=True),
            sa.Column("target_port", sa.Integer()),
            sa.Column("entry_path", sa.String(length=500), default="/"),
            sa.Column("kind", sa.String(length=16), default="http"),
            sa.Column("health", sa.String(length=16), default="starting"),
            sa.Column("access_subject", sa.String(length=200), default=""),
            sa.Column("access_lease", sa.String(length=64), default=""),
            sa.Column("state", sa.String(length=16), default="starting"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "state IN ('starting', 'healthy', 'unhealthy', 'stopped', 'failed')",
                name="ck_preview_sessions_state",
            ),
            sa.CheckConstraint("kind IN ('http', 'api')", name="ck_preview_sessions_kind"),
            sa.CheckConstraint(
                "target_port > 0 AND target_port < 65536", name="ck_preview_sessions_port_range"
            ),
        )

    if "review_decisions" not in existing:
        op.create_table(
            "review_decisions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64),
                      sa.ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True),
            sa.Column("task_id", sa.String(length=64), index=True),
            sa.Column("artifact_version", sa.String(length=64)),
            sa.Column("acceptance_items", sa.JSON(), default=list),
            sa.Column("evidence_refs", sa.JSON(), default=list),
            sa.Column("decision", sa.String(length=16)),
            sa.Column("reason", sa.Text(), default=""),
            sa.Column("decider", sa.String(length=200)),
            sa.Column("verification_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "decision IN ('pass', 'rework', 'blocked')", name="ck_review_decisions_decision"
            ),
        )

    if "orchestrator_leases" not in existing:
        op.create_table(
            "orchestrator_leases",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("root_task_id", sa.String(length=64), unique=True, index=True),
            sa.Column("orchestrator_id", sa.String(length=64)),
            sa.Column("fencing_token", sa.Integer(), default=1),
            sa.Column("state", sa.String(length=16), default="active"),
            sa.Column("handoff_packet", sa.JSON(), default=dict),
            sa.Column("takeover_summary", sa.JSON(), default=dict),
            sa.Column("granted_at", sa.DateTime(timezone=True)),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "state IN ('active', 'revoked', 'released')", name="ck_orchestrator_leases_state"
            ),
            sa.CheckConstraint(
                "fencing_token >= 1", name="ck_orchestrator_leases_fencing_token_positive"
            ),
        )


def downgrade() -> None:
    for table in (
        "orchestrator_leases", "review_decisions", "preview_sessions",
        "terminal_sessions", "workspace_events", "file_revisions", "workspace_manifests",
    ):
        try:
            op.drop_table(table)
        except Exception:
            # Idempotent downgrade: a partially applied upgrade must still reverse.
            pass
