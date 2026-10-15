"""P06 · 团队执行驱动（后台轮次）。

``start_team`` 只负责建实例、冻结 binding、翻状态；真正让成员跑起来的是这里：
起一个后台线程，用**自己的** session 反复调用
:meth:`AgentTeamService.drive_once`，直到团队落到终态或触达轮次上限。

为什么必须有自己的 session：请求 session 随响应结束就关闭，后台线程再拿它写
状态必然 ``Session is closed``（P06 原始症状之一）。线程内每轮独立
commit，进程崩溃时已完成轮次不会丢，最多损失最后一轮。

诚实边界（不夸大）：
* 这是**轮次驱动**，不是 Temporal 级别的可靠工作流（见 T7 文档：Temporal 仍是
  正式可选引擎）。进程重启后不会自动续跑未终结的团队，需要重新 start。
* ``max_rounds`` 是硬上限，超时即写明 ``runner_gave_up`` 原因并把仍 running 的
  成员标 failed，不留永久 running。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from ..identity import Actor

log = logging.getLogger(__name__)

#: 已被后台驱动的团队，避免重复起线程（同一 team 只跑一个 runner）。
_running: set[str] = set()
_lock = threading.Lock()

#: 单轮最多推进的成员数（每成员一次模型调用）。
DEFAULT_MAX_MEMBERS = 3
#: 最多轮次；每轮至少推进一个成员，否则判定无进展并收尾。
DEFAULT_MAX_ROUNDS = 8
#: 轮间停顿，给 UI 留出读事件的机会。
DEFAULT_ROUND_PAUSE = 0.05

#: 已终结的 team_id（只用于观测/幂等，不参与调度）。
_finished: set[str] = set()


def is_driving(team_id: str) -> bool:
    with _lock:
        return team_id in _running


def _claim(team_id: str) -> bool:
    with _lock:
        if team_id in _running:
            return False
        _running.add(team_id)
        return True


def _release(team_id: str) -> None:
    with _lock:
        _running.discard(team_id)


def drive_team_background(
    *,
    session_maker: Callable[[], Any],
    team_id: str,
    owner_id: str,
    service_factory: Callable[[Any], Any] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_members: int = DEFAULT_MAX_MEMBERS,
    round_pause_seconds: float = DEFAULT_ROUND_PAUSE,
    synchronous: bool = False,
) -> bool:
    """后台驱动一个团队。返回是否真的被调度（已在跑则 False）。

    ``service_factory(session) -> AgentTeamService`` 可注入：生产用真实服务，
    测试用假件（不连库、不起线程也能验证调度语义）。
    ``synchronous=True`` 时同线程跑完（测试与 CLI 同步路径用）。
    """
    if not _claim(team_id):
        return False
    try:
        if synchronous:
            _run(service_factory, session_maker, team_id, owner_id,
                 max_rounds, max_members, round_pause_seconds)
            return True
        threading.Thread(
            target=_run,
            args=(service_factory, session_maker, team_id, owner_id,
                  max_rounds, max_members, round_pause_seconds),
            name=f"team-runner-{team_id}",
            daemon=True,
        ).start()
        return True
    except Exception:
        _release(team_id)
        raise


def _run(service_factory, session_maker, team_id, owner_id,
         max_rounds, max_members, round_pause) -> None:
    actor = Actor.owner(owner_id)
    try:
        session = session_maker()
        try:
            svc = service_factory(session)
            for round_no in range(1, max_rounds + 1):
                snap = svc.drive_once(actor, team_id, max_members=max_members)
                state = ((snap or {}).get("team") or {}).get("state", "")
                log.info("team_runner round=%s team=%s state=%s",
                         round_no, team_id, state)
                if state in ("completed", "failed", "cancelled", "paused"):
                    _mark_finished(team_id)
                    return
                session.commit()
                time.sleep(round_pause)
            # 轮次用尽仍未终结：写明原因并收尾，绝不留永久 running。
            _give_up_if_still_running(svc, actor, team_id)
            session.commit()
        finally:
            session.close()
    except Exception:  # pragma: no cover - 后台线程不把异常吞成静默
        log.exception("team_runner crashed team=%s", team_id)
    finally:
        _release(team_id)


def _mark_finished(team_id: str) -> None:
    with _lock:
        _finished.add(team_id)


def _give_up_if_still_running(svc, actor, team_id: str) -> None:
    team = svc.get_team(actor, team_id)
    stuck = [i for i in svc._members(team_id) if i.state == "running"]
    if not stuck:
        return
    for inst in stuck:
        svc._fail_member(actor, team, inst, "runner_gave_up: 轮次用尽未终结")
    team.state = "failed"
    svc._emit_event(actor, team, "team.failed",
                    details={"reason": "runner_gave_up",
                             "stuck_roles": [i.role for i in stuck]})
    svc.session.flush()
