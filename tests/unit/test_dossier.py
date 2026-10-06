"""P15 任务档案库与企业模式服务层测试（A-三重模式-03 / A-上下文持久化-02/-03）。

测的是一条主线：**档案是读模型，不是第二份真源**。

* 档案 / 简报 / 复盘都从既有表 + 审计链现算；空字段必须**说明为什么空**；
* 「一秒上手」简报有**硬字数上限**，超限明确标注截断（不假装是全量）；
* 知识沉淀落盘且 **owner 隔离**；
* 企业模式是**声明式映射**：未映射字段全部保留（不静默丢弃），权限/身份**复用**
  既有服务（不另起多租户）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from find_yourself.db.models import AuditEvent, Task, TaskAttempt, TaskEvent
from find_yourself.db.team_models import AgentInstance, TeamDefinition
from find_yourself.services.actor import Actor
from find_yourself.services.dossier.archive import TaskArchive
from find_yourself.services.dossier.enterprise import (
    EXAMPLE_EXTERNAL,
    EnterpriseAdapter,
    adapter_catalog,
)
from find_yourself.services.errors import NotFound, ValidationFailed

GOAL = "把产品资料变成三条小红书文案"


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1")


@pytest.fixture()
def other() -> Actor:
    return Actor.owner("owner-2")


@pytest.fixture()
def archive(session, tmp_path) -> TaskArchive:
    return TaskArchive(session, directory=tmp_path / "dossier")


def _mk_task(session, **kw) -> Task:
    task = Task(
        id=kw.pop("task_id", "task-1"),
        owner_id=kw.pop("owner_id", "owner-1"),
        goal=kw.pop("goal", GOAL),
        deadline=kw.pop("deadline", datetime(2027, 1, 1, tzinfo=timezone.utc)),
        idempotency_key=kw.pop("idempotency_key", "idem-1"),
        **kw,
    )
    session.add(task)
    session.flush()
    return task


# --------------------------------------------------------------------------- #
# 档案全景
# --------------------------------------------------------------------------- #
def test_archive_reports_objective_and_sources(archive, owner, session):
    _mk_task(session, status="running", stage="execution", progress_percent=40, steps=2)
    data = archive.archive(owner, "task-1")
    assert data["objective"]["goal"] == GOAL
    assert data["objective"]["progress_percent"] == 40
    # 每个字段都能说清来源（防「档案从哪来」说不清）
    assert set(data["sources"]) == {
        "objective", "milestones/change_log", "roster", "decisions", "artifacts",
        "dependencies",
    }


def test_empty_archive_explains_why_each_part_is_empty(archive, owner, session):
    _mk_task(session)
    data = archive.archive(owner, "task-1")
    assert data["milestones"] == [] and data["artifacts"] == []
    assert data["roster"]["members"] == []
    joined = " ".join(data["notes"])
    assert "里程碑为空" in joined
    assert "分工表为空" in joined
    assert "产出物版本为空" in joined


def test_archive_refuses_another_owners_task(archive, owner, session):
    _mk_task(session, owner_id="owner-2")
    with pytest.raises(NotFound):
        archive.archive(owner, "task-1")


def test_milestones_and_artifacts_come_from_real_rows(archive, owner, session):
    _mk_task(session)
    session.add(TaskEvent(id="e1", task_id="task-1", owner_id="owner-1", kind="status",
                          from_status="queued", to_status="running"))
    session.add(TaskEvent(id="e2", task_id="task-1", owner_id="owner-1", kind="progress",
                          detail={"percent": 60}))
    session.add(TaskAttempt(id="a1", task_id="task-1", attempt_no=1, status="succeeded",
                            checkpoint_ref="ck:1"))
    session.flush()
    data = archive.archive(owner, "task-1")
    assert [m["kind"] for m in data["milestones"]] == ["status", "progress"]
    assert data["artifacts"][0]["version"] == 1
    assert data["artifacts"][0]["checkpoint_ref"] == "ck:1"


def test_roster_comes_from_team_and_member_rows(archive, owner, session):
    _mk_task(session)
    session.add(TeamDefinition(id="team-1", owner_id="owner-1", name="写作小队",
                              root_task_id="task-1"))
    session.flush()   # 先落团队，成员实例有指向它的外键
    session.add(AgentInstance(id="ai-1", team_id="team-1", role="drafter",
                              session_id="sess-1", root_task_id="task-1",
                              state="running", effective_model="qwen-max"))
    session.flush()
    data = archive.archive(owner, "task-1")
    assert data["roster"]["teams"][0]["name"] == "写作小队"
    assert data["roster"]["members"][0]["role"] == "drafter"
    assert data["roster"]["members"][0]["effective_model"] == "qwen-max"


def test_decision_log_comes_from_the_audit_chain(archive, owner, session, audit):
    _mk_task(session)
    audit.append(owner, "task.paused", "task-1", {"reason": "等评审"})
    session.flush()
    archive.audit = audit
    data = archive.archive(owner, "task-1")
    assert any(d["action"] == "task.paused" for d in data["decisions"])


def test_decision_log_is_owner_isolated(archive, owner, other, session, audit):
    _mk_task(session)
    audit.append(owner, "task.paused", "task-1", {})
    session.flush()
    archive.audit = audit
    # other 读不到 owner-1 的任务（404），因此决策日志也不会泄漏
    with pytest.raises(NotFound):
        archive.archive(other, "task-1")


# --------------------------------------------------------------------------- #
# 「翻档案」一秒上手
# --------------------------------------------------------------------------- #
def test_briefing_is_pasteable_and_mentions_the_essentials(archive, owner, session):
    _mk_task(session, blocked_reason="等评审")
    out = archive.briefing(owner, "task-1")
    assert out["task_id"] == "task-1"
    assert GOAL in out["briefing"]
    assert "谁在干（分工）" in out["briefing"]
    assert "卡点：等评审" in out["briefing"]
    assert out["truncated"] is False
    assert out["chars"] <= out["limit"]


def test_briefing_respects_the_hard_char_limit_and_says_so(archive, owner, session):
    _mk_task(session)
    for i in range(40):
        session.add(TaskEvent(id=f"e{i}", task_id="task-1", owner_id="owner-1",
                              kind="progress", detail={"i": i}))
    session.flush()
    out = archive.briefing(owner, "task-1", limit=200)
    assert out["truncated"] is True
    assert out["chars"] <= 200
    assert "简报已截断" in out["briefing"]
    assert out["archive_endpoint"].endswith("/archive")


def test_briefing_rejects_a_useless_limit(archive, owner, session):
    _mk_task(session)
    with pytest.raises(ValidationFailed):
        archive.briefing(owner, "task-1", limit=10)


# --------------------------------------------------------------------------- #
# 复盘与知识沉淀
# --------------------------------------------------------------------------- #
def test_retrospective_produces_report_metrics_and_lessons(archive, owner, session):
    _mk_task(session, blocked_reason="上游接口没就绪")
    session.add(TaskEvent(id="e1", task_id="task-1", owner_id="owner-1", kind="status",
                          from_status="queued", to_status="waiting_input"))
    session.flush()
    out = archive.retrospective(owner, "task-1")
    assert "任务复盘" in out["report"]
    assert out["metrics"]["change_events"] == 1
    assert any(l["kind"] == "blocker" for l in out["lessons"])
    assert "上游接口没就绪" in " ".join(l["text"] for l in out["lessons"])


def test_retrospective_without_incidents_says_reuse_is_fine(archive, owner, session):
    _mk_task(session, status="completed", progress_percent=100)
    out = archive.retrospective(owner, "task-1")
    assert out["lessons"][0]["kind"] == "baseline"


def test_distill_writes_a_reusable_template(archive, owner, session, tmp_path):
    _mk_task(session)
    out = archive.distill(owner, "task-1", name="小红书文案 · 经验模板",
                          tags=["writing"])
    path = tmp_path / "dossier" / f"{out['knowledge_id']}.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "适用场景" in text and "经验条目" in text and "复用时怎么改" in text
    assert '"owner_id": "owner-1"' in text

    listing = archive.list_knowledge(owner)
    assert listing["total"] == 1
    assert listing["items"][0]["title"] == "小红书文案 · 经验模板"

    detail = archive.knowledge_detail(owner, out["knowledge_id"])
    assert detail["meta"]["source_task_id"] == "task-1"


def test_knowledge_is_owner_isolated(archive, owner, other, session):
    _mk_task(session)
    out = archive.distill(owner, "task-1", name="我的模板")
    assert archive.list_knowledge(other)["total"] == 0
    with pytest.raises(NotFound):
        archive.knowledge_detail(other, out["knowledge_id"])


def test_unknown_knowledge_is_404(archive, owner):
    with pytest.raises(NotFound):
        archive.knowledge_detail(owner, "nope")


# --------------------------------------------------------------------------- #
# 企业模式
# --------------------------------------------------------------------------- #
@pytest.fixture()
def enterprise(tmp_path) -> EnterpriseAdapter:
    return EnterpriseAdapter(directory=tmp_path / "dossier")


def test_catalog_publishes_mappings_and_reuses_existing_governance(enterprise, owner):
    cat = enterprise.catalog(owner)
    targets = {a["target"] for a in cat["adapters"]}
    assert {"generic", "spring-ai-chatclient", "langgraph", "openai-assistants"} <= targets
    gov = cat["governance"]
    # 复用声明必须点名既有服务，而不是「另起一套」
    assert "grant" in gov["permission_source"]
    assert "auth" in gov["identity_source"]
    assert gov["single_loop_kernel"] is True
    assert adapter_catalog()[0]["mappings"]


def test_adapt_maps_tools_model_and_loop_budget(enterprise, owner):
    out = enterprise.adapt(owner, "generic", EXAMPLE_EXTERNAL)
    spec = out["spec"]
    assert spec["agent"]["system_prompt"].startswith("你是合同审阅助手")
    assert spec["agent"]["tool_allowlist"] == ["knowledge.search", "artifact.write"]
    assert spec["model"] == {"provider_id": "openai", "model_name": "gpt-4o"}
    assert spec["loop"]["max_steps"] == 12
    assert out["unmapped"] == []
    assert out["executed"] is False


def test_adapt_keeps_unmapped_fields_instead_of_dropping_them(enterprise, owner):
    external = {"name": "x", "system_prompt": "s", "tools": [], "customRule": {"level": 3}}
    out = enterprise.adapt(owner, "generic", external)
    assert out["unmapped"] == ["customRule.level"]
    assert out["spec"]["extensions"]["unmapped"]["customRule"]["level"] == 3
    assert any("未丢弃" in w for w in out["warnings"])


def test_adapt_warns_when_the_external_definition_lacks_a_prompt(enterprise, owner):
    out = enterprise.adapt(owner, "generic", {"name": "x", "tools": ["a"]})
    assert any("系统提示词" in w for w in out["warnings"])
    assert out["spec"]["agent"]["id"] == "x"


def test_adapt_normalizes_nested_tool_shapes(enterprise, owner):
    external = {"name": "r", "system_prompt": "s",
                "tools": [{"type": "function", "function": {"name": "search"}},
                          {"name": "write"}, "plain"]}
    out = enterprise.adapt(owner, "openai-assistants", external)
    # type="function" 只是分类，不是工具名；工具名取 function.name
    assert out["spec"]["agent"]["tool_allowlist"] == ["search", "write", "plain"]


def test_adapt_rejects_unknown_target_and_bad_overrides(enterprise, owner):
    with pytest.raises(NotFound):
        enterprise.adapt(owner, "no-such-framework", {})
    with pytest.raises(ValidationFailed) as ei:
        enterprise.adapt(owner, "generic", EXAMPLE_EXTERNAL, overrides={"owner_id": "x"})
    assert ei.value.code == "override_unknown_key"


def test_register_mapping_extends_the_declarative_table(enterprise, owner):
    with pytest.raises(ValidationFailed) as ei:
        enterprise.register_mapping(owner, "generic",
                                    [{"from": "a", "to": "b", "via": "nope"}])
    assert ei.value.code == "mapping_unknown_transform"
    out = enterprise.register_mapping(owner, "generic",
                                     [{"from": "corp_id", "to": "agent.id", "via": "identity"}])
    assert out["registered"] == 1
    adapted = enterprise.adapt(owner, "generic", {"corp_id": "c-9", "system_prompt": "s",
                                                  "tools": []})
    assert adapted["spec"]["agent"]["id"] == "c-9"
    assert "generic" in enterprise.catalog(owner)["custom_mappings"]


def test_onboarding_doc_has_mapping_table_and_governance(enterprise, owner):
    doc = enterprise.onboarding_doc(owner, "spring-ai-chatclient")
    md = doc["markdown"]
    assert "企业接入文档" in md
    assert "| `client.systemPrompt` | `agent.system_prompt` |" in md
    assert "不提供第二套多租户" in md
    assert "```json" in md
