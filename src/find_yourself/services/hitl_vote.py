"""人工介入投票表决（A-人工介入-03 · P17）。

**它解决什么问题**：``HitlInterruptService``（需求 12）让**一个人**在若干选项里拍板。
但真实争执往往是「两个 Agent 各执一词，谁的权重高都说不服谁」——这时让人从零
读一遍上下文再选，本身就是把机器的活推给人。本模块把这一步往下压一层：

    Agent 们投票 → 票数唯一最高 → 结论落回那条 HITL 中断
                  ↘ 票数平手   → **用户定音**（这才是需求里「用户定音」四个字
                                  真正落地的时刻）

设计上的六条硬规矩，每条都对应一个「不这么做会出什么事」：

1. **投票必须挂在一个真实的 pending 中断上。** ``open_vote`` 要拿
   ``interrupt_id`` 去查 HITL，查不到或已决策 → 拒。没有下游的投票是一条
   跟执行流脱钩的孤儿记录：投完了，然后没有任何东西被改变。
2. **候选必须是那条中断的选项的子集。** 中断当初只向人/offer 了那几个选项，
   投票却能加出一个第 5 条路，那这个投票就不是在回答同一个问题。
3. **票只能投给已登记的候选**，且权重是 ``1..1000`` 的整数。整数让「平手」可以
   精确判定——而平手正是这个功能存在的理由，浮点会把「看起来一样」判成不等。
4. **一人一票**（主键 ``(session_id, voter)``）。重复投票走 UPDATE 而非再插一行，
   所以「票数」恒等于「投票人数」，一个人刷不出十票；改票允许，但每次进审计链。
5. **平手不许自动破。** ``close`` 遇到并列最高票直接 409 ``vote_tied`` 并把并列
   选项返回给前端——「用户定音」是需求要求的行为，不是兜底。
6. **只有 owner 能收口。** ``HitlInterruptService.decide`` 本就要求
   ``require_owner``（「执行自己不能给自己放行」），本模块沿用同一条边界：
   Agent 可以投票，但落槌必须是人。

回灌：收口时调 ``HitlInterruptService.decide`` 把胜出选项写回那条中断，
``resolution`` 里带上计票明细，于是审计链上「谁投了什么、最后为什么这么定」
是可追的，而不是只剩一个结论。

审计：开票 / 投票 / 改票 / 收口 / 定音 / 作废全部 append 到哈希链。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.hitl_models import HitlInterrupt
from ..db.hitl_vote_models import (
    MAX_BALLOT_WEIGHT,
    MAX_CANDIDATES,
    HitlVoteBallot,
    HitlVoteCandidate,
    HitlVoteSession,
)
from ..db.types import utcnow
from .actor import Actor
from .audit import AuditService
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from .hitl import HitlInterruptService


class HitlVoteService:
    """投票表决。刻意保持小而完整——不是一个投票框架。"""

    def __init__(
        self,
        session: Session,
        audit: AuditService | None = None,
        *,
        hitl: HitlInterruptService | None = None,
    ):
        self.session = session
        self.audit = audit
        # 复用 HITL 主体而不是另造一套：投票的结论要能被同一条 decide() 消费。
        self.hitl = hitl or HitlInterruptService(session, audit)

    # ------------------------------------------------------------------
    # 开票
    # ------------------------------------------------------------------
    def open_vote(
        self,
        actor: Actor,
        interrupt_id: str,
        *,
        question: str = "",
        candidates: list[dict[str, Any]] | None = None,
        tie_options: list[str] | None = None,
    ) -> dict[str, Any]:
        """为一条 pending 中断开一场投票。

        ``candidates`` 每项 ``{"value", "label", "proposer", "weight"}``；
        ``value`` **必须**是那条中断 ``options`` 里某个 ``value``（规矩 2）。
        """
        actor.require_authenticated()
        interrupt = self._interrupt(interrupt_id)
        self._require_pending(interrupt)
        self._require_owner_of(actor, interrupt)

        if self.session.execute(
            select(HitlVoteSession).where(
                HitlVoteSession.interrupt_id == interrupt_id,
                HitlVoteSession.status == "open",
            )
        ).scalar_one_or_none() is not None:
            raise Conflict(
                "vote_already_open",
                f"Interrupt {interrupt_id} already has an open vote; close it "
                f"before opening another",
            )

        offered = {o["value"] for o in (interrupt.options or [])}
        specs = list(candidates or [])
        if not specs:
            raise ValidationFailed(
                "candidates_required",
                "a vote with no candidates is not a question",
            )
        if len(specs) > MAX_CANDIDATES:
            raise ValidationFailed(
                "too_many_candidates",
                f"a vote may carry at most {MAX_CANDIDATES} candidates, got {len(specs)}",
            )

        session_row = HitlVoteSession(
            id=f"vote-{uuid4().hex[:12]}",
            owner_id=actor.owner_id or actor.service_id,
            interrupt_id=interrupt_id,
            execution_id=interrupt.execution_id,
            question=question or f"Resolve the dispute at {interrupt.checkpoint}",
            status="open",
            winner_option=None,
            resolved_by=None,
            reason="",
            tie_options=list(tie_options) if tie_options else None,
            created_at=utcnow(),
            updated_at=utcnow(),
            version=1,
        )
        self.session.add(session_row)

        seen: set[str] = set()
        for spec in specs:
            value = str(spec.get("value") or "").strip()
            if not value:
                raise ValidationFailed("candidate_value_required",
                                       "every candidate needs a non-empty value")
            if value in seen:
                raise ValidationFailed(
                    "duplicate_candidate", f"candidate {value!r} listed twice"
                )
            if value not in offered:
                # 规矩 2：投票不得引入这条中断没 offer 过的路。
                raise ValidationFailed(
                    "candidate_not_offered",
                    f"candidate {value!r} is not one of the interrupt's offered "
                    f"options {sorted(offered)}; a vote must answer the same question",
                )
            seen.add(value)
            self.session.add(HitlVoteCandidate(
                session_id=session_row.id,
                option_value=value,
                label=str(spec.get("label") or value)[:200],
                proposer=str(spec.get("proposer") or "")[:200],
                weight=int(spec.get("weight", 1)),
                created_at=utcnow(),
            ))

        self.session.flush()
        self._audit(actor, "vote.opened", session_row.id, {
            "interrupt_id": interrupt_id,
            "candidates": sorted(seen),
        })
        return self.view(actor, session_row.id)

    # ------------------------------------------------------------------
    # 投票
    # ------------------------------------------------------------------
    def cast_ballot(
        self,
        actor: Actor,
        session_id: str,
        *,
        voter: str,
        option: str,
        weight: int = 1,
        reason: str = "",
    ) -> dict[str, Any]:
        """投一票。同一人重复投票 = 改票（UPDATE，不新增行）。

        🔴 **service 身份也能投票**——这不是放宽，是这功能的前提：需求叫
        「Agent 争执用户定音」，争执双方就是 Agent。若把投票也 gate 在 owner 上，
        Agent 永远投不了票，用户只能绕过投票直接拍板，那整个投票层就是死代码。
        所以这里只要求「已认证」（owner 或 service 皆可），``voter`` 字段记录
        投票人身份；**收口**才需要 owner（见 :meth:`close` 的 ``require_owner``）。
        """
        actor.require_authenticated()
        session_row = self._open_session_for_ballot(session_id)

        if not voter:
            raise ValidationFailed("voter_required", "a ballot needs a voter")
        # ⚠️ 刻意**只收 int**，不做 int() 强转：`int(3.5)` 会静默截断成 3，
        # 让「我投 3.5 票」变成「我投 3 票」而不报任何错——权重是决定平手与否的
        # 关键量，静默改写它等于伪造票数。bool 也拒（isinstance(True, int) 为真）。
        if isinstance(weight, bool) or not isinstance(weight, int):
            raise ValidationFailed(
                "weight_invalid",
                f"weight must be an integer, got {weight!r} "
                f"({type(weight).__name__})",
            )
        if not (1 <= weight <= MAX_BALLOT_WEIGHT):
            raise ValidationFailed(
                "weight_out_of_range",
                f"weight must be within 1..{MAX_BALLOT_WEIGHT}, got {weight}",
            )

        if not self._candidate_exists(session_id, option):
            raise ValidationFailed(
                "candidate_not_registered",
                f"{option!r} is not a registered candidate of this vote",
            )

        ballot = self.session.get(HitlVoteBallot, (session_id, voter))
        replaced: str | None = None
        if ballot is None:
            self.session.add(HitlVoteBallot(
                session_id=session_id,
                voter=voter,
                option_value=option,
                weight=weight,
                reason=reason or "",
                created_at=utcnow(),
                updated_at=utcnow(),
            ))
            action = "vote.cast"
        else:
            replaced = ballot.option_value
            ballot.option_value = option
            ballot.weight = weight
            ballot.reason = reason or ballot.reason
            ballot.updated_at = utcnow()
            action = "vote.recast"

        session_row.updated_at = utcnow()
        session_row.version += 1
        self.session.flush()
        self._audit(actor, action, session_id, {
            "voter": voter, "option": option, "weight": weight,
            **({"replaced": replaced} if replaced else {}),
        })
        return self._view_any(session_id)

    # ------------------------------------------------------------------
    # 计票 / 读
    # ------------------------------------------------------------------
    def tally(self, actor: Actor, session_id: str) -> dict[str, Any]:
        """当前计票。**不**落库，纯读——谁都能随时看，且不会污染审计。"""
        session_row = self._session(actor, session_id)
        counts, ballots = self._tally_rows(session_id)
        leader = self._leader(counts)
        return {
            "session_id": session_id,
            "status": session_row.status,
            "counts": counts,
            "ballot_count": len(ballots),
            "total_weight": sum(counts.values()),
            # 🔴 平手时 leader 必须是 None：报告一个「领先者」等于替用户做了决定，
            # 与 close() 拒绝自动破平手是同一条道理。并列项放在 tie_options。
            "leader": None if len(leader) > 1 else (leader[0] if leader else None),
            "tied": len(leader) > 1,
            "tie_options": leader if len(leader) > 1 else [],
        }

    def view(self, actor: Actor, session_id: str) -> dict[str, Any]:
        """会话全貌：会话 + 候选人 + 票根 + 计票（owner 收敛的读）。"""
        self._session(actor, session_id)
        return self._view_any(session_id)

    def _view_any(self, session_id: str) -> dict[str, Any]:
        """未做 owner 收敛的会话视图。

        仅供两处内部使用：owner 收敛后的 :meth:`view`，以及投票后的返回值
        （投票者可能是 service 身份，对它做 owner 收敛会把刚投的票变成 404）。

        收敛已在调用方做完，这里只按 id 取行。
        """
        session_row = self.session.get(HitlVoteSession, session_id)
        if session_row is None:
            raise NotFound("vote_not_found", f"Vote {session_id} not found")
        candidates = self.session.execute(
            select(HitlVoteCandidate)
            .where(HitlVoteCandidate.session_id == session_id)
            .order_by(HitlVoteCandidate.option_value)
        ).scalars().all()
        ballots = self.session.execute(
            select(HitlVoteBallot)
            .where(HitlVoteBallot.session_id == session_id)
            .order_by(HitlVoteBallot.voter)
        ).scalars().all()
        counts, _ = self._tally_rows(session_id)
        leader = self._leader(counts)
        return {
            "id": session_row.id,
            "owner_id": session_row.owner_id,
            "interrupt_id": session_row.interrupt_id,
            "execution_id": session_row.execution_id,
            "question": session_row.question,
            "status": session_row.status,
            "winner_option": session_row.winner_option,
            "resolved_by": session_row.resolved_by,
            "reason": session_row.reason,
            "tie_options": session_row.tie_options or [],
            "closed_at": _iso(session_row.closed_at),
            "decided_by": session_row.decided_by,
            "created_at": _iso(session_row.created_at),
            "updated_at": _iso(session_row.updated_at),
            "version": session_row.version,
            "candidates": [
                {
                    "option_value": c.option_value,
                    "label": c.label,
                    "proposer": c.proposer,
                    "weight": c.weight,
                }
                for c in candidates
            ],
            "ballots": [
                {
                    "voter": b.voter,
                    "option_value": b.option_value,
                    "weight": b.weight,
                    "reason": b.reason,
                    "cast_at": _iso(b.created_at),
                    "updated_at": _iso(b.updated_at),
                }
                for b in ballots
            ],
            "counts": counts,
            "ballot_count": len(ballots),
            "total_weight": sum(counts.values()),
            # 同 tally()：平手不报 leader。
            "leader": None if len(leader) > 1 else (leader[0] if leader else None),
            "tied": len(leader) > 1,
            # ⚠️ 这里**不要**再写一次 "tie_options"。
            # 本 dict 字面量上面已经有一处 ``"tie_options": session_row.tie_options or []``，
            # 重复键会让后者**静默覆盖**前者（Python 不报错，只留最后一个）——
            # 于是平手被后续投票打破、``session_row.tie_options`` 已是历史落库值时，
            # API 返回的却是「当前 tally 的实时平手集」，与库里存的不一致。
            # 语义上这里应以上面那处**落库值为准**：``tied``/``leader`` 是实时视图，
            # ``tie_options`` 是「当初平手时冻结的候选集」，两者本就不是一回事。
            # （ruff F601 就是盯着这行报的错。）
        }

    def list_votes(
        self, actor: Actor, *, status: str | None = None
    ) -> dict[str, Any]:
        """该 owner 名下的投票（人的工作台视图）。"""
        if not actor.owner_id:
            raise PermissionDenied("owner_required", "vote listing requires an owner actor")
        stmt = select(HitlVoteSession).where(HitlVoteSession.owner_id == actor.owner_id)
        if status:
            stmt = stmt.where(HitlVoteSession.status == status)
        rows = self.session.execute(
            stmt.order_by(HitlVoteSession.created_at.desc())
        ).scalars().all()
        return {
            "items": [
                {
                    "id": r.id,
                    "interrupt_id": r.interrupt_id,
                    "execution_id": r.execution_id,
                    "question": r.question,
                    "status": r.status,
                    "winner_option": r.winner_option,
                    "tied": bool(r.tie_options),
                    "created_at": _iso(r.created_at),
                    "closed_at": _iso(r.closed_at),
                }
                for r in rows
            ]
        }

    # ------------------------------------------------------------------
    # 收口 / 定音 / 作废
    # ------------------------------------------------------------------
    def close(
        self,
        actor: Actor,
        session_id: str,
        *,
        option: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """收口，把结论写回那条 HITL 中断。

        * 不传 ``option``：按票数取唯一最高者。**平手即 409**（规矩 5），
          并把并列选项返回去——「用户定音」是需求要求的行为，不是兜底。
        * 传 ``option``：用户定音路径（平手时二选一，或 owner 推翻票数）。
          该选项必须是已登记的候选。``resolved_by`` 记 ``owner_tiebreak``，
          事后能看出「这一槌不是票数决定的」。

        两条路径都要求 owner（规矩 6），与 ``HitlInterruptService.decide`` 一致。
        """
        actor.require_owner()
        session_row = self._session(actor, session_id)
        if session_row.status != "open":
            raise Conflict(
                "vote_closed",
                f"Vote {session_id} is already {session_row.status}",
            )
        if option is not None and not self._candidate_exists(session_id, option):
            raise ValidationFailed(
                "candidate_not_registered",
                f"{option!r} is not a registered candidate of this vote",
            )

        counts, _ballots = self._tally_rows(session_id)
        if not counts:
            raise ValidationFailed(
                "no_votes",
                f"Vote {session_id} has no ballots yet; nothing to decide",
            )
        leader = self._leader(counts)

        if option is not None:
            winner = option
            resolved_by = "owner_tiebreak"
        else:
            if len(leader) > 1:
                raise Conflict(
                    "vote_tied",
                    f"Vote {session_id} is tied between {leader}; a human must "
                    f"pick one (pass option=...) — an even tally is not a decision",
                )
            winner = leader[0]
            resolved_by = "tally"

        interrupt = self._interrupt(session_row.interrupt_id)
        if interrupt.status != "pending":
            raise Conflict(
                "interrupt_not_pending",
                f"Interrupt {session_row.interrupt_id} is {interrupt.status}; "
                f"a vote cannot decide it",
            )

        # 先落票，再回灌 HITL：反过来会在 decide() 失败时留下一张「已投但没结论」
        # 的票，而票本身是有效的（用户确实投了），不该被回灌失败连带作废。
        session_row.status = "resolved"
        session_row.winner_option = winner
        session_row.resolved_by = resolved_by
        session_row.reason = reason or ""
        session_row.tie_options = leader if len(leader) > 1 else None
        session_row.closed_at = utcnow()
        session_row.decided_by = actor.owner_id
        session_row.updated_at = utcnow()
        session_row.version += 1
        self.session.flush()

        self.hitl.decide(
            actor,
            session_row.interrupt_id,
            winner,
            resolution={
                "via": "hitl_vote",
                "vote_id": session_id,
                "resolved_by": resolved_by,
                "counts": counts,
                "reason": reason,
            },
            expected_version=None,
        )
        self._audit(actor, f"vote.{resolved_by}", session_id, {
            "winner": winner, "counts": counts, "tied": leader if len(leader) > 1 else [],
        })
        return self.view(actor, session_id)

    def cancel(self, actor: Actor, session_id: str, *, reason: str = "") -> dict[str, Any]:
        """作废一场投票。中断不动——它仍在等一个真人的决定，不该被一场被撤的
        投票悄悄「解决」掉。"""
        actor.require_owner()
        session_row = self._session(actor, session_id)
        if session_row.status != "open":
            raise Conflict("vote_closed", f"Vote {session_id} is already {session_row.status}")
        session_row.status = "cancelled"
        # 按 ck_vote_resolution_shape，cancelled 行必须**没有**胜出选项。这里
        # 不编一个假的 winner（「cancelled」不是一条路），前端据
        # ``status == 'cancelled'`` 即可与「真结论」区分。
        session_row.winner_option = None
        session_row.resolved_by = "cancelled"
        session_row.reason = reason or ""
        session_row.closed_at = utcnow()
        session_row.decided_by = actor.owner_id
        session_row.updated_at = utcnow()
        session_row.version += 1
        self.session.flush()
        self._audit(actor, "vote.cancelled", session_id, {"reason": reason})
        return self.view(actor, session_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _tally_rows(self, session_id: str) -> tuple[dict[str, int], list[HitlVoteBallot]]:
        ballots = self.session.execute(
            select(HitlVoteBallot).where(HitlVoteBallot.session_id == session_id)
        ).scalars().all()
        counts: dict[str, int] = {}
        for b in ballots:
            counts[b.option_value] = counts.get(b.option_value, 0) + int(b.weight)
        return counts, list(ballots)

    @staticmethod
    def _leader(counts: dict[str, int]) -> list[str]:
        """并列最高的选项。整数权重下判定精确；全零时返回空（还没有票）。"""
        if not counts:
            return []
        top = max(counts.values())
        if top <= 0:
            return []
        return sorted(k for k, v in counts.items() if v == top)

    def _candidate_exists(self, session_id: str, option: str | None) -> bool:
        if not option:
            return False
        return self.session.get(HitlVoteCandidate, (session_id, option)) is not None

    def _interrupt(self, interrupt_id: str) -> HitlInterrupt:
        row = self.session.get(HitlInterrupt, interrupt_id)
        if row is None:
            raise NotFound("interrupt_not_found", f"Interrupt {interrupt_id} not found")
        return row

    @staticmethod
    def _require_pending(interrupt: HitlInterrupt) -> None:
        if interrupt.status != "pending":
            raise Conflict(
                "interrupt_not_pending",
                f"Interrupt {interrupt.id} is {interrupt.status}; a dispute vote "
                f"only makes sense while nobody has answered yet",
            )

    @staticmethod
    def _require_owner_of(actor: Actor, interrupt: HitlInterrupt) -> None:
        if interrupt.owner_id and interrupt.owner_id != actor.owner_id:
            # 别人的争执不该由我开票。不泄露存在性：与既有路由一致按 not found 回。
            raise NotFound("interrupt_not_found", "Interrupt not found")

    def _session(self, actor: Actor, session_id: str) -> HitlVoteSession:
        row = self.session.get(HitlVoteSession, session_id)
        if row is None or row.owner_id != (actor.owner_id or ""):
            raise NotFound("vote_not_found", f"Vote {session_id} not found")
        return row

    def _open_session_for_ballot(self, session_id: str) -> HitlVoteSession:
        """取一场仍开放的投票，**不**做 owner 收敛——Agent 要能投票（见 cast_ballot）。"""
        row = self.session.get(HitlVoteSession, session_id)
        if row is None:
            raise NotFound("vote_not_found", f"Vote {session_id} not found")
        if row.status != "open":
            raise Conflict("vote_closed", f"Vote {session_id} is {row.status}")
        return row

    def _audit(self, actor: Actor, action: str, target: str, details: dict) -> None:
        if self.audit is None:
            return
        self.audit.append(actor, action, target, details)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
