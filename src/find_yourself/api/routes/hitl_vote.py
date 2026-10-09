"""人工介入投票表决 HTTP 路由（A-人工介入-03 · P17）。

范式与 ``api/routes/hitl.py`` / ``kanban.py`` 一致：

* 读路由挂 ``get_actor``，改动路由挂 ``csrf_protected``；
* **任何 body 都不接受 ``owner_id``/``role``**——actor 一律由服务端解析；
* 错误统一走 ``services/errors.py`` 的 DomainError 信封（全局处理器转成
  ``{"error": {code, message, details}}``），不泄露栈或他人对象；
* 改动路由**必须显式 commit**（本仓约定：service 只 flush，commit 由路由做——
  ``get_db`` 只 yield + close 不提交，见 ``api/deps.py``）。漏掉这一行的症状很
  隐蔽：响应体看起来正常，但请求结束时改动被回滚了。

🔴 本文件**尚未注册**到 ``api/routes/__init__.py``：那是跨包锁，由主控统一追加
（提交信息里的 ``NEEDS-LOCK: routes=hitl_vote``）。注册前这些端点不可达——这是
有意为之，不是遗漏。因此 HTTP 层测试用「把本 router 挂到裸 app 上」的方式跑，
不需要动共享的路由注册表。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from ...services.actor import Actor
from ...services.hitl_vote import HitlVoteService
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/hitl", tags=["hitl-vote"])


def _svc(svc: Services) -> HitlVoteService:
    """按请求装配投票服务。

    🔴 刻意**不在** ``Services`` 上加 ``hitl_vote`` 字段：``api/deps.py`` 是跨包锁，
    与 ``routes/__init__.py`` 同级，由主控统一追加。在那之前，这里用容器里**已
    存在**的三个对象现场拼一个——``svc.session`` / ``svc.audit`` /
    ``svc.hitl`` 都是 ``Services`` 的公开字段，不构成新的跨包改动。

    复用 ``svc.hitl``（而非新建 ``HitlInterruptService``）很关键：投票收口要把
    结论回灌进那条中断，两处必须是**同一个**实例，否则 audit 哈希链会断在中间。
    """
    return HitlVoteService(svc.session, svc.audit, hitl=svc.hitl)


def _commit(svc: Services) -> None:
    """Commit the request-scoped session after a mutation.

    🔴 本仓约定：**service 层只 flush，commit 由路由层做**。照 ``kanban.py`` /
    ``agent_teams.py`` 的写法每个改动路由都显式 commit。漏掉时响应体会返回
    已 flush 的新状态（看起来完全正常），但 session.close() 会把改动回滚。
    """
    svc.session.commit()


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class CandidateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 必须是该 HITL 中断 options 里某个 value；不是则 422
    #: （见 services/hitl_vote.py 的规矩 2）
    value: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=200)
    proposer: str = Field(default="", max_length=200)
    #: 候选项固有权重，≥0（整数，让平手可精确判定）
    weight: int = Field(default=1, ge=0)


class OpenVoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(default="", max_length=1000)
    candidates: list[CandidateSpec] = Field(min_length=1)
    tie_options: list[str] | None = None


class BallotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voter: str = Field(min_length=1, max_length=200)
    option: str = Field(min_length=1, max_length=64)
    weight: int = Field(default=1, ge=1, le=1000)
    reason: str = Field(default="", max_length=2000)


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 不传 = 按票数取唯一最高者（平手则 409 vote_tied）；传 = 用户定音
    option: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=2000)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
@router.get("/votes")
async def list_votes(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """该 owner 名下的投票（人的工作台视图）。"""
    return _svc(svc).list_votes(actor)


@router.get("/votes/{vote_id}")
async def get_vote(
    vote_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """会话全貌：候选人 + 票根 + 计票。"""
    return _svc(svc).view(actor, vote_id)


@router.get("/votes/{vote_id}/tally")
async def vote_tally(
    vote_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """当前计票（纯读，不落库、不进审计）。``tied`` 告诉前端要不要渲染「二选一」。"""
    return _svc(svc).tally(actor, vote_id)


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
@router.post("/interrupts/{interrupt_id}/vote", status_code=status.HTTP_201_CREATED)
async def open_vote(
    interrupt_id: str,
    body: OpenVoteBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """为一条 pending 中断开一场投票。

    候选必须来自该中断已 offer 的选项，否则 422 ``candidate_not_offered``——
    投票是在回答同一个问题，不是在旁边另开一条路。
    """
    out = _svc(svc).open_vote(
        actor,
        interrupt_id,
        question=body.question,
        candidates=[c.model_dump() for c in body.candidates],
        tie_options=body.tie_options,
    )
    _commit(svc)
    return out


@router.post("/votes/{vote_id}/ballots", status_code=status.HTTP_201_CREATED)
async def cast_ballot(
    vote_id: str,
    body: BallotBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """投一票。同一人重复投票 = 改票，不新增行。"""
    out = _svc(svc).cast_ballot(
        actor, vote_id,
        voter=body.voter, option=body.option,
        weight=body.weight, reason=body.reason,
    )
    _commit(svc)
    return out


@router.post("/votes/{vote_id}/close")
async def close_vote(
    vote_id: str,
    body: CloseBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """收口并把结论写回那条 HITL 中断。

    * 不传 ``option``：按票数。**平手 → 409 ``vote_tied``**（用户定音是需求要求
      的行为，不是兜底）。
    * 传 ``option``：用户定音路径，``resolved_by`` 记 ``owner_tiebreak``。
    """
    out = _svc(svc).close(actor, vote_id, option=body.option, reason=body.reason)
    _commit(svc)
    return out


@router.post("/votes/{vote_id}/cancel")
async def cancel_vote(
    vote_id: str,
    body: ReasonBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """作废一场投票。底层中断**不动**——它仍在等一个真人的决定。"""
    out = _svc(svc).cancel(actor, vote_id, reason=body.reason)
    _commit(svc)
    return out
