"""团队级审批流（需求 6）。

个人级审批已经有一套（``ProposalService``，FROZEN_CONTRACT §6），
HITL 又提供了「暂停 → 等人决策 → 恢复」的执行中断机制
（:class:`~find_yourself.services.hitl.HitlInterruptService`）。缺的那一维
是**团队**：谁能批、批给谁、多人协作时怎么流转。本模块补的就是这一维。

复用而非重造
------------
状态机**不重造**。一条团队审批申请对应一行 ``hitl_interrupts``：
等待中/已决策、决策值合法性、单赢家条件 UPDATE、超时，全部由
``HitlInterruptService`` 负责。本模块只加三样它没有的东西：

1. **团队维度** —— 申请归哪个团队、该团队谁能拍板。
2. **审批人资格校验** —— 按拍板那一刻的成员行判定，不信调用方自称的角色。
3. **利益冲突回避** —— 申请人不能审自己提交的申请。

``GrantService`` 的关系
------------------------
``GrantService`` 管的是「**跨域的、按记录逐条授予的、有时限的**数据访问」
（``Grant(source_domain, consumer_domain, record_ids, expires_at)``，且
wildcard 被明文禁止）。团队审批资格是「**组内**的、**按人**的、随成员行状态
生效」的授权，两者不是同一个轴：一个是「这份数据给不给那个域看」，一个是
「这个人在这个组里能不能拍板」。所以团队成员表**不是** ``Grant`` 的子类或
替代，而是与之并行的第二类授权，判定时互不冒充。

但两者**共用同一个审计哈希链**（``AuditService``）与同一个
``Actor``：谁在什么时候把审批权给了谁、和谁批了哪条申请，都要能事后追责。

三条硬规矩
----------
1. **审批人只能是自己团队的。** 跨团队一律按「不存在」处理——不确认存在性，
   避免枚举（沿用 HITL 的归属隔离做法）。
2. **审批人不能审自己提交的。** 利益冲突回避，与团队无关地成立。
3. **资格按成员行判，不按请求参数判。** 请求体里的 ``role`` 从不提升权限
   （BUG-03的同一条纪律）。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.hitl_models import (
    HITL_DECIDED_STATUSES,
    HITL_TIMEOUT_DECISION,
    HitlInterrupt,
)
from ..db.team_approval_models import (
    TEAM_DECISIONS,
    ApprovalTeam,
    ApprovalTeamMember,
    TeamApprovalRequest,
)
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from .hitl import HitlInterruptService

#: 拍板时在 HITL options 里放行的那三个决策。顺序即审批中心 UI 的展示顺序。
DECISION_OPTIONS = [
    {"value": "approve", "label": "批准"},
    {"value": "reject", "label": "驳回"},
    {"value": "return_for_change", "label": "退回修改"},
]


class TeamApprovalService:
    """团队维度 + 审批资格 + 利益冲突回避。状态机委托给 HITL。"""

    def __init__(
        self,
        session: Session,
        audit: Any | None = None,
        *,
        hitl: HitlInterruptService | None = None,
    ):
        self.session = session
        self.audit = audit
        # 复用同一个 session，HITL 里的 flush/条件 UPDATE 才和本模块看到同一份状态。
        self.hitl = hitl or HitlInterruptService(session, audit)

    #: 「决策值 → 终态」映射**直接指向** HITL 的那个函数对象，而不是抄一份。
    #:
    #: 为什么要显式挂出来：抄一份必然漂移——HITL 那边调整了保守默认，团队
    #: 这边还按旧规则记终态，审计链上就会出现同一个决策的两个分类，而**没有
    #: 任何测试会红**。挂成同一个函数对象后，漂移在物理上就不可能发生。
    #:
    #: 代价与它的显式化：这是一个**跨模块私有耦合**。``_status_for`` 以下划线
    #: 开头，HITL 侧随时可能改名或「清理」。因此：
    #:
    #: * HITL 那边 :meth:`HitlInterruptService._status_for` 的 docstring 里
    #:   注明了「被 team_approval 引用，改名请一起改」；
    #: * 本行是那个约定的**可执行副本**——
    #:   ``test_both_flows_share_the_same_status_mapper`` 断言两者是同一
    #:   个对象。HITL 那边一改名，这条测试立刻红，而不是等到某天
    #:   「审批偶尔记错终态」再花半天定位。
    _status_mapper = staticmethod(HitlInterruptService._status_for)

    # ------------------------------------------------------------------
    # 团队与成员
    # ------------------------------------------------------------------
    def create_team(self, actor: Actor, name: str, *, team_id: str | None = None) -> dict[str, Any]:
        """建组。建组人自动成为 ``member``（可提可批）。

        「建组人自动有审批权」是刻意的：否则建组后无人能批，团队是死的。
        但它**只**给 member（两权），不给纯 approver——这样「建组人第一个
        审批」仍然要受第2 条规矩约束。
        """
        actor.require_owner()
        name = (name or "").strip()
        if not name:
            raise ValidationFailed("team_name_required", "Team name is required")
        tid = team_id or f"team-{uuid4().hex[:12]}"
        if self.session.get(ApprovalTeam, tid) is not None:
            raise Conflict("team_exists", f"Team {tid} already exists")
        team = ApprovalTeam(id=tid, name=name, created_by=actor.owner_id, state="active")
        self.session.add(team)
        self.session.add(
            ApprovalTeamMember(
                id=f"atm-{uuid4().hex[:12]}",
                team_id=tid,
                user_id=actor.owner_id,
                role="member",
                state="active",
                granted_by=actor.owner_id,
            )
        )
        self.session.flush()
        self._audit(actor, "team_approval.team_created", tid, {"name": name})
        return self._team_view(team)

    def list_teams(self, actor: Actor) -> list[dict[str, Any]]:
        """我能看到的团队 = 我在其中的团队。不给「全局团队列表」——
        那会让任何登录用户枚举出所有团队名。"""
        actor.require_authenticated()
        rows = self.session.execute(
            select(ApprovalTeam)
            .join(
                ApprovalTeamMember,
                ApprovalTeamMember.team_id == ApprovalTeam.id,
            )
            .where(
                ApprovalTeamMember.user_id == actor.owner_id,
                ApprovalTeamMember.state == "active",
                ApprovalTeam.state == "active",
            )
            .order_by(ApprovalTeam.created_at)
        ).scalars()
        return [self._team_view(t) for t in rows]

    def add_member(
        self, actor: Actor, team_id: str, user_id: str, *, role: str = "member"
    ) -> dict[str, Any]:
        """授予团队资格。**只有该团队的审批人**能加人。

        让任何成员都能扩审批权，等于审批权可以自我扩散——那这套资格就
        没有边界了。所以这条比「谁能批申请」更严：审批人自己才能拉新人进
        审批池。
        """
        actor.require_owner()
        if role not in ("requester", "approver", "member"):
            raise ValidationFailed("bad_role", f"Unknown team role {role!r}")
        user_id = (user_id or "").strip()
        if not user_id:
            raise ValidationFailed("user_required", "user_id is required")
        team = self._team(team_id)
        # 跨团队/非审批人：不泄露团队是否存在。
        self._require_approver(actor, team, action="add_member")

        existing = self.session.execute(
            select(ApprovalTeamMember).where(
                ApprovalTeamMember.team_id == team.id,
                ApprovalTeamMember.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 重复授予按幂等更新处理，而不是 Conflict：调用方重试一次
            # 不应该把整个流程炸掉。但降级/撤销要显式留痕。
            if existing.role == role and existing.state == "active":
                return self._member_view(existing)
            existing.role = role
            existing.state = "active"
            existing.granted_by = actor.owner_id
            existing.revoked_at = None
            existing.version += 1
            self.session.flush()
            self._audit(actor, "team_approval.member_updated", existing.id,
                        {"team_id": team.id, "user_id": user_id, "role": role})
            return self._member_view(existing)

        row = ApprovalTeamMember(
            id=f"atm-{uuid4().hex[:12]}",
            team_id=team.id,
            user_id=user_id,
            role=role,
            state="active",
            granted_by=actor.owner_id,
        )
        self.session.add(row)
        self.session.flush()
        self._audit(actor, "team_approval.member_added", row.id,
                    {"team_id": team.id, "user_id": user_id, "role": role})
        return self._member_view(row)

    def revoke_member(self, actor: Actor, team_id: str, user_id: str) -> dict[str, Any]:
        """撤销资格。保留行（``state='revoked'``）而不是删除。

        删掉行之后，「他当时为什么有资格批」就再也答不上来了；而审批记录的
        可解释性是这套流程存在的理由之一。
        """
        actor.require_owner()
        team = self._team(team_id)
        self._require_approver(actor, team, action="revoke_member")
        row = self.session.execute(
            select(ApprovalTeamMember).where(
                ApprovalTeamMember.team_id == team.id,
                ApprovalTeamMember.user_id == user_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("member_not_found", f"{user_id} is not a member of {team_id}")
        if row.state == "revoked":
            return self._member_view(row)
        row.state = "revoked"
        row.revoked_at = utcnow()
        row.version += 1
        self.session.flush()
        self._audit(actor, "team_approval.member_revoked", row.id,
                    {"team_id": team.id, "user_id": user_id})
        return self._member_view(row)

    def list_members(self, actor: Actor, team_id: str) -> list[dict[str, Any]]:
        """团队成员名册。**任何成员**都能看（包括只读的requester）——
        审批人是谁必须对申请人透明，否则他无法判断该不该找谁批。"""
        actor.require_authenticated()
        team = self._team(team_id)
        self._require_member(actor, team)
        rows = self.session.execute(
            select(ApprovalTeamMember)
            .where(ApprovalTeamMember.team_id == team.id)
            .order_by(ApprovalTeamMember.created_at)
        ).scalars()
        return [self._member_view(m) for m in rows]

    # ------------------------------------------------------------------
    # 提交申请
    # ------------------------------------------------------------------
    def submit(
        self,
        actor: Actor,
        team_id: str,
        *,
        execution_id: str,
        checkpoint: str,
        title: str = "",
        detail: dict | None = None,
        required_role: str = "approver",
        supersedes_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """提交一条团队审批申请，返回申请视图。

        这里的 ``interrupt`` 调用会把执行真的挂起：申请提交后，那个执行
        就卡在 ``checkpoint`` 上，直到有人拍板。**没有**「先落库再异步通知」
        这种中间态——申请存在就意味着执行停着。

        申请人必须是该团队成员（能带request 能力或以上）。
        """
        actor.require_authenticated()
        if required_role not in ("requester", "approver", "member"):
            raise ValidationFailed("bad_role", f"Unknown required_role {required_role!r}")
        team = self._team(team_id)
        self._require_member(actor, team)

        prior: TeamApprovalRequest | None = None
        if supersedes_id:
            prior = self._request_row(supersedes_id)
            if prior.team_id != team.id:
                raise NotFound("request_not_found", f"request_not_found: {supersedes_id}")

        interrupt = self.hitl.interrupt(
            actor,
            execution_id,
            checkpoint,
            context={
                "team_id": team.id,
                "requester_id": actor.owner_id,
                "title": title,
                "detail": dict(detail or {}),
                "required_role": required_role,
            },
            options=list(DECISION_OPTIONS),
            reason=f"team approval requested for {team.name}",
            timeout_seconds=timeout_seconds,
        )

        row = TeamApprovalRequest(
            id=f"tar-{uuid4().hex[:12]}",
            team_id=team.id,
            interrupt_id=interrupt["id"],
            requester_id=actor.owner_id,
            required_role=required_role,
            round=(prior.round + 1) if prior else 1,
            title=title,
            detail=dict(detail or {}),
            supersedes_id=supersedes_id,
        )
        self.session.add(row)
        self.session.flush()
        self._audit(actor, "team_approval.requested", row.id,
                    {"team_id": team.id, "interrupt_id": interrupt["id"],
                     "round": row.round})
        return self._view(row)

    def list_requests(
        self, actor: Actor, team_id: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        """团队的全部申请（含已决策），按时间倒序。

        可见性 = 我是该团队成员。不提供跨团队的申请列表。
        """
        actor.require_authenticated()
        team = self._team(team_id)
        self._require_member(actor, team)
        rows = self.session.execute(
            select(TeamApprovalRequest)
            .where(TeamApprovalRequest.team_id == team.id)
            .order_by(TeamApprovalRequest.created_at.desc())
        ).scalars()
        out = [self._view(r) for r in rows]
        if status is not None:
            out = [r for r in out if r["status"] == status]
        return out

    def get(self, actor: Actor, request_id: str) -> dict[str, Any]:
        """取单条申请。不属于你的团队 → 按「不存在」处理。"""
        actor.require_authenticated()
        row = self._request_row(request_id)
        team = self._team(row.team_id)
        self._require_member(actor, team)
        return self._view(row)

    # ------------------------------------------------------------------
    # 拍板
    # ------------------------------------------------------------------
    def decide(
        self,
        actor: Actor,
        request_id: str,
        decision: str,
        *,
        note: str = "",
        resolution: dict | None = None,
    ) -> dict[str, Any]:
        """审批人拍板。三个决策：``approve`` / ``reject`` / ``return_for_change``。

        校验顺序是有意义的，且**不能调换**：

        1. 先校验决策值本身在白名单里——这是**纯输入校验**，不泄露任何
           授权状态：``TEAM_DECISIONS`` 是公开常量，申请人在提交时看到的就是
           这三个选项。所以「你传了一个不存在的决策」与「你有没有资格」无关，
           应当先报 422，而不是先撞上403 让人以为是自己没权限（实测中这会
           把「决策值非法」误报成「利益冲突」，纯属误导）。
        2. 再确认这条申请属于调用者的团队（否则按不存在处理）——
           跨团队的人不该从403 知道「这里有一条申请」。
        3. 再校验拍板资格（成员行 + required_role）。
        4. 再回避利益冲突（requester 不能自己批）。

        为什么没有直接调 ``HitlInterruptService.decide``
        --------------------------------------------------
        HITL 的 ``decide`` 里有两条**与团队语义冲突**的规则：它要求
        ``actor.require_owner()``（这条我们也要，保留），以及
        ``_require_visible``——**只允许中断行自己的owner 拍板**。而团队审批
        的定义恰恰是「申请人不能拍自己的板，必须由**另一个**有资格的成员拍」。
        也就是说，团队审批要触达的那一行，在 HITL 的可见性规则下**对审批人
        不可见**。

        HITL 是已验收的成品（只读），所以这里不改动它、也不去伪造一个
        「看起来像请求人」的 Actor 来骗过 ``_require_visible``（那会让审计里的
        ``decided_by`` 变成一个假身份）。本方法改为在**做完团队授权之后**，
        自己执行那条与 HITL 完全同形的状态流转：

        * 决策值必须在 ``options`` 白名单里（与 ``hitl.decide`` 同一判据）；
        * 条件 UPDATE ``WHERE id=? AND status='pending'``，``rowcount == 0``
          即冲突——**单赢家语义与 HITL 逐字一致**，两个并发审批只有一个能赢；
        * 已决策/ 已超时的拒绝语义也一致（超时先落盘再报错，否则这次拒绝了
          但库里仍是 pending，超时就是个谎言）。

        也就是说：HITL 已经做好的东西（归属隔离、单赢家、决策白名单、超时
        语义）**一条都没有被削弱**，团队层只在它前面加了一道「你有没有资格
        代表这个团队拍板」的闸门。
        """
        actor.require_owner()  # 执行体不能代替人拍板
        # 1) 纯输入校验先行（不泄露授权状态）
        if decision not in TEAM_DECISIONS:
            raise ValidationFailed(
                "bad_decision",
                f"decision must be one of {list(TEAM_DECISIONS)}, got {decision!r}",
            )
        # 2)~4) 团队归属 -> 拍板资格 -> 利益冲突
        row = self._request_row(request_id)
        team = self._team(row.team_id)
        self._require_member(actor, team)
        self._require_eligible_approver(actor, team, row, action="decide")

        interrupt = self._interrupt_row(row)
        if interrupt is None:
            raise NotFound("request_not_found", f"request_not_found: {request_id}")
        if interrupt.status != "pending":
            raise Conflict(
                "already_decided",
                f"Request {request_id} was already decided "
                f"(status={interrupt.status}, decision={interrupt.decision})",
            )
        if interrupt.expires_at is not None and interrupt.expires_at <= utcnow():
            # 先落盘再报错——否则这次拒绝了但库里仍是 pending，下一个人还能
            # 对同一行拍板，超时就是个谎言。
            self._mark_expired(actor, interrupt)
            raise Conflict(
                "interrupt_expired",
                f"Request {request_id} passed its deadline unanswered",
            )
        allowed = {o["value"] for o in (interrupt.options or [])}
        if decision not in allowed:
            raise ValidationFailed(
                f"decision {decision!r} is not one of the offered options: "
                f"{sorted(allowed)}"
            )

        # 条件 UPDATE：只有仍处于 pending 的那一行会被命中。重复/并发决策的
        # 第二个请求拿到 rowcount == 0，据此拒绝（与 hitl.decide 同一条纪律）。
        res = self.session.execute(
            update(HitlInterrupt)
            .where(
                HitlInterrupt.id == interrupt.id,
                HitlInterrupt.status == "pending",
            )
            .values(
                # 刻意复用 HITL 的决策→终态映射（含「未显式批准/取消一律
                # rejected」的保守默认），而不是在团队层重写一份。抄一份
                # 就会漂移：HITL 那边改了保守默认，团队这边还按旧规则记终态，
                # 审计链上就会出现两个对同一决策不一致的分类。
                # 见类属性 ``_status_mapper`` 处的说明（含双向注释约定）。
                status=self._status_mapper(decision),
                decision=decision,
                resolution={
                    **(resolution or {}),
                    "note": note,
                    "team_id": team.id,
                },
                decided_by=actor.owner_id,
                decided_at=utcnow(),
                updated_at=utcnow(),
                version=HitlInterrupt.version + 1,
            )
        )
        if res.rowcount == 0:
            raise Conflict(
                "already_decided",
                f"Request {request_id} was decided concurrently",
            )
        self.session.flush()
        self._audit(actor, "team_approval.decided", row.id,
                    {"decision": decision, "team_id": team.id,
                     "interrupt_id": row.interrupt_id})
        return self._view(self._request_row(request_id))

    def _mark_expired(self, actor: Actor, interrupt: HitlInterrupt) -> None:
        self.session.execute(
            update(HitlInterrupt)
            .where(
                HitlInterrupt.id == interrupt.id,
                HitlInterrupt.status == "pending",
            )
            .values(
                status="expired",
                decision=HITL_TIMEOUT_DECISION,
                decided_at=utcnow(),
                updated_at=utcnow(),
                version=HitlInterrupt.version + 1,
            )
        )
        self.session.flush()
        self._audit(actor, "team_approval.expired", interrupt.id,
                    {"execution_id": interrupt.execution_id})

    # ------------------------------------------------------------------
    # 内部：可见性与资格
    # ------------------------------------------------------------------
    def _team(self, team_id: str) -> ApprovalTeam:
        row = self.session.get(ApprovalTeam, team_id)
        if row is None:
            raise NotFound("team_not_found", f"team_not_found: {team_id}")
        return row

    def _request_row(self, request_id: str) -> TeamApprovalRequest:
        row = self.session.get(TeamApprovalRequest, request_id)
        if row is None:
            raise NotFound("request_not_found", f"request_not_found: {request_id}")
        return row

    def _member_row(self, team_id: str, user_id: str) -> ApprovalTeamMember | None:
        return self.session.execute(
            select(ApprovalTeamMember).where(
                ApprovalTeamMember.team_id == team_id,
                ApprovalTeamMember.user_id == user_id,
            )
        ).scalar_one_or_none()

    def _require_member(self, actor: Actor, team: ApprovalTeam) -> ApprovalTeamMember:
        """必须是该团队**在册**成员。跨团队按「不存在」处理。"""
        if actor.subject_type != "owner":
            raise NotFound("team_not_found", f"team_not_found: {team.id}")
        row = self._member_row(team.id, actor.owner_id)
        if row is None or row.state != "active" or team.state != "active":
            # 不确认团队是否存在：外人拿到的是 404，与「没这��团队」无法区分。
            raise NotFound("team_not_found", f"team_not_found: {team.id}")
        return row

    def _require_approver(
        self, actor: Actor, team: ApprovalTeam, *, action: str
    ) -> ApprovalTeamMember:
        """必须持有该团队的审批权（用于加人/撤人这类治理动作）。"""
        row = self._require_member(actor, team)
        if row.role not in ("approver", "member"):
            raise PermissionDenied(
                "approver_required",
                f"Only approvers of team {team.id} can {action}",
                403,
            )
        return row

    def _require_eligible_approver(
        self, actor: Actor, team: ApprovalTeam, request: TeamApprovalRequest, *, action: str
    ) -> ApprovalTeamMember:
        """拍板资格 = 在册 + 角色达标 + **不是申请人本人**。

        三条都过才放行。顺序上先角色后利益冲突：非审批人压根不该知道这条
        申请跟自己有关，所以给他的拒绝理由是「你不是审批人」而不是
        「你是申请人」——后者会泄露申请人与团队的关系。
        """
        row = self._require_member(actor, team)
        needed = (
            {"approver", "member"}
            if request.required_role == "approver"
            else {request.required_role, "member"}
        )
        if row.role not in needed:
            raise PermissionDenied(
                "approver_required",
                f"Decision on {request.id} requires role "
                f"{request.required_role!r} in team {team.id}",
                403,
            )
        if request.requester_id == actor.owner_id:
            # 利益冲突：自己提的申请自己批，等于没有审批。
            raise PermissionDenied(
                "self_approval_forbidden",
                f"Request {request.id} was submitted by you; an approver cannot "
                "decide their own request",
                403,
            )
        return row

    # ------------------------------------------------------------------
    # 内部：视图
    # ------------------------------------------------------------------
    def _interrupt_row(self, request: TeamApprovalRequest) -> HitlInterrupt | None:
        return self.session.get(HitlInterrupt, request.interrupt_id)

    def _view(self, request: TeamApprovalRequest) -> dict[str, Any]:
        """申请视图。状态**从 HITL 那一行读**，本表不持有状态。"""
        iv = self._interrupt_view(request)
        status = iv.get("status")
        return {
            "id": request.id,
            "team_id": request.team_id,
            "interrupt_id": request.interrupt_id,
            "execution_id": iv.get("execution_id"),
            "checkpoint": iv.get("checkpoint"),
            "requester_id": request.requester_id,
            "required_role": request.required_role,
            "round": request.round,
            "supersedes_id": request.supersedes_id,
            "title": request.title,
            "detail": request.detail or {},
            "status": status,
            "pending": status == "pending",
            "decided": status in HITL_DECIDED_STATUSES,
            "decision": iv.get("decision"),
            "decided_by": iv.get("decided_by"),
            "decided_at": iv.get("decided_at"),
            "options": iv.get("options", []),
            "created_at": iv.get("created_at"),
            "version": request.version,
        }

    def _interrupt_view(self, request: TeamApprovalRequest) -> dict[str, Any]:
        """读 HITL 行的视图。

        刻意**不**走 ``HitlInterruptService.get``：那个方法的可见性规则是
        「只看得到自己创建的中断」，而团队里任何成员都需要看到本团队的申请
        （申请人要追踪自己的申请、其他成员要看审批队列）。这里要的是团队
        可见性，所以直接读行、只取展示所需的字段。**读取**放宽是安全的：
        真正的写入关卡在 :meth:`decide`，那里重新做了一遍完整的团队授权。
        """
        row = self._interrupt_row(request)
        if row is None:
            # 历史脏数据：申请行在但中断行没了。降级为空视图而不是抛错——
            # 一条坏申请不该把整个列表接口炸掉，而 get() 已经在别处报过了。
            return {}
        return {
            "status": row.status,
            "decision": row.decision,
            "execution_id": row.execution_id,
            "checkpoint": row.checkpoint,
            "options": list(row.options or []),
            "decided_by": row.decided_by,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _audit(
        self, actor: Actor, action: str, target: str, details: dict | None
    ) -> None:
        if self.audit is not None:
            self.audit.append(actor, action, target, details or {})

    @staticmethod
    def _team_view(team: ApprovalTeam) -> dict[str, Any]:
        return {
            "id": team.id,
            "name": team.name,
            "created_by": team.created_by,
            "state": team.state,
            "created_at": team.created_at.isoformat() if team.created_at else None,
            "version": team.version,
        }

    @staticmethod
    def _member_view(member: ApprovalTeamMember) -> dict[str, Any]:
        return {
            "id": member.id,
            "team_id": member.team_id,
            "user_id": member.user_id,
            "role": member.role,
            "state": member.state,
            "granted_by": member.granted_by,
            "revoked_at": member.revoked_at.isoformat() if member.revoked_at else None,
            "version": member.version,
        }
