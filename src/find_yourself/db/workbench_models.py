"""Database models for 18 工程代码工作台与主协调 Agent (Engineering Code Workbench).

Implements the versioned contract objects required by
``18_工程代码工作台与主协调Agent全流程实施规格.md`` §5:

- WorkspaceManifest    : owner, data domain, local/cloud, authorized root,
                         execution identity, lease, resource limits, branch and
                         task references.
- FileRevision         : path, content digest, version, source actor, timestamp.
                         Saves carry ``expected_revision``; mismatches conflict.
- WorkspaceEvent       : monotonic per-workspace event log (version/seq, source
                         actor, task id, before/after diff) for reconnect replay.
- TerminalSessionRecord: workspace, user/agent identity, shell, process id,
                         permission, state, timeout, event cursor.
- PreviewSessionRecord : workspace, process, target port, controlled entry,
                         health, validity and access subject.
- ReviewDecisionRecord : artifact version, acceptance items, evidence,
                         pass/rework/blocked reason, decider.

None of these tables ever stores reusable login tokens or private memory text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from find_yourself.db.base import Base
from find_yourself.db.types import ID, TZDateTime, utcnow

# W6：kind / state 白名单与 services.hub 共用同一份常量，杜绝两边漂移。
from find_yourself.services.hub import CONNECTION_STATES, HUB_KINDS

WORKSPACE_MODES = ("local", "cloud")
WORKSPACE_STATES = ("active", "paused", "archived")
TERMINAL_STATES = ("created", "running", "exited", "stopped", "timeout", "failed")
PREVIEW_STATES = ("starting", "healthy", "unhealthy", "stopped", "failed")
REVIEW_DECISIONS = ("pass", "rework", "blocked")
LEASE_STATES = ("active", "revoked", "released")


class WorkspaceManifest(Base):
    """Authorized workspace registration (18 §5 WorkspaceManifest)."""

    __tablename__ = "workspace_manifests"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    project_name: Mapped[str] = mapped_column(String(120))
    data_domain: Mapped[str] = mapped_column(String(16), default="work")
    mode: Mapped[str] = mapped_column(String(16), default="local")
    authorized_root: Mapped[str] = mapped_column(String(1000))
    exec_identity: Mapped[str] = mapped_column(String(200), default="")
    branch: Mapped[str] = mapped_column(String(200), default="")
    task_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    resource_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(f"mode IN {WORKSPACE_MODES}", name="mode"),
        CheckConstraint(f"state IN {WORKSPACE_STATES}", name="state"),
        CheckConstraint("data_domain IN ('personal', 'work', 'shared')", name="data_domain"),
    )


class FileRevision(Base):
    """Immutable revision record per workspace file (18 §5 FileRevision)."""

    __tablename__ = "file_revisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True
    )
    rel_path: Mapped[str] = mapped_column(String(1000), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    encoding: Mapped[str] = mapped_column(String(32), default="utf-8")
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    source_actor: Mapped[str] = mapped_column(String(200), default="")
    source_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("workspace_id", "rel_path", "revision", name="workspace_path_revision"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )


class WorkspaceEvent(Base):
    """Monotonic per-workspace event log for reconnect replay (18 §3 实时修改)."""

    __tablename__ = "workspace_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    rel_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_actor: Mapped[str] = mapped_column(String(200), default="")
    source_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diff: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_ws_evt_workspace_seq", "workspace_id", "seq", unique=True),
    )


class TerminalSessionRecord(Base):
    """Real PTY terminal session (18 §5 TerminalSession)."""

    __tablename__ = "terminal_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True
    )
    actor_identity: Mapped[str] = mapped_column(String(200))
    shell: Mapped[str] = mapped_column(String(300))
    pty_backend: Mapped[str] = mapped_column(String(64), default="pipe")
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cols: Mapped[int] = mapped_column(Integer, default=120)
    rows: Mapped[int] = mapped_column(Integer, default=30)
    state: Mapped[str] = mapped_column(String(16), default="created")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=300.0)
    event_cursor: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(f"state IN {TERMINAL_STATES}", name="state"),
    )


class PreviewSessionRecord(Base):
    """Isolated workspace process preview (18 §5 PreviewSession)."""

    __tablename__ = "preview_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True
    )
    process_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_port: Mapped[int] = mapped_column(Integer)
    entry_path: Mapped[str] = mapped_column(String(500), default="/")
    kind: Mapped[str] = mapped_column(String(16), default="http")
    health: Mapped[str] = mapped_column(String(16), default="starting")
    access_subject: Mapped[str] = mapped_column(String(200), default="")
    access_lease: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(16), default="starting")
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(f"state IN {PREVIEW_STATES}", name="state"),
        CheckConstraint("kind IN ('http', 'api')", name="kind"),
        CheckConstraint("target_port > 0 AND target_port < 65536", name="port_range"),
    )


class ReviewDecisionRecord(Base):
    """Orchestrator acceptance decision bound to an artifact version (18 §5)."""

    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    artifact_version: Mapped[str] = mapped_column(String(64))
    acceptance_items: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    decider: Mapped[str] = mapped_column(String(200))
    verification_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        CheckConstraint(f"decision IN {REVIEW_DECISIONS}", name="decision"),
    )


class PreviewSourceRecord(Base):
    """Unified preview source registry (P1-A 实时预览窗).

    Three source kinds share one registry protocol:

    - ``static``  : a relative html/md path inside the workspace; content is
      served read-only through a dedicated endpoint with ``CSP: sandbox``.
      ``version`` is a digest of (mtime_ns, size) so the frontend can poll it
      for hot-refresh.
    - ``process`` : an isolated workspace process preview (delegates to
      PreviewService); ``preview_session_id`` links to the real session and
      the registry only records the loopback access address.

    The absolute filesystem path is NEVER stored or returned — only the
    workspace-relative path.
    """

    __tablename__ = "preview_sources"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="static")
    rel_path: Mapped[str] = mapped_column(String(1000), default="")
    media_type: Mapped[str] = mapped_column(String(100), default="text/html")
    preview_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        # P2 · Layer 4 增补 ``data``（结构化数据图表源）；迁移 0033 同步此白名单。
        CheckConstraint("kind IN ('static', 'process', 'data')", name="psrc_kind"),
        CheckConstraint("state IN ('active', 'offline')", name="psrc_state"),
    )


class OrchestratorLease(Base):
    """Single valid orchestrator lease per root task, with fencing token (18 §8)."""

    __tablename__ = "orchestrator_leases"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    root_task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    orchestrator_id: Mapped[str] = mapped_column(String(64))
    fencing_token: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(16), default="active")
    handoff_packet: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    takeover_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    granted_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(f"state IN {LEASE_STATES}", name="state"),
        CheckConstraint("fencing_token >= 1", name="fencing_token_positive"),
    )


class CabinExteriorRemoved(Base):
    """数码小屋室外家具移除记录 (W11 · 室外场景家具可移除)。

    One row per ``(owner_id, house_id)``。只存「用户明确删掉了哪几件」，
    不存整份摆放清单 —— 默认清单会随版本新增家具，整份存档会把新家具
    永久藏掉且用户无从恢复。黑名单能正确表达数据生命周期。

    The row is owner-private: every query filters on ``owner_id`` and the
    service reports another tenant's row as 404 so existence never leaks.
    """

    __tablename__ = "cabin_exterior_removed"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    house_id: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )


class CabinInterior(Base):
    """数码小屋室内布置 (W1 · 小屋室内场景与家具布置系统).

    One row per ``(owner_id, house_id)`` — switching the house template switches
    to that template's own saved arrangement. ``layout`` is the validated
    furniture payload (see ``services/cabin_interior.py``); ``version`` is the
    optimistic lock used by ``PUT /api/cabin/interiors/{house_id}``.

    The row is owner-private: every query filters on ``owner_id`` and the
    service reports another tenant's row as 404 so existence never leaks.
    """

    __tablename__ = "cabin_interiors"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    house_id: Mapped[str] = mapped_column(String(32), index=True)
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "house_id", name="uq_cabin_interior_owner_house"),
        CheckConstraint("version >= 1", name="cabin_interior_version_positive"),
        CheckConstraint("length(house_id) > 0", name="cabin_interior_house_nonempty"),
    )


#: 角色档案状态白名单（FROZEN_CONTRACT §状态术语）。
#: ``draft`` = 已生成未确认（不进小屋、不上分享卡）；``confirmed`` = 用户已确认。
AVATAR_STATES = ("draft", "confirmed")


class AvatarProfile(Base):
    """个性化像素角色档案 (W11 · 个性化像素角色生成系统).

    一行 = 一个 owner 的**当前专属小人**。要点：

    - ``owner_id`` 唯一：``generate`` 是 upsert，重复调不会堆出一堆角色；
      这也是小屋「专属小人」能一对一绑定的根据。
    - ``portrait`` 存原始画像输入（用户逐项同意后的那份），``params`` /
      ``base_signature`` / ``fingerprint`` 存引擎产物。**矩阵不落库**——
      24×32×8 层的矩阵由 ``params`` 确定性重算得到（引擎无随机、无时间依赖），
      落库只会造成「库里那份」与「算出来那份」不一致的隐性 bug。
    - ``state`` 区分草稿/已确认：草稿不得被小屋或分享卡消费。
    - ``version`` 是乐观锁，供 ``PUT /api/avatar/confirm`` 消费。
    """

    __tablename__ = "avatar_profiles"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(16), default="draft")
    # 用户逐项勾选同意的画像输入（缺项由引擎走中性默认并诚实标注）
    portrait: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 引擎产物：角色参数 + AI 底稿签名 + 微调覆盖
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    base_signature: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 底稿指纹（谁的画像，微调不变）/ 呈现指纹（现在长什么样，微调会变）
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    params_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    engine_version: Mapped[str] = mapped_column(String(32), default="")
    # 「像不像自己」自评（1~10，可空）+ 一句感想（可空）
    likeness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likeness_note: Mapped[str] = mapped_column(Text, default="")
    # 是否已设为小屋专属小人（小屋只消费已确认且已设为专属的档案）
    is_house_avatar: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(f"state IN {AVATAR_STATES}", name="avatar_state"),
        CheckConstraint(
            "likeness_score IS NULL OR (likeness_score >= 1 AND likeness_score <= 10)",
            name="avatar_likeness_range",
        ),
        CheckConstraint("version >= 1", name="avatar_version_positive"),
    )


# --- W6 超级中台适配器中心（追加；不改动上面任何既有模型） ---------------------- #

class HubConnection(Base):
    """中台连接 (W6 · 超级中台适配器中心 + 统一万能适配层)。

    一行 = owner 接入的一个「被接对象」，四类（AI 服务 / 知识源 / 工具 / MCP）
    全部归一到这张表，由 ``services/hub/adapters.py`` 的 ``HubAdapter`` 协议驱动。

    凭证安全（任务书 §3）：

    * 明文凭证**从不**落在 ``endpoint_config``：私密字段单独进 ``secret_config``，
      值为 ``{"enc": "enc:v1:...", "mask": "sk-a****23"}``——Fernet 密文 + 展示掩码。
    * 对外序列化一律走 ``services/hub/connections.public_connection()``，只出掩码。
    * ``preference`` 是路由的用户偏好权重（0-10），只影响排序不影响可用性。

    ``state`` 里 ``needs_credentials`` 是 manifest 导入后缺必填凭证的诚实状态：
    不是「已接入」，也不能被路由命中。
    """

    __tablename__ = "hub_connections"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    group: Mapped[str] = mapped_column(String(16), default="tool")
    preset_id: Mapped[str] = mapped_column(String(48), default="")
    icon: Mapped[str] = mapped_column(String(16), default="🔌")
    description: Mapped[str] = mapped_column(Text, default="")
    # 非敏感配置；敏感字段在 secret_config
    endpoint_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # {field: {"enc": "enc:v1:...", "mask": "sk-a****23"}}
    secret_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    capabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="active")
    last_health_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_health_detail: Mapped[str] = mapped_column(Text, default="")
    last_health_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preference: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_hub_connection_owner_name"),
        CheckConstraint(f"kind IN {HUB_KINDS}", name="hub_connection_kind"),
        CheckConstraint(f"state IN {CONNECTION_STATES}", name="hub_connection_state"),
        CheckConstraint("preference >= 0 AND preference <= 10", name="hub_preference_range"),
        CheckConstraint("version >= 1", name="hub_connection_version_positive"),
        Index("ix_hub_connection_owner_kind", "owner_id", "kind"),
    )
