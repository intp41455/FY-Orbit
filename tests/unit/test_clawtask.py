"""P16 任务可移植服务层测试（A-任务可移植-03/04/05）。

三块各测其**不变量**：

* ``format``：自描述 / 跨模型（human_brief 必填）/ 可迁移（不泄漏本机绝对路径）/
  完整性（摘要不符即拒绝）/ 与 P13 模板契约对齐。
* ``market``：只收 ``task_template``、摘要校验、下架权限、**无支付**必须显式暴露。
* ``hibernate``：封存→唤醒往返、包被改动即拒绝（fail closed）、owner 隔离、
  临界策略（80% 预警 / 95% 强制建议）。
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.clawtask import format as F
from find_yourself.services.clawtask.hibernate import (
    FORCE_PERCENT,
    WARN_PERCENT,
    HibernationStore,
    hibernation_policy,
)
from find_yourself.services.clawtask.market import ClawTaskMarket
from find_yourself.services.errors import (
    DomainError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from find_yourself.services.templates.scaffold import (
    ESSENTIAL_KEYS,
    TEMPLATE_SCHEMA_VERSION,
)


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1")


@pytest.fixture()
def other() -> Actor:
    return Actor.owner("owner-2")


def _essentials() -> dict:
    return {k: {"value": f"default-{k}", "explain": "why"} for k in ESSENTIAL_KEYS}


def make_doc(**over) -> dict:
    doc = {
        "clawtask_version": F.CLAWTASK_VERSION,
        "kind": "task_template",
        "id": "tpl-1",
        "name": "小红书爆款文案生成",
        "goal": "输入产品，产出三条小红书文案",
        "human_brief": "这是给别的模型看的交接说明：输入产品名与卖点，产出三条文案。",
        # 固定时间戳：摘要必须**可复现**，随机时间会让 canonical 摘要在两次调用间漂移。
        "created_at": "2026-10-07T00:00:00+00:00",
        "target_models": ["gpt", "claude", "qwen", "deepseek"],
        "system_template": {
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "template_id": "writing-pipeline",
            "quality_tier": "novice",
            "essentials": _essentials(),
        },
        "context": {"format": "Markdown + YAML front-matter"},
        "steps": [{"id": "s1", "title": "选题", "owner": "topic", "status": "pending"}],
        "budget": {"max_model_calls": 12},
        "progress": {"status": "queued", "stage": "requirements", "percent": 0},
    }
    doc.update(over)
    return doc


# --------------------------------------------------------------------------- #
# 冻结契约
# --------------------------------------------------------------------------- #
def test_schema_pins_the_p13_template_contract():
    schema = F.clawtask_schema()
    assert schema["clawtask_version"] == "1.0.0"
    assert schema["extension"] == ".clawtask"
    assert schema["system_template_schema_version"] == TEMPLATE_SCHEMA_VERSION
    assert schema["human_brief_required"] is True
    assert schema["integrity_algorithm"] == "sha256"


def test_clean_document_needs_no_corrections():
    # 未封存时只有 integrity 一项是「预期缺失」；结构本身必须干净。
    assert F.validate_clawtask(make_doc(), check_integrity=False) == []


# --------------------------------------------------------------------------- #
# 完整性
# --------------------------------------------------------------------------- #
def test_digest_is_stable_and_seal_then_verify_passes():
    doc = make_doc()
    assert F.clawtask_digest(doc) == F.clawtask_digest(make_doc())
    sealed = F.seal(doc)
    assert F.verify_integrity(sealed) == []


def test_tampering_after_sealing_is_detected():
    sealed = F.seal(make_doc())
    sealed["goal"] = "偷偷改过的目标"
    problems = F.verify_integrity(sealed)
    assert problems and "integrity_mismatch" in problems[0]


def test_unsealed_document_is_reported_not_silently_accepted():
    problems = F.verify_integrity(make_doc())
    assert problems and "integrity_missing" in problems[0]


# --------------------------------------------------------------------------- #
# 自描述 / 跨模型 / 可迁移
# --------------------------------------------------------------------------- #
def test_human_brief_is_required_for_cross_model_handoff():
    doc = make_doc()
    doc.pop("human_brief")
    assert any("missing_required" in p and "human_brief" in p
               for p in F.validate_clawtask(doc, check_integrity=False))
    doc = make_doc(human_brief="太短")
    assert any("human_brief_too_short" in p
               for p in F.validate_clawtask(doc, check_integrity=False))


def test_system_template_must_match_the_p13_schema_version():
    doc = make_doc()
    doc["system_template"]["schema_version"] = "0.9.0"
    problems = F.validate_clawtask(doc, check_integrity=False)
    assert any("system_template_schema_mismatch" in p for p in problems)


def test_system_template_must_carry_all_eight_essentials():
    doc = make_doc()
    doc["system_template"]["essentials"] = {"budget": {"value": 1}}
    problems = F.validate_clawtask(doc, check_integrity=False)
    assert any("system_template_essentials_incomplete" in p for p in problems)


def test_absolute_local_paths_are_not_portable():
    doc = make_doc()
    doc["context"]["workspace"] = "/Users/dev/code/my-project"
    problems = F.validate_clawtask(doc, check_integrity=False)
    assert any("local_path_leaked" in p for p in problems)
    # Windows 盘符同样拦
    doc2 = make_doc()
    doc2["context"]["workspace"] = r"C:\Users\dev\code"
    assert any("local_path_leaked" in p
               for p in F.validate_clawtask(doc2, check_integrity=False))


def test_local_only_section_may_hold_machine_pointers():
    """``local_only`` 是**明确的例外**：它整体在可移植导出时被剥离。"""
    doc = make_doc(local_only={"artifacts_dir": "/Users/dev/fy/artifacts"})
    assert F.validate_clawtask(doc, check_integrity=False) == []


def test_portable_serialize_strips_local_only_but_keeps_it_locally():
    doc = make_doc(local_only={"artifacts_dir": "/Users/dev/fy/artifacts"})
    portable = F.serialize(doc, portable=True)
    assert "local_only" not in json.loads(portable["text"])
    local = F.serialize(doc, portable=False)
    assert json.loads(local["text"])["local_only"]["artifacts_dir"]


def test_serialize_refuses_an_invalid_document():
    doc = make_doc()
    doc.pop("goal")
    with pytest.raises(ValidationFailed) as ei:
        F.serialize(doc)
    assert ei.value.code == "clawtask_invalid"


def test_serialize_parse_roundtrip_is_lossless():
    doc = make_doc()
    exported = F.serialize(doc)
    back = F.parse(exported["text"])
    assert F.clawtask_digest(back) == exported["digest"]
    assert back["goal"] == doc["goal"]
    assert exported["filename"].endswith(F.CLAWTASK_EXT)


def test_parse_rejects_a_tampered_file():
    exported = F.serialize(make_doc())
    tampered = exported["text"].replace("三条小红书文案", "一百条小红书文案")
    with pytest.raises(ValidationFailed) as ei:
        F.parse(tampered)
    assert ei.value.code == "clawtask_invalid"


def test_progress_percent_out_of_range_is_rejected():
    doc = make_doc(progress={"status": "running", "stage": "doing", "percent": 130})
    assert any("progress_percent_out_of_range" in p
               for p in F.validate_clawtask(doc, check_integrity=False))


# --------------------------------------------------------------------------- #
# 从真实任务行构建
# --------------------------------------------------------------------------- #
def _fake_task(**over) -> types.SimpleNamespace:
    base = dict(
        id="task-1", owner_id="owner-1", goal="写一篇短文", domain="personal",
        mode="listen", strategy="auto", status="running", stage="doing",
        steps=3, max_steps=20, progress_percent=42, root_task_id="task-1",
        parent_task_id=None, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def test_from_task_carries_only_real_fields(owner):
    template = {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "template_id": "writing-pipeline",
        "quality_tier": "novice",
        "essentials": _essentials(),
        "members": [{"id": "topic", "role": "选题策划"}, {"id": "drafter", "role": "正文写作"}],
    }
    doc = F.from_task(owner, _fake_task(), system_template=template)
    assert doc["progress"]["percent"] == 42.0
    assert doc["progress"]["steps_done"] == 3
    assert doc["system_template"]["template_id"] == "writing-pipeline"
    assert [s["id"] for s in doc["steps"]] == ["topic", "drafter"]
    assert "写一篇短文" in doc["human_brief"]
    assert doc["artifacts"] == []            # 没产出就是空，不填料


def test_from_task_hides_another_owners_task(owner):
    with pytest.raises(NotFound):
        F.from_task(owner, _fake_task(owner_id="owner-2"))


def test_build_human_brief_marks_step_status():
    brief = F.build_human_brief(
        name="n", goal="g", status="running", stage="doing", percent=50,
        steps=[{"id": "a", "title": "选题", "owner": "topic", "status": "done"},
               {"id": "b", "title": "写作", "owner": "drafter"}],
        next_action="继续写正文",
    )
    assert "[x] 选题" in brief and "[ ] 写作" in brief
    assert "继续写正文" in brief


# --------------------------------------------------------------------------- #
# 任务市场（无账号 / 无支付）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def market(tmp_path) -> ClawTaskMarket:
    return ClawTaskMarket(directory=tmp_path / "market")


def test_market_rejects_task_instances(market, owner):
    with pytest.raises(ValidationFailed) as ei:
        market.publish(owner, make_doc(kind="task"))
    assert ei.value.code == "market_only_task_templates"


def test_publish_then_list_get_and_import(market, owner):
    entry = market.publish(owner, make_doc(), version="1.0.0")
    assert entry["item_id"].endswith("@1.0.0")
    # 交易缺口必须显式暴露（本轮不含账号与支付）
    assert entry["pricing"]["payments_supported"] is False

    listing = market.list_items(owner, query="小红书")
    assert listing["total"] == 1
    assert listing["payments_supported"] is False

    got = market.get_item(owner, entry["item_id"])
    assert got["digest"] == entry["digest"]

    imported = market.import_item(owner, entry["item_id"])
    assert imported["doc"]["name"] == "小红书爆款文案生成"
    assert imported["payments_supported"] is False
    assert "输入产品即可跑" in imported["note"]


def test_import_rejects_a_tampered_market_file(market, owner, tmp_path):
    entry = market.publish(owner, make_doc())
    path = tmp_path / "market" / entry["filename"]
    path.write_text(path.read_text(encoding="utf-8").replace("三条", "三百条"),
                    encoding="utf-8")
    with pytest.raises(ValidationFailed) as ei:
        market.import_item(owner, entry["item_id"])
    assert ei.value.code in ("clawtask_invalid", "market_entry_tampered")


def test_import_reports_a_missing_file_instead_of_inventing_it(market, owner, tmp_path):
    entry = market.publish(owner, make_doc())
    (tmp_path / "market" / entry["filename"]).unlink()
    with pytest.raises(NotFound) as ei:
        market.import_item(owner, entry["item_id"])
    assert ei.value.code == "market_entry_file_missing"


def test_only_the_publisher_can_unpublish(market, owner, other):
    entry = market.publish(owner, make_doc())
    with pytest.raises(PermissionDenied):
        market.unpublish(other, entry["item_id"])
    assert market.unpublish(owner, entry["item_id"])["removed"] is True
    with pytest.raises(NotFound):
        market.get_item(owner, entry["item_id"])


def test_publish_overwrites_same_name_and_version(market, owner):
    market.publish(owner, make_doc())
    market.publish(owner, make_doc(goal="改过的目标"))
    assert market.list_items(owner)["total"] == 1


def test_market_paging_bounds_are_enforced(market, owner):
    with pytest.raises(ValidationFailed):
        market.list_items(owner, limit=0)
    with pytest.raises(ValidationFailed):
        market.list_items(owner, limit=101)


# --------------------------------------------------------------------------- #
# 冬眠机制
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store(tmp_path) -> HibernationStore:
    return HibernationStore(directory=tmp_path / "hib")


def test_policy_thresholds_match_the_balance_breaker():
    assert WARN_PERCENT == 80.0 and FORCE_PERCENT == 95.0
    warn = hibernation_policy(budget_percent=85)
    assert warn["warn_only"] is True and warn["should_hibernate"] is False
    force = hibernation_policy(budget_percent=96)
    assert force["should_hibernate"] is True and "budget_force" in force["triggers"]
    assert hibernation_policy(model_failed=True)["should_hibernate"] is True
    assert hibernation_policy(user_requested=True)["should_hibernate"] is True
    assert hibernation_policy(budget_percent=10)["should_hibernate"] is False


def test_hibernate_then_wake_roundtrip_with_artifacts(store, owner, tmp_path):
    artifact = tmp_path / "draft.md"
    artifact.write_text("初稿正文", encoding="utf-8")
    out = store.hibernate(owner, make_doc(), reason="想换更聪明的模型",
                          artifacts=[str(artifact)], budget_percent=96)
    assert out["artifact_count"] == 1
    assert out["wake_hint"].endswith("/wake")

    listing = store.list_hibernations(owner)
    assert listing["total"] == 1
    assert listing["items"][0]["reason"] == "想换更聪明的模型"

    woken = store.wake(owner, out["hibernation_id"])
    assert woken["awakened"] is True
    assert woken["doc"]["name"] == "小红书爆款文案生成"
    assert woken["artifacts"][0]["restorable"] is True
    assert woken["artifacts"][0]["sha256"]


def test_wake_refuses_a_tampered_package(store, owner, tmp_path):
    artifact = tmp_path / "draft.md"
    artifact.write_text("原稿", encoding="utf-8")
    out = store.hibernate(owner, make_doc(), reason="暂停", artifacts=[str(artifact)])
    packed = tmp_path / "hib" / out["hibernation_id"] / "artifacts"
    (next(packed.iterdir())).write_text("被改过的稿", encoding="utf-8")
    with pytest.raises(DomainError) as ei:
        store.wake(owner, out["hibernation_id"])
    assert ei.value.code == "hibernation_integrity"


def test_hibernate_rejects_a_missing_artifact(store, owner):
    with pytest.raises(ValidationFailed) as ei:
        store.hibernate(owner, make_doc(), reason="x",
                        artifacts=["/definitely/not/here.md"])
    assert ei.value.code == "hibernate_artifact_missing"


def test_hibernation_is_owner_isolated(store, owner, other):
    out = store.hibernate(owner, make_doc(), reason="暂停")
    assert store.list_hibernations(other)["total"] == 0
    with pytest.raises(NotFound):
        store.wake(other, out["hibernation_id"])
    with pytest.raises(NotFound):
        store.inspect(other, out["hibernation_id"])


def test_discard_removes_the_package(store, owner, tmp_path):
    out = store.hibernate(owner, make_doc(), reason="暂停")
    assert store.discard(owner, out["hibernation_id"])["discarded"] is True
    assert store.list_hibernations(owner)["total"] == 0
    assert not (tmp_path / "hib" / out["hibernation_id"]).exists()


def test_hibernation_package_keeps_local_pointers(store, owner):
    """封存包是本机副本：保留 ``local_only`` 才能在原地续跑。"""
    doc = make_doc(local_only={"thread_id": "thread-9"})
    out = store.hibernate(owner, doc, reason="暂停")
    manifest = store.inspect(owner, out["hibernation_id"])
    assert manifest["local_only"]["thread_id"] == "thread-9"
