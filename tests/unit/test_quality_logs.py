"""P1 日志分级与可导出（A-基座质保-10）单元测试。

覆盖需求原文六条验收：

1. 五级分级齐全、级别可配置（``FY_QUALITY_LEVEL_RULES`` 覆盖 + 未知级别报错）
2. 按界面 / 项目 / 时间 / 级别 / actor 五维筛选
3. 一键导出排查包（日志 + 留痕 + 保存点清单），通用可读格式
4. 导出前自动脱敏凭据与密钥，导出内容可预览
5. 导出行为本身进留痕
6. 与既有审计链同源（不新建第二套日志）

另有一条**诚实性**断言：审计帧没有时间戳列，所以时间维是序号窗口，
响应必须显式声明 ``time_axis="seq"`` 且逐条 ``at=None``，不许编造墙钟时间。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from find_yourself.db.models import AuditEvent
from find_yourself.db.staging_models import WorkStash
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.quality import logs as mod
from find_yourself.services.quality.logs import (
    LEVELS,
    REDACTED,
    LogService,
    classify,
    level_rules,
    redact,
)


@pytest.fixture()
def svc(session, audit, owner, tmp_path):
    return LogService(session, audit=audit, directory=tmp_path / "quality")


def _seed_events(audit: AuditService, owner) -> None:
    """典型五级样本 + 一份带凭据的 details（用来验证导出脱敏）。"""
    audit.append(owner, "task.updated", "proj-alpha:t1",
                 {"surface": "workbench", "project": "proj-alpha", "field": "goal"})
    audit.append(owner, "task.save_failed", "proj-alpha:t1",
                 {"surface": "workbench", "project": "proj-alpha",
                  "error": "disk full", "api_key": "sk-should-never-leak"})
    audit.append(owner, "sync.retry_scheduled", "proj-beta:t2",
                 {"surface": "chat", "project": "proj-beta", "attempt": 2})
    audit.append(owner, "debug.probe", "proj-beta:t2",
                 {"surface": "chat", "project": "proj-beta"})
    audit.append(owner, "list.viewed", "proj-beta:t2",
                 {"surface": "chat", "project": "proj-beta",
                  "connection_string": "postgres://u:p@h/db"})


def _seed_savepoint(session, owner, *, kind="pre_write_snapshot", owner_id=None) -> WorkStash:
    row = WorkStash(
        id=f"stash-{kind}",
        owner_id=owner_id or owner.owner_id,
        title="写前快照",
        content="draft",
        stash_metadata={"kind": kind, "snapshot_id": "snap-1", "task_id": "t1", "files": 2},
    )
    session.add(row)
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# ① 五级分级、可配置
# --------------------------------------------------------------------------- #
def test_five_levels_are_complete_and_configurable(svc):
    body = svc.levels()
    assert [lv["id"] for lv in body["levels"]] == list(LEVELS)
    assert set(LEVELS) == {"error", "warning", "info", "debug", "change"}
    assert body["configurable"] is True
    assert body["config_env"] == "FY_QUALITY_LEVEL_RULES"
    # 同源声明：不是第二套日志系统
    assert "audit_events" in body["source"]
    assert body["time_axis"] == "seq"


def test_level_rules_can_be_overridden_by_json(tmp_path, monkeypatch):
    cfg = tmp_path / "rules.json"
    cfg.write_text(json.dumps({"error": ["boom"], "change": ["tweaked"]}), encoding="utf-8")
    monkeypatch.setenv("FY_QUALITY_LEVEL_RULES", str(cfg))
    rules = level_rules()
    assert rules["error"] == ("boom",)
    # 未覆盖的级别保持出厂规则
    assert "failed" in rules["warning"] or rules["warning"] == mod.DEFAULT_LEVEL_RULES["warning"]
    assert classify("something.boom", rules) == "error"
    assert classify("anything.else", rules) == "change"


def test_level_rules_reject_unknown_level_and_missing_file(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"fatal": ["x"]}), encoding="utf-8")
    monkeypatch.setenv("FY_QUALITY_LEVEL_RULES", str(bad))
    with pytest.raises(ValidationFailed) as exc:
        level_rules()
    assert exc.value.code == "level_unknown"

    monkeypatch.setenv("FY_QUALITY_LEVEL_RULES", str(tmp_path / "nope.json"))
    with pytest.raises(ValidationFailed) as exc2:
        level_rules()
    assert exc2.value.code == "level_rules_missing"


def test_classify_maps_actions_to_the_five_levels():
    assert classify("claw.integrity_failed") == "error"
    assert classify("sync.retry_scheduled") == "warning"
    assert classify("debug.probe") == "debug"
    assert classify("list.viewed") == "info"
    assert classify("task.updated") == "change"


# --------------------------------------------------------------------------- #
# ④ 脱敏
# --------------------------------------------------------------------------- #
def test_redact_removes_credential_keys_and_values():
    payload = {
        "keep": "ok",
        "password": "p@ss",
        "nested": {"api_key": "abc", "note": "postgres://u:p@h/db"},
        "list": [{"csrf": "t"}, "bearer to-ken", "plain"],
    }
    cleaned, hits = redact(payload)
    assert cleaned["keep"] == "ok"
    assert cleaned["password"] == REDACTED
    assert cleaned["nested"]["api_key"] == REDACTED
    assert cleaned["nested"]["note"] == REDACTED
    assert cleaned["list"][0]["csrf"] == REDACTED
    assert cleaned["list"][1] == REDACTED
    assert cleaned["list"][2] == "plain"
    assert hits >= 4


def test_redact_reports_no_false_positive_on_plain_values():
    cleaned, hits = redact({"goal": "写三条文案", "count": 3, "flag": True})
    assert cleaned == {"goal": "写三条文案", "count": 3, "flag": True}
    assert hits == 0


# --------------------------------------------------------------------------- #
# ② 五维筛选
# --------------------------------------------------------------------------- #
def test_query_filters_by_all_five_dimensions(svc, audit, owner, session):
    _seed_events(audit, owner)
    session.commit()

    assert svc.query(owner)["total"] == 5
    assert svc.query(owner, level="error")["total"] == 1
    assert svc.query(owner, level="warning")["total"] == 1
    assert svc.query(owner, surface="workbench")["total"] == 2
    assert svc.query(owner, surface="chat")["total"] == 3
    assert svc.query(owner, project="proj-alpha")["total"] == 2
    assert svc.query(owner, actor_filter="owner-1")["total"] == 5
    assert svc.query(owner, actor_filter="someone-else")["total"] == 0

    all_items = svc.query(owner)["items"]
    seqs = [i["seq"] for i in all_items]
    assert seqs == sorted(seqs)
    window = svc.query(owner, since_seq=seqs[1], until_seq=seqs[2])
    assert [i["seq"] for i in window["items"]] == seqs[1:3]

    # 时间维是序号轴：显式声明 + 逐条不编造墙钟
    assert all_items[0]["at"] is None
    assert svc.query(owner)["time_axis"] == "seq"


def test_query_rejects_unknown_level_and_bad_paging(svc, owner):
    with pytest.raises(ValidationFailed) as e1:
        svc.query(owner, level="fatal")
    assert e1.value.code == "level_unknown"
    with pytest.raises(ValidationFailed) as e2:
        svc.query(owner, limit=0)
    assert e2.value.code == "page_limit_invalid"
    with pytest.raises(ValidationFailed) as e3:
        svc.query(owner, limit=mod.MAX_EXPORT_ROWS + 1)
    assert e3.value.code == "page_limit_exceeded"
    with pytest.raises(ValidationFailed) as e4:
        svc.query(owner, offset=-1)
    assert e4.value.code == "offset_invalid"


def test_query_redacts_by_default_and_can_be_turned_off(svc, audit, owner, session):
    _seed_events(audit, owner)
    session.commit()
    err = svc.query(owner, level="error")["items"][0]
    assert err["details"]["api_key"] == REDACTED
    assert svc.query(owner, level="error")["redacted_count"] >= 1
    raw = svc.query(owner, level="error", redact_output=False)["items"][0]
    assert raw["details"]["api_key"] == "sk-should-never-leak"


def test_query_is_owner_isolated(svc, audit, session):
    audit.append(Actor.owner("owner-2"), "task.updated", "x", {"surface": "secret"})
    session.commit()
    assert svc.query(Actor.owner("owner-1"))["total"] == 0


# --------------------------------------------------------------------------- #
# ③ 保存点清单
# --------------------------------------------------------------------------- #
def test_savepoints_only_list_pre_write_snapshots(svc, session, owner):
    _seed_savepoint(session, owner, kind="pre_write_snapshot")
    _seed_savepoint(session, owner, kind="manual_note")
    session.commit()
    items = svc.savepoints(owner)
    assert len(items) == 1
    assert items[0]["snapshot_id"] == "snap-1"
    assert items[0]["created_at"] is not None      # 保存点带真实时间戳
    assert items[0]["file_count"] == 2


# --------------------------------------------------------------------------- #
# ③④⑤ 一键导出
# --------------------------------------------------------------------------- #
def test_export_writes_redacted_readable_package_and_leaves_a_trace(svc, audit, owner, session):
    _seed_events(audit, owner)
    _seed_savepoint(session, owner)
    session.commit()

    result = svc.export_package(owner, level="error")
    assert result["contains_credentials"] is False
    assert result["log_count"] == 1
    assert result["savepoint_count"] == 1
    assert result["redactions"] >= 1
    assert set(result["files"]) == {"logs.json", "savepoints.json", "manifest.json"}
    # ④ 可预览
    assert result["preview"]["manifest"]["export_id"] == result["export_id"]
    assert len(result["preview"]["sample"]) == 1

    from pathlib import Path

    root = Path(result["directory"])
    logs = json.loads((root / "logs.json").read_text(encoding="utf-8"))
    assert logs["count"] == 1
    assert logs["entries"][0]["details"]["api_key"] == REDACTED
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contains_credentials"] is False
    assert manifest["redaction_policy"]["replacement"] == REDACTED
    # 真正的秘密值不得出现在导出包的任何文件里（策略清单本身当然要写出来）
    for name in ("manifest.json", "logs.json", "savepoints.json"):
        body = (root / name).read_text(encoding="utf-8")
        assert "sk-should-never-leak" not in body
        assert "postgres://u:p@h/db" not in body

    # ⑤ 导出行为进留痕（挂到既有哈希链上）
    frames = list(session.execute(
        select(AuditEvent).where(AuditEvent.action == "quality.export_created")
    ).scalars())
    assert len(frames) == 1
    assert frames[0].target == result["export_id"]


def test_export_listing_and_detail_are_owner_isolated(svc, audit, owner, session):
    _seed_events(audit, owner)
    session.commit()
    made = svc.export_package(owner)
    assert [m["export_id"] for m in svc.list_exports(owner)["items"]] == [made["export_id"]]
    detail = svc.export_detail(owner, made["export_id"])
    assert detail["manifest"]["export_id"] == made["export_id"]
    assert detail["savepoints"]["count"] == 0

    stranger = Actor.owner("owner-2")
    assert svc.list_exports(stranger)["total"] == 0
    with pytest.raises(NotFound) as exc:
        svc.export_detail(stranger, made["export_id"])
    assert exc.value.code == "export_not_found"


def test_export_without_filters_covers_every_level(svc, audit, owner, session):
    _seed_events(audit, owner)
    session.commit()
    result = svc.export_package(owner, preview=False)
    assert result["log_count"] == 5
    assert "preview" not in result
    # 导出包里的五级都要出现（分级视图不是只导错误）
    from pathlib import Path

    logs = json.loads((Path(result["directory"]) / "logs.json").read_text(encoding="utf-8"))
    assert {e["level"] for e in logs["entries"]} == {"error", "warning", "info", "debug", "change"}
