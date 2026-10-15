"""B6 上车仪式：外部 Agent 加入团队（身份登记 + 作用域绑定）。

* owner 邀请制：调用方必须是 owner（csrf 由路由层保证）；
* 身份必绑：service_id 必须指向存在且 active 的 ServiceIdentity，
  登记 who（service 名/kind）进成员条目，不接受匿名上车；
* role 唯一 + 格式校验 + depends_on 无环（与建队同一套门）；
* 乐观锁：expected_version 对不上即 Conflict；
* 审计留痕 team.member_joined（含 service 身份，不含密钥）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.agent_teams import AgentTeamService
from find_yourself.services.audit import AuditService
from find_yourself.services.auth import AuthService
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed


@pytest.fixture()
def teams(session):
    return AgentTeamService(session, AuditService(session))


@pytest.fixture()
def auth(session):
    return AuthService(session, AuditService(session))


def _team(teams, owner):
    return teams.create_team(
        owner, name="外援小队", template_id="engineering",
        budget_ref={"root_budget_usd": 0.50, "member_reserve_cap_usd": 0.05},
    )


def _service(auth, owner, name="ext-dify"):
    row, token = auth.issue_scoped_service_credential(
        owner, name=name, kind="agent", domains=["personal"], tools=[],
    )
    return row, token


def test_join_external_agent_records_identity(teams, auth, owner, session):
    team = _team(teams, owner)
    row, _token = _service(auth, owner)
    session.commit()
    version = team.version
    joined = teams.join_external(
        owner, team.id, expected_version=version, role="researcher",
        agent_host="dify", service_id=row.id,
    )
    assert joined["role"] == "researcher"
    assert joined["external"] is True
    assert joined["service_id"] == row.id
    assert joined["service_name"] == "ext-dify"
    fresh = teams.get_team(owner, team.id)
    assert fresh.version == version + 1
    roles = [m["role"] for m in fresh.members]
    assert "researcher" in roles


def test_join_rejects_unknown_or_revoked_service(teams, auth, owner, session):
    team = _team(teams, owner)
    with pytest.raises(NotFound, match="service"):
        teams.join_external(
            owner, team.id, expected_version=team.version, role="x",
            agent_host="dify", service_id="no-such-service",
        )
    row, _token = _service(auth, owner)
    session.commit()
    # 吊销后同样拒绝（state 非 active）。
    row.state = "revoked"
    session.flush()
    with pytest.raises(ValidationFailed, match="revoked|active|service"):
        teams.join_external(
            owner, team.id, expected_version=team.version, role="y",
            agent_host="dify", service_id=row.id,
        )


def test_join_rejects_duplicate_role_and_bad_version(teams, auth, owner, session):
    team = _team(teams, owner)
    row, _token = _service(auth, owner)
    session.commit()
    with pytest.raises(ValidationFailed, match="uplicate"):
        teams.join_external(
            owner, team.id, expected_version=team.version, role="coordinator",
            agent_host="dify", service_id=row.id,
        )
    with pytest.raises(Conflict, match="[Vv]ersion"):
        teams.join_external(
            owner, team.id, expected_version=team.version + 99, role="newbie",
            agent_host="dify", service_id=row.id,
        )


def test_join_requires_owner(teams, auth, owner, session):
    from find_yourself.services.actor import Actor

    team = _team(teams, owner)
    row, _token = _service(auth, owner)
    session.commit()
    other = Actor.owner("owner-stranger")
    with pytest.raises(NotFound, match="Team"):
        teams.join_external(
            other, team.id, expected_version=team.version, role="spy",
            agent_host="dify", service_id=row.id,
        )
