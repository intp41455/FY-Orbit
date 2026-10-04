"""多人协作闭环 —— 评论 / @人 / 通知 / 角色（需求 15，第一切片）。

四个能力
--------
1. **评论**：对某条 record（task/canvas/artifact/memory）发表、编辑、删除评论。
2. **@人**：从评论正文解析 ``@<user_id>``，只保留**对该 record 有可见权限**的人。
3. **通知**：被 @ 的人收到站内通知，可标记已读；通知**不携带评论正文**。
4. **角色**：``owner`` / ``admin`` / ``manager`` / ``viewer`` 四级能力。

授权：复用，不重造
------------------
跨域的数据可见性一律交给既有的 :class:`~find_yourself.services.grant.GrantService`：
服务身份（agent/worker）要读某条跨域的 record，必须先过
``GrantService.is_authorized(record_domain=..., record_id=..., consumer_domain=...)``
——与 ``memory.search`` / ``canvas`` 用的是**同一个谓词**。本模块**不写第二份**
可见性判定。协作角色**叠加在** grant 之上：先过 grant 的数据关卡，再按角色决定
「能读还是能写、能不能删别人的」。角色本身**永远不扩大数据可见范围**。

30 天上限直接复用 ``grant.MAX_GRANT_SECONDS`` 常量（单一真源），不手抄第二份
数字；禁通配（record 与主体都非空）由 ``collaboration_models`` 的 CHECK 保证。

owner 隔离
----------
评论/角色按 **record 归属** 判定访问；通知按 **收件人** 过滤。任何「无权可见」
一律按 ``NotFound`` 处理，与「不存在」返回**一致**的错误，不泄露存在性。
隔离是 **SQL WHERE 谓词**，不是调用方记得做的后置过滤。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db.collaboration_models import (
    ASSIGNABLE_ROLES,
    NOTIFICATION_KINDS,
    NOTIFICATION_MENTION,
    NOTIFICATION_REPLY,
    RECORD_KINDS,
    ROLE_CAPABILITIES,
    CollaborationRole,
    Comment,
    Notification,
)
from ..db.models import Artifact, Memory, Task
from ..db.canvas_models import CanvasInstance
from ..db.types import utcnow
from .actor import Actor
from .errors import NotFound, PermissionDenied, ValidationFailed
from .grant import MAX_GRANT_SECONDS

#: 匹配 ``@user_id``。user_id 是 owner/service 身份，形如 ``owner-1`` / ``svc-1``。
MENTION_RE = re.compile(r"@([A-Za-z0-9_.\-]+)")

#: 通知定位串的最大长度。它**不含**评论正文，这里只是给字符串一个上界。
SUMMARY_MAX = 200

#: 评论分页。**默认必须有界**：不设默认上限就等于「一次拉全量」，在大记录上
#: 既是性能问题，也让「分页」形同虚设。调用方只能请求更小的页，不能请求无限。
COMMENT_DEFAULT_LIMIT = 50
COMMENT_MAX_LIMIT = 200
COMMENT_ORDERS = ("asc", "desc")


@dataclass(frozen=True)
class Record:
    """一条可被评论的 record 的归属投影。"""

    kind: str
    id: str
    owner_id: str
    domain: str


@dataclass(frozen=True)
class NotificationTarget:
    """一位应收到通知的人及其 kind。纯数据，不含任何评论正文。"""

    user_id: str
    kind: str


#: kind 的具体程度（越靠前越具体）。同一个人被两条规则命中时取更具体的那条，
#: 避免「回复了某人、又 @ 了同一个人」产生两条重复通知。
NOTIFICATION_KIND_PRIORITY = (NOTIFICATION_REPLY, NOTIFICATION_MENTION)


class CollaborationService:
    def __init__(self, session: Session, audit: Any | None = None, *, grants: Any | None = None):
        self.s = session
        self.audit = audit
        #: 跨域可见性判定的唯一来源。None 时跨域服务身份一律拒绝（fail closed）。
        self.grants = grants

    # ------------------------------------------------------------------
    # 身份与 record 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _identity(actor: Actor) -> str:
        if actor.subject_type == "owner":
            return actor.owner_id or ""
        if actor.subject_type == "service":
            return actor.service_id or ""
        return ""

    def resolve_record(self, kind: str, record_id: str) -> Record | None:
        """把 ``(kind, record_id)`` 解析成归属投影；不存在返回 ``None``。

        ``artifact`` 没有 owner 列，归属通过 ``task_id`` 反查 ``tasks.owner_id``；
        一个没有关联任务的 artifact **无法确定归属**，一律按不可访问处理
        （fail closed，而不是放行）。
        """
        if kind not in RECORD_KINDS:
            raise ValidationFailed("bad_record_kind", f"Unknown record kind {kind!r}")
        if not record_id:
            raise ValidationFailed("record_id_required", "record_id is required")
        if kind == "task":
            row = self.s.get(Task, record_id)
            if row is not None:
                return Record("task", row.id, row.owner_id, row.domain)
        elif kind == "memory":
            row = self.s.get(Memory, record_id)
            if row is not None and row.deleted_at is None:
                return Record("memory", row.id, row.owner_id, row.domain)
        elif kind == "canvas":
            row = self.s.get(CanvasInstance, record_id)
            if row is not None:
                return Record("canvas", row.id, row.owner_id, row.domain)
        elif kind == "artifact":
            row = self.s.get(Artifact, record_id)
            if row is not None and row.deleted_at is None:
                owner = ""
                if row.task_id:
                    task = self.s.get(Task, row.task_id)
                    owner = task.owner_id if task is not None else ""
                return Record("artifact", row.id, owner, row.domain)
        return None

    def _service_domain_ok(self, actor: Actor, ref: Record) -> bool:
        """服务身份跨域读：复用 ``grant.is_authorized``，不另写谓词。"""
        for domain in actor.bound_domains:
            if domain == ref.domain:
                return True
            if self.grants is not None and self.grants.is_authorized(
                record_domain=ref.domain, record_id=ref.id, consumer_domain=domain
            ):
                return True
        return False

    def _role_for(self, actor: Actor, ref: Record) -> str | None:
        ident = self._identity(actor)
        if not ident:
            return None
        if actor.subject_type == "owner" and ident == ref.owner_id:
            return "owner"
        # 服务身份：数据可见性先过 grant（角色不绕过数据关卡）。
        if actor.subject_type == "service" and not self._service_domain_ok(actor, ref):
            return None
        row = self.s.execute(
            select(CollaborationRole).where(
                CollaborationRole.record_kind == ref.kind,
                CollaborationRole.record_id == ref.id,
                CollaborationRole.user_id == ident,
                CollaborationRole.state == "active",
                CollaborationRole.expires_at > utcnow(),
            )
        ).scalar_one_or_none()
        return row.role if row is not None else None

    def _require_access(self, actor: Actor, kind: str, record_id: str,
                        capability: str | None) -> tuple[Record, str]:
        """访问闸门。无权可见与不存在**返回同一个 NotFound**（不泄露存在性）。"""
        actor.require_authenticated()
        ref = self.resolve_record(kind, record_id)
        if ref is None:
            raise NotFound("record_not_found", f"record_not_found: {kind}/{record_id}")
        role = self._role_for(actor, ref)
        if role is None:
            # 注意：这里是「与不存在一致」的那条分支，不是 403。
            raise NotFound("record_not_found", f"record_not_found: {kind}/{record_id}")
        if capability is not None and capability not in ROLE_CAPABILITIES[role]:
            raise PermissionDenied(
                "collaboration_forbidden",
                f"Role {role!r} lacks {capability!r} on {kind}/{record_id}",
                403,
            )
        return ref, role

    # ------------------------------------------------------------------
    # 角色
    # ------------------------------------------------------------------
    def assign_role(
        self,
        actor: Actor,
        *,
        record_kind: str,
        record_id: str,
        user_id: str,
        role: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        """给某人在某条 record 上分配能力。仅 ``owner``/``admin``（``manage_roles``）可调。"""
        actor.require_owner()
        if role not in ASSIGNABLE_ROLES:
            raise ValidationFailed(
                "bad_role", f"role must be one of {list(ASSIGNABLE_ROLES)}, got {role!r}"
            )
        user_id = (user_id or "").strip()
        if not user_id:
            raise ValidationFailed("user_required", "user_id is required")
        ref, _ = self._require_access(actor, record_kind, record_id, "manage_roles")
        self._validate_expiry(expires_at)

        existing = self.s.execute(
            select(CollaborationRole).where(
                CollaborationRole.record_kind == ref.kind,
                CollaborationRole.record_id == ref.id,
                CollaborationRole.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 重复授予按幂等更新：调用方重试不该炸整个流程。
            existing.role = role
            existing.state = "active"
            existing.expires_at = expires_at
            existing.granted_by = actor.owner_id
            existing.revoked_at = None
            existing.version += 1
            self.s.flush()
            self._audit(actor, "collaboration.role_updated", existing.id,
                        {"record_kind": ref.kind, "record_id": ref.id, "user_id": user_id,
                         "role": role})
            return self._role_view(existing)

        row = CollaborationRole(
            id=f"cr-{uuid4().hex[:12]}",
            record_kind=ref.kind,
            record_id=ref.id,
            owner_id=ref.owner_id,
            user_id=user_id,
            role=role,
            state="active",
            granted_by=actor.owner_id,
            expires_at=expires_at,
        )
        self.s.add(row)
        self.s.flush()
        self._audit(actor, "collaboration.role_assigned", row.id,
                    {"record_kind": ref.kind, "record_id": ref.id, "user_id": user_id,
                     "role": role})
        return self._role_view(row)

    @staticmethod
    def _validate_expiry(expires_at: datetime) -> None:
        now = utcnow()
        if expires_at.tzinfo is None:
            raise ValidationFailed("role_tz", "expires_at must be timezone-aware UTC")
        if expires_at <= now or (expires_at - now).total_seconds() > MAX_GRANT_SECONDS:
            raise ValidationFailed(
                "role_expiry", "Role expiry must be in the future and <= 30 days"
            )

    def revoke_role(
        self, actor: Actor, *, record_kind: str, record_id: str, user_id: str
    ) -> dict[str, Any]:
        actor.require_owner()
        ref, _ = self._require_access(actor, record_kind, record_id, "manage_roles")
        row = self.s.execute(
            select(CollaborationRole).where(
                CollaborationRole.record_kind == ref.kind,
                CollaborationRole.record_id == ref.id,
                CollaborationRole.user_id == user_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("role_not_found", "role_not_found")
        if row.state == "revoked":
            return self._role_view(row)
        row.state = "revoked"
        row.revoked_at = utcnow()
        row.version += 1
        self.s.flush()
        self._audit(actor, "collaboration.role_revoked", row.id,
                    {"record_kind": ref.kind, "record_id": ref.id, "user_id": user_id})
        return self._role_view(row)

    def list_roles(self, actor: Actor, *, record_kind: str, record_id: str) -> list[dict[str, Any]]:
        ref, _ = self._require_access(actor, record_kind, record_id, "read")
        rows = self.s.execute(
            select(CollaborationRole)
            .where(
                CollaborationRole.record_kind == ref.kind,
                CollaborationRole.record_id == ref.id,
            )
            .order_by(CollaborationRole.created_at)
        ).scalars()
        return [self._role_view(r) for r in rows]

    # ------------------------------------------------------------------
    # 评论
    # ------------------------------------------------------------------
    def add_comment(
        self, actor: Actor, *, record_kind: str, record_id: str, body: str
    ) -> dict[str, Any]:
        """发表评论。需要 ``write`` 能力。"""
        ref, _ = self._require_access(actor, record_kind, record_id, "write")
        body = (body or "").strip()
        if not body:
            raise ValidationFailed("comment_body_required", "Comment body cannot be empty")
        author = self._identity(actor)
        mentions = self._parse_mentions(body, self._visible_user_ids(ref))
        comment = Comment(
            id=f"cm-{uuid4().hex[:12]}",
            owner_id=ref.owner_id,
            record_kind=ref.kind,
            record_id=ref.id,
            author_id=author,
            body=body,
            mentions=mentions,
        )
        self.s.add(comment)
        self.s.flush()
        notified = self._notify_mentions(actor, comment, ref)
        # message_id 折进 details（不进 hash 的新列），把评论与它的审计帧双向关联。
        self._audit(actor, "collaboration.comment.created", comment.id,
                    {"record_kind": ref.kind, "record_id": ref.id,
                     "mentions": len(mentions)},
                    message_id=comment.id)
        return {"comment": self._comment_view(comment), "notified": notified}

    def edit_comment(self, actor: Actor, comment_id: str, body: str) -> dict[str, Any]:
        """编辑评论。**只有作者本人**能改。"""
        actor.require_authenticated()
        comment = self._comment_row(comment_id)
        ref, _ = self._require_access(actor, comment.record_kind, comment.record_id, None)
        if comment.author_id != self._identity(actor):
            raise PermissionDenied(
                "not_comment_author", "Only the comment author can edit it", 403
            )
        body = (body or "").strip()
        if not body:
            raise ValidationFailed("comment_body_required", "Comment body cannot be empty")
        comment.body = body
        comment.mentions = self._parse_mentions(body, self._visible_user_ids(ref))
        comment.edited_at = utcnow()
        comment.version += 1
        self.s.flush()
        # 编辑可能**去掉**某个 @：陈旧通知必须一并回收，否则被取消提及的人
        # 仍会看到一个指向「已经不再提到他」的评论的通知。
        stale = self._reconcile_notifications(comment)
        notified = self._notify_mentions(actor, comment, ref)
        self._audit(actor, "collaboration.comment.edited", comment.id,
                    {"record_kind": ref.kind, "record_id": ref.id,
                     "mentions": len(comment.mentions), "stale_notifications": stale},
                    message_id=comment.id)
        return {"comment": self._comment_view(comment), "notified": notified}

    def delete_comment(self, actor: Actor, comment_id: str) -> dict[str, Any]:
        """删除评论。作者本人，或持有 ``delete_any`` 的角色（owner/admin）。"""
        actor.require_authenticated()
        comment = self._comment_row(comment_id)
        ref, role = self._require_access(actor, comment.record_kind, comment.record_id, None)
        is_author = comment.author_id == self._identity(actor)
        if not is_author and "delete_any" not in ROLE_CAPABILITIES[role]:
            raise PermissionDenied(
                "delete_forbidden", "Only the author or an owner/admin can delete this comment", 403
            )
        if comment.deleted_at is not None:
            return self._comment_view(comment)
        comment.deleted_at = utcnow()
        comment.version += 1
        self.s.flush()
        # 联动：评论没了，指向它的通知必须一并失效，否则收件人会点进一条
        # 已经不存在的评论，且未读计数永远清不掉。通知是派生数据，直接删行
        # （评论本身仍是软删、可审计；通知不需要一条「已失效」的历史）。
        invalidated = self._delete_notifications_for_comment(comment.id)
        self._audit(actor, "collaboration.comment.deleted", comment.id,
                    {"record_kind": ref.kind, "record_id": ref.id,
                     "invalidated_notifications": invalidated},
                    message_id=comment.id)
        return self._comment_view(comment)

    def list_comments(
        self,
        actor: Actor,
        *,
        record_kind: str,
        record_id: str,
        limit: int | None = None,
        offset: int = 0,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        """列出评论。分页与排序**在隔离谓词之内**完成（见 ``_comments_query``）。"""
        return self.list_comments_page(
            actor, record_kind=record_kind, record_id=record_id,
            limit=limit, offset=offset, order=order,
        )["items"]

    def list_comments_page(
        self,
        actor: Actor,
        *,
        record_kind: str,
        record_id: str,
        limit: int | None = None,
        offset: int = 0,
        order: str = "asc",
    ) -> dict[str, Any]:
        """评论分页视图：``{items, limit, offset, order, count, has_more}``。

        授权与切片**不可分离**：``_require_access`` 先跑，隔离谓词进 WHERE，
        ``LIMIT/OFFSET`` 只作用在**已经被隔离过滤过**的结果集上。谁也无权
        通过 ``offset`` 翻到别人的记录里——翻页参数既不改变 FROM，也不改变
        WHERE，越权在切片之前就已经是 ``NotFound``。
        """
        ref, _ = self._require_access(actor, record_kind, record_id, "read")
        limit, offset, order = self._normalize_page(limit, offset, order)
        stmt = (
            select(Comment)
            .where(
                Comment.record_kind == ref.kind,
                Comment.record_id == ref.id,
                Comment.deleted_at.is_(None),
            )
            .order_by(
                Comment.created_at.desc() if order == "desc" else Comment.created_at.asc(),
                # 稳定次序：同一时间戳下按 id 再定序，否则翻页会漏/重。
                Comment.id.desc() if order == "desc" else Comment.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = [self._comment_view(c) for c in self.s.execute(stmt).scalars()]
        # has_more：多取一条判断是否还有下一页，避免调用方误以为「到底了」。
        stmt_more = (
            select(Comment.id)
            .where(
                Comment.record_kind == ref.kind,
                Comment.record_id == ref.id,
                Comment.deleted_at.is_(None),
            )
            .limit(1)
            .offset(offset + limit)
        )
        has_more = self.s.execute(stmt_more).first() is not None
        return {
            "items": rows,
            "limit": limit,
            "offset": offset,
            "order": order,
            "count": len(rows),
            "has_more": has_more,
        }

    @staticmethod
    def _normalize_page(limit: int | None, offset: int, order: str) -> tuple[int, int, str]:
        if order not in COMMENT_ORDERS:
            raise ValidationFailed("bad_order", f"order must be one of {list(COMMENT_ORDERS)}")
        if limit is None:
            limit = COMMENT_DEFAULT_LIMIT
        if not isinstance(limit, int) or limit < 1 or limit > COMMENT_MAX_LIMIT:
            raise ValidationFailed(
                "bad_limit", f"limit must be between 1 and {COMMENT_MAX_LIMIT}"
            )
        if not isinstance(offset, int) or offset < 0:
            raise ValidationFailed("bad_offset", "offset must be >= 0")
        return limit, offset, order

    # ------------------------------------------------------------------
    # @人 与通知
    # ------------------------------------------------------------------
    def _visible_user_ids(self, ref: Record) -> set[str]:
        """能看见这条 record 的人 = owner + 在册且未过期的角色持有者。

        ``@`` 只能落在这个集合里——否则用户就能通过 @ 去探测/骚扰无权可见的
        主体，那等于把「存在性」泄漏给外人。
        """
        ids: set[str] = {ref.owner_id} if ref.owner_id else set()
        rows = self.s.execute(
            select(CollaborationRole.user_id).where(
                CollaborationRole.record_kind == ref.kind,
                CollaborationRole.record_id == ref.id,
                CollaborationRole.state == "active",
                CollaborationRole.expires_at > utcnow(),
            )
        ).scalars()
        ids.update(rows)
        return ids

    def _parse_mentions(self, body: str, visible: set[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in MENTION_RE.findall(body or ""):
            if raw in seen:
                continue
            seen.add(raw)
            if raw in visible:
                out.append(raw)
        return out

    def _merge_targets(
        self, pairs: list[tuple[str, str]], author_id: str
    ) -> list[NotificationTarget]:
        """把「(人, kind)」序列合并为去重后的通知计划。

        **自通知抑制在此收口**：作者永不在自己的通知名单里，不管他是被 @ 还是
        被回复的目标。@ 路径与将来的回复路径共用这一个函数，所以「不能给自己发
        通知」不需要在两条路径上各写一遍，也就不会在其中一条上漏掉。
        """
        best: dict[str, str] = {}
        for user_id, kind in pairs:
            if not user_id or user_id == author_id:
                continue  # 自通知抑制
            if kind not in NOTIFICATION_KIND_PRIORITY:
                raise ValidationFailed(
                    "bad_notification_kind", f"Unknown notification kind {kind!r}"
                )
            if user_id not in best or (
                NOTIFICATION_KIND_PRIORITY.index(kind)
                < NOTIFICATION_KIND_PRIORITY.index(best[user_id])
            ):
                best[user_id] = kind
        return [NotificationTarget(uid, k) for uid, k in best.items()]

    def plan_reply_targets(
        self,
        *,
        author_id: str,
        parent_author_id: str | None,
        mentioned: list[str] | None = None,
    ) -> list[NotificationTarget]:
        """**回复**触发的通知计划（纯函数，不落库）。

        语义（谁通知谁）
        ----------------
        1. **回复人 → 被回复评论的作者**：kind = ``NOTIFICATION_REPLY``，即「有人
           回了你」。这是回复通知的主目标。
        2. **回复人 → 正文里 @ 到的人**：kind = ``NOTIFICATION_MENTION``，与普通
           评论的 @ 语义一致（``mentioned`` 必须已按可见性过滤，见
           :meth:`_parse_mentions`）。
        3. **record 的 owner 不因「被回复」而收到通知**：只有被直接 @、或是被回复
           评论的作者才收。否则每条评论都惊动 owner，通知就失去意义。
        4. **去重**：一个人既是父评论作者又被 @，只收一条，取更具体的
           ``comment_reply``。
        5. **自通知抑制**：回复自己的评论、或 @ 自己 → 不产生任何通知。

        为什么现在不落库
        ----------------
        ``NOTIFICATION_REPLY`` **尚未**进 ``NOTIFICATION_KINDS``：扩白名单要同步
        扩 ``ck_notif_kind`` 的 DB CHECK，属 schema 变更，须走迁移（待 0032）；
        且「回复」还缺一根承载父评论关系的列。所以本函数只产出**计划**，能否落库
        由 :meth:`notify_targets` 的白名单闸门决定——未白名单的 kind 会被拒绝，
        而不是被悄悄丢弃。
        """
        pairs: list[tuple[str, str]] = []
        if parent_author_id:
            pairs.append((parent_author_id, NOTIFICATION_REPLY))
        for uid in mentioned or []:
            pairs.append((uid, NOTIFICATION_MENTION))
        return self._merge_targets(pairs, author_id)

    def notify_targets(
        self,
        actor: Actor,
        comment: Comment,
        ref: Record,
        targets: list[NotificationTarget],
    ) -> list[dict[str, Any]]:
        """把一份通知计划落库。**先整体校验再落库**，绝不部分写入。

        白名单闸门：任何不在 ``NOTIFICATION_KINDS`` 里的 kind 一律拒绝（fail
        loud）。这划出了「将来加 comment_reply 只需扩白名单与 CHECK、服务层不再
        改动」的边界——planner 算得出计划，落库能力由白名单决定。
        """
        for t in targets:
            if t.kind not in NOTIFICATION_KINDS:
                raise ValidationFailed(
                    "kind_not_whitelisted",
                    f"Notification kind {t.kind!r} is not enabled; it needs a schema migration",
                )
        created: list[Notification] = []
        for t in targets:
            # 同一评论同一人同一 kind 幂等。
            existing = self.s.execute(
                select(Notification).where(
                    Notification.owner_id == t.user_id,
                    Notification.comment_id == comment.id,
                    Notification.kind == t.kind,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            row = Notification(
                id=f"nt-{uuid4().hex[:12]}",
                owner_id=t.user_id,
                kind=t.kind,
                record_kind=ref.kind,
                record_id=ref.id,
                comment_id=comment.id,
                author_id=comment.author_id,
                summary=self._summary(ref, comment),
            )
            self.s.add(row)
            created.append(row)
        self.s.flush()
        for row in created:
            # 通知正文不含评论正文：details 里显式声明，便于下游安全地记录/转发。
            self._audit(actor, "collaboration.notification.created", row.id,
                        {"record_kind": ref.kind, "record_id": ref.id,
                         "comment_id": comment.id, "kind": row.kind,
                         "contains_private_text": False},
                        message_id=comment.id)
        return [self._notification_view(n) for n in created]

    def _notify_mentions(
        self, actor: Actor, comment: Comment, ref: Record
    ) -> list[dict[str, Any]]:
        """@ 提及路径：与回复路径共用同一套合并/自通知抑制逻辑。"""
        targets = self._merge_targets(
            [(uid, NOTIFICATION_MENTION) for uid in (comment.mentions or [])],
            comment.author_id,
        )
        return self.notify_targets(actor, comment, ref, targets)

    def _delete_notifications_for_comment(self, comment_id: str) -> int:
        """硬删指向某条评论的通知，返回删除行数。"""
        res = self.s.execute(
            delete(Notification).where(Notification.comment_id == comment_id)
        )
        self.s.flush()
        return int(res.rowcount or 0)

    def _reconcile_notifications(self, comment: Comment) -> int:
        """回收「评论仍存在、但已不再提及该人」的通知，返回删除行数。"""
        keep = list(comment.mentions or [])
        stmt = delete(Notification).where(
            Notification.comment_id == comment.id,
            Notification.kind == NOTIFICATION_MENTION,
        )
        if keep:
            stmt = stmt.where(Notification.owner_id.not_in(keep))
        res = self.s.execute(stmt)
        self.s.flush()
        return int(res.rowcount or 0)

    def unread_count(self, actor: Actor) -> int:
        """当前身份的未读通知数。**不区分 kind**（kind 多类型后此式仍正确），
        也**不携带任何正文**——它只是一个整数。"""
        actor.require_authenticated()
        ident = self._identity(actor)
        if not ident:
            return 0
        return int(
            self.s.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.owner_id == ident,
                    Notification.read_at.is_(None),
                )
            ).scalar_one()
        )

    @staticmethod
    def _summary(ref: Record, comment: Comment) -> str:
        """**不携带评论正文**的定位串（比「截断到 N 字」更保守：截断仍会泄内容）。"""
        who = comment.author_id or "某人"
        text = f"{who} 在 {ref.kind} 的评论中提到了你"
        return text[:SUMMARY_MAX]

    def list_notifications(self, actor: Actor) -> list[dict[str, Any]]:
        actor.require_authenticated()
        ident = self._identity(actor)
        if not ident:
            return []
        rows = self.s.execute(
            select(Notification)
            .where(Notification.owner_id == ident)
            .order_by(Notification.created_at.desc())
        ).scalars()
        return [self._notification_view(n) for n in rows]

    def mark_notification_read(self, actor: Actor, notification_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        ident = self._identity(actor)
        row = self.s.get(Notification, notification_id)
        if row is None or row.owner_id != ident:
            # 不是你的通知 = 不存在，同样的 404。
            raise NotFound("notification_not_found", "notification_not_found")
        if row.read_at is None:
            row.read_at = utcnow()
            row.version += 1
            self.s.flush()
            self._audit(actor, "collaboration.notification.read", row.id, {})
        return self._notification_view(row)

    # ------------------------------------------------------------------
    # 内部：行读取与视图
    # ------------------------------------------------------------------
    def _comment_row(self, comment_id: str) -> Comment:
        row = self.s.get(Comment, comment_id)
        if row is None:
            raise NotFound("comment_not_found", "comment_not_found")
        return row

    def _audit(
        self, actor: Actor, action: str, target: str, details: dict | None,
        *, message_id: str | None = None,
    ) -> None:
        if self.audit is not None:
            self.audit.append(actor, action, target, details or {}, message_id=message_id)

    @staticmethod
    def _role_view(row: CollaborationRole) -> dict[str, Any]:
        return {
            "id": row.id,
            "record_kind": row.record_kind,
            "record_id": row.record_id,
            "owner_id": row.owner_id,
            "user_id": row.user_id,
            "role": row.role,
            "state": row.state,
            "granted_by": row.granted_by,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "version": row.version,
        }

    @staticmethod
    def _comment_view(row: Comment) -> dict[str, Any]:
        return {
            "id": row.id,
            "record_kind": row.record_kind,
            "record_id": row.record_id,
            "author_id": row.author_id,
            "body": row.body,
            "mentions": list(row.mentions or []),
            "deleted": row.deleted_at is not None,
            "edited_at": row.edited_at.isoformat() if row.edited_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "version": row.version,
        }

    @staticmethod
    def _notification_view(row: Notification) -> dict[str, Any]:
        return {
            "id": row.id,
            "owner_id": row.owner_id,
            "kind": row.kind,
            "record_kind": row.record_kind,
            "record_id": row.record_id,
            "comment_id": row.comment_id,
            "author_id": row.author_id,
            "summary": row.summary,
            "read": row.read_at is not None,
            "read_at": row.read_at.isoformat() if row.read_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "version": row.version,
        }
