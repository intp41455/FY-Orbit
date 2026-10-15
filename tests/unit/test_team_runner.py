"""P06 · 后台轮次驱动（team_runner）回归测试。

不连真库、不起真线程：用假 service / 假 session 验证三件事——
真的在推进、没有永久 running、同一团队不重复调度。
"""

from __future__ import annotations

import pytest

from find_yourself.services import team_runner


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeTeam:
    def __init__(self):
        self.state = "running"
        self.events: list[tuple[str, dict]] = []


class FakeInstance:
    def __init__(self, role: str, state: str = "running"):
        self.role = role
        self.state = state
        self.blocked_reason = ""


class FakeService:
    """按脚本化轮次返回快照，并记录 drive_once / _fail_member 调用。"""

    def __init__(self, rounds: list[str] | None = None, stuck: int = 0):
        self.rounds = rounds if rounds is not None else ["completed"]
        self.stuck = stuck
        self.calls = 0
        self.failed: list[str] = []
        self.team = FakeTeam()
        self._instances = [FakeInstance(f"m{i}") for i in range(self.stuck)]
        self.session = self

    def drive_once(self, actor, team_id, *, max_members=3):
        self.calls += 1
        idx = min(self.calls - 1, len(self.rounds) - 1)
        self.team.state = self.rounds[idx]
        return {"team": {"state": self.team.state}}

    def get_team(self, actor, team_id):
        return self.team

    def _members(self, team_id):
        return self._instances

    def _fail_member(self, actor, team, inst, reason):
        inst.state = "failed"
        inst.blocked_reason = reason
        self.failed.append(inst.role)

    def _emit_event(self, actor, team, event_type, **kwargs):
        self.team.events.append((event_type, kwargs))

    def flush(self):
        pass


def _factory(service):
    return lambda session: service


@pytest.fixture(autouse=True)
def _clean_registry():
    team_runner._running.clear()
    yield
    team_runner._running.clear()


def test_drive_team_background_runs_until_terminal_state():
    service = FakeService(rounds=["running", "running", "completed"])
    session = FakeSession()

    started = team_runner.drive_team_background(
        session_maker=lambda: session, team_id="team-1", owner_id="own-1",
        service_factory=_factory(service), round_pause_seconds=0,
        synchronous=True,
    )

    assert started is True
    assert service.calls == 3, "必须一直推到终态"
    assert session.commits >= 1
    assert session.closed is True, "线程用完必须关自己的 session"
    assert team_runner.is_driving("team-1") is False


def test_same_team_is_not_scheduled_twice_while_running():
    service = FakeService(rounds=["running"])
    session = FakeSession()
    # 先手工占位，模拟已有一个 runner 在跑
    team_runner._running.add("team-busy")

    started = team_runner.drive_team_background(
        session_maker=lambda: session, team_id="team-busy", owner_id="own-1",
        service_factory=_factory(service), round_pause_seconds=0,
        synchronous=True,
    )

    assert started is False, "同一团队不重复调度"
    assert service.calls == 0


def test_round_limit_marks_stuck_members_failed_with_reason():
    """轮次用尽仍未终结 → 写明 runner_gave_up 并收尾，不留永久 running。"""
    service = FakeService(rounds=["running", "running"], stuck=2)

    team_runner.drive_team_background(
        session_maker=FakeSession, team_id="team-give-up", owner_id="own-1",
        service_factory=_factory(service), max_rounds=2, round_pause_seconds=0,
        synchronous=True,
    )

    assert service.calls == 2
    assert sorted(service.failed) == ["m0", "m1"]
    assert all("runner_gave_up" in i.blocked_reason for i in service._instances)
    assert service.team.state == "failed"
    reasons = [d.get("details", {}).get("reason")
               for t, d in service.team.events if t == "team.failed"]
    assert "runner_gave_up" in reasons


def test_crash_does_not_leave_registry_poisoned():
    class Boom:
        def drive_once(self, *a, **k):
            raise RuntimeError("gateway exploded")

        def get_team(self, *a, **k):
            raise RuntimeError("gateway exploded")

    started = team_runner.drive_team_background(
        session_maker=FakeSession, team_id="team-crash", owner_id="own-1",
        service_factory=_factory(Boom()), round_pause_seconds=0, synchronous=True,
    )

    assert started is True
    assert team_runner.is_driving("team-crash") is False, \
        "崩溃后必须释放占位，否则该团队永远无法再次调度"
