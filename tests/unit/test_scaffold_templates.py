"""P13 开箱模板服务层测试（A-开箱模板-02/03/05/07/08/09）。

覆盖的是**服务层契约**，不是 HTTP 层（那是 ``tests/api/test_templates_api.py``）：

* 冻结 schema 的形状（P12/P16 的消费契约）；
* 出厂模板内容包**全部**通过结构校验（内容包坏了必须在这里亮红灯）；
* 八类隐性必备条件都有默认值与说明（需求 -03①②）；
* ``instantiate`` 的「无空必填项 / 缺项明确指出 / 未知覆盖键 422」（需求 -03④）；
* 总控提示词体检：**否定感知**（禁行规则本身不得被误判）+ 真越权要报警（-02③）；
* 模板 ↔ 代码往返一致（需求 -05④）且管线只用受限动词集；
* 质量档位 / 三层供给是同一份数据的视图（-04⑥、-08）；
* 手册质量验收器（-09）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.templates import scaffold as S
from find_yourself.services.templates.scaffold import (
        CONTROLLER_FORBIDDEN_RULE,
        ESSENTIAL_KEYS,
        ScaffoldTemplateService,
        check_controller_prompt,
        check_manual_quality,
        parse_template_markdown,
        template_schema,
        validate_template,
    )

FACTORY_IDS = ("writing-pipeline", "research-pipeline", "development-pipeline")


@pytest.fixture()
def service(tmp_path) -> ScaffoldTemplateService:
    """出厂模板用**随包发布**的真内容；用户目录指到 tmp，测试不脏仓库。"""
    return ScaffoldTemplateService(user_dir=tmp_path / "user-templates")


@pytest.fixture()
def owner():
    from find_yourself.services.actor import Actor

    return Actor.owner("owner-1")


# --------------------------------------------------------------------------- #
# 冻结契约
# --------------------------------------------------------------------------- #
def test_schema_is_frozen_and_complete():
    schema = template_schema()
    assert schema["schema_version"] == S.TEMPLATE_SCHEMA_VERSION == "1.0.0"
    assert schema["entity"] == "system_scaffold_template"
    assert schema["min_members"] == 3
    assert schema["controller_id"] == "controller"
    assert set(schema["essential_keys"]) == set(ESSENTIAL_KEYS)
    assert len(schema["essential_keys"]) == 8          # 八类，不多不少
    assert len(schema["config_items"]) == 7            # 七项配置
    assert set(schema["quality_tiers"]) == {"novice", "strict"}
    assert "controller" in schema["required_top_level"]


# --------------------------------------------------------------------------- #
# 出厂内容包
# --------------------------------------------------------------------------- #
def test_every_factory_template_loads_and_validates_clean(service):
    loaded = service._templates()
    assert set(loaded) >= set(FACTORY_IDS)
    for tid, doc in loaded.items():
        assert validate_template(doc) == [], f"{tid} 出厂内容包结构不合格"


@pytest.mark.parametrize("tid", FACTORY_IDS)
def test_factory_template_has_all_seven_config_items(service, tid):
    doc = service._templates()[tid]
    assert S.missing_config_items(doc) == [], f"{tid} 七项配置不全"
    # 拓扑里必须能找到总控与全部成员（七项可交叉印证，不是摆设）
    members = {m["id"] for m in doc["members"]}
    assert doc["topology"]["controller"] == S.CONTROLLER_ID
    assert set(doc["topology"]["members"]) == members


@pytest.mark.parametrize("tid", FACTORY_IDS)
def test_factory_template_prefills_eight_essentials_with_explanations(service, tid):
    """需求 -03①②：八类必备项全部有默认值，且每项带说明。"""
    essentials = service._templates()[tid]["essentials"]
    assert set(essentials) == set(ESSENTIAL_KEYS)
    for key, item in essentials.items():
        assert item.get("value") not in (None, "", [], {}), f"{key} 出厂为空"
        assert str(item.get("explain") or "").strip(), f"{key} 缺 explain"


def test_controller_prompt_declares_the_forbidden_rule(service):
    """需求 -02①：总控提示词出厂必须明示禁行规则。"""
    for tid in FACTORY_IDS:
        prompt = service._templates()[tid]["controller"]["system_prompt"]
        assert CONTROLLER_FORBIDDEN_RULE in prompt
        assert service._templates()[tid]["controller"]["forbidden_rules"]


# --------------------------------------------------------------------------- #
# 内容包解析的失败面
# --------------------------------------------------------------------------- #
def test_markdown_without_fence_is_rejected():
    with pytest.raises(ValidationFailed) as ei:
        parse_template_markdown("# 只有散文，没有围栏块", source="x.md")
    assert ei.value.code == "template_block_missing"


def test_markdown_with_non_mapping_fence_is_rejected():
    with pytest.raises(ValidationFailed) as ei:
        parse_template_markdown("```yaml\n- a\n- b\n```")
    assert ei.value.code == "template_not_mapping"


def test_broken_content_pack_is_reported_with_every_problem():
    doc = parse_template_markdown("""```yaml
schema_version: "1.0.0"
template_id: broken
name: 坏模板
scenario: writing
layer: novice_default
quality_tier: novice
controller:
  id: not-the-controller
  system_prompt: ""
members: []
essential_keys: []
communication_protocol: {}
dispatch_rules: {}
acceptance: {}
essentials: {}
example_task: {}
```""")
    problems = validate_template(doc)
    joined = " ".join(problems)
    assert "controller_id_invalid" in joined
    assert "controller_prompt_missing" in joined
    assert "members_too_few" in joined
    assert "essential_missing" in joined
    assert "example_task_missing" in joined


# --------------------------------------------------------------------------- #
# 总控提示词体检（否定感知）
# --------------------------------------------------------------------------- #
def test_forbidden_rule_alone_is_not_flagged_as_scope_risk():
    """🔴 回归护栏：禁行规则是「不得直接执行具体任务」，朴素子串匹配会把它
    误判成越权（第一版就翻了这个车）。"""
    warnings = check_controller_prompt(
        f"你是总控，负责任务分配与调度跟进。你{CONTROLLER_FORBIDDEN_RULE}。"
    )
    assert warnings == []


def test_missing_forbidden_rule_and_duties_are_flagged():
    warnings = check_controller_prompt("你负责把活干完。")
    joined = " ".join(warnings)
    assert "controller_forbidden_rule_missing" in joined
    assert "controller_duties_missing" in joined


def test_actual_scope_violation_is_flagged():
    warnings = check_controller_prompt(
        f"你是总控。{CONTROLLER_FORBIDDEN_RULE}。你也可以你自己写代码，亲自完成实现。"
    )
    joined = " ".join(warnings)
    assert "controller_scope_risk" in joined


# --------------------------------------------------------------------------- #
# 实例化（需求 -02/-03）
# --------------------------------------------------------------------------- #
def test_instantiate_is_runnable_with_no_unfilled_required_fields(service, owner):
    out = service.instantiate(owner, "writing-pipeline")
    assert out["runnable"] is True
    assert out["unresolved"] == []
    assert out["warnings"] == []
    assert out["created_from"] == "factory"
    assert out["system"]["controller"]["system_prompt"]


def test_instantiate_overriding_an_essential_to_empty_is_reported_not_silent(service, owner):
    """需求 -03④：缺项要**明确指出**，不是静默失败。"""
    out = service.instantiate(owner, "writing-pipeline",
                              overrides={"termination": ""})
    assert out["runnable"] is False
    assert "termination" in out["unresolved"]
    assert "跑不通" in out["message"]


def test_instantiate_clearing_a_member_prompt_is_reported(service, owner):
    out = service.instantiate(owner, "writing-pipeline",
                              overrides={"member_prompts": {"drafter": ""}})
    assert "member_prompt:drafter" in out["unresolved"]
    assert out["runnable"] is False


def test_instantiate_rejects_unknown_override_keys(service, owner):
    with pytest.raises(ValidationFailed) as ei:
        service.instantiate(owner, "writing-pipeline",
                            overrides={"owner_id": "someone-else"})
    assert ei.value.code == "override_unknown_key"


def test_instantiate_detects_broken_controller_prompt_but_still_returns(service, owner):
    """需求 -02③：改坏提示词**不阻断**，但必须给明显警告。"""
    out = service.instantiate(owner, "writing-pipeline",
                              overrides={"controller_prompt": "你自己写代码吧"})
    assert out["runnable"] is True           # 不阻断
    assert any("controller_scope_risk" in w for w in out["warnings"])


def test_instantiate_audits_the_action(service, owner, session, audit):
    from sqlalchemy import select

    from find_yourself.db.models import AuditEvent

    service.audit = audit
    service.instantiate(owner, "research-pipeline")
    actions = [e.action for e in session.execute(select(AuditEvent)).scalars()]
    assert "template.instantiated" in actions


# --------------------------------------------------------------------------- #
# 恢复出厂
# --------------------------------------------------------------------------- #
def test_restore_factory_returns_factory_values(service, owner):
    factory_prompt = service._templates()["writing-pipeline"]["controller"]["system_prompt"]
    out = service.restore_factory(owner, "writing-pipeline")
    assert out["restored_from"] == "factory"
    assert out["controller_prompt"] == factory_prompt
    assert set(out["essentials"]) == set(ESSENTIAL_KEYS)


# --------------------------------------------------------------------------- #
# 档位与分层
# --------------------------------------------------------------------------- #
def test_quality_tiers_and_layers_are_views_of_one_dataset(service, owner):
    tiers = service.quality_tiers()
    assert [t["id"] for t in tiers["tiers"]] == ["novice", "strict"]
    assert tiers["default"] == "novice"
    layers = service.layers()
    assert [item["id"] for item in layers["layers"]] == list(S.LAYERS)
    assert layers["single_source"] is True
    # 同一模板在两个档位下都能列出（不维护第二份数据）
    novice = service.list_templates(owner, tier="novice")["items"]
    strict = service.list_templates(owner, tier="strict")["items"]
    assert {i["template_id"] for i in novice} == {i["template_id"] for i in strict}
    assert all(i["quality_tier"] == "strict" for i in strict)


def test_unknown_tier_or_scenario_is_rejected(service, owner):
    with pytest.raises(ValidationFailed):
        service.list_templates(owner, tier="ultra")
    with pytest.raises(ValidationFailed):
        service.list_templates(owner, scenario="nope")


# --------------------------------------------------------------------------- #
# 选中即懂 + 用量估算（需求 -07）
# --------------------------------------------------------------------------- #
def test_overview_shows_composition_and_labels_estimates(service, owner):
    doc = service.get_template(owner, "development-pipeline")
    ov = doc["overview"]
    assert ov["member_count"] == 4
    assert ov["controller"]["id"] == "controller"
    assert {m["id"] for m in ov["members"]} == {"requirements", "implementer", "tester", "reviewer"}
    est = ov["estimate"]
    assert est["estimated"] is True                  # 绝不冒充实测
    assert est["basis"] and est["cost_note"]
    assert (ov["artifact_rule"] or {}).get("pattern")


def test_essentials_view_exposes_value_explain_and_factory_value(service, owner):
    doc = service.get_template(owner, "writing-pipeline")
    view = {e["key"]: e for e in doc["essentials_view"]}
    assert set(view) == set(ESSENTIAL_KEYS)
    assert view["budget"]["value"]["max_model_calls"] == 24
    assert view["budget"]["explain"]
    assert view["budget"]["factory_value"] == view["budget"]["value"]
    assert view["budget"]["overridable"] is True


# --------------------------------------------------------------------------- #
# 模板 ↔ 代码同源（需求 -05）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tid", FACTORY_IDS)
def test_expand_to_code_is_a_lossless_roundtrip(service, owner, tid):
    exported = service.expand_to_code(owner, tid)
    assert exported["roundtrip_consistent"] is True
    template, dsl_doc = S.parse_template_export(exported["code"])
    assert template["template_id"] == tid
    assert [m["id"] for m in template["members"]] == [
        m["id"] for m in service._templates()[tid]["members"]
    ]
    assert dsl_doc["nodes"] and dsl_doc["edges"]


def test_pipeline_only_uses_the_restricted_verb_set(service, owner):
    exported = service.expand_to_code(owner, "writing-pipeline")
    assert "agent" in exported["verbs"]
    template, dsl_doc = S.parse_template_export(exported["code"])
    for node in dsl_doc["nodes"]:
        if node["type"] == "transform":
            assert node["verb"] in exported["verbs"]


def test_parse_export_without_spec_block_is_rejected():
    with pytest.raises(ValidationFailed) as ei:
        S.parse_template_export("# 只有注释，没有模板块\nx = 1\n")
    assert ei.value.code == "template_export_spec_missing"


def test_save_as_does_not_touch_the_factory_original(service, owner, tmp_path):
    """需求 -01⑤：另存为新模板不改动出厂原件。"""
    factory = service._templates()["writing-pipeline"]
    factory_prompt = factory["controller"]["system_prompt"]
    saved, path = service.save_as_template(owner, factory, name="我的写作系统")
    assert path.startswith(str(tmp_path / "user-templates"))
    assert saved["template_id"] == "我的写作系统"   # 中文名不被抹成英文
    assert service._templates()["writing-pipeline"]["controller"]["system_prompt"] == factory_prompt
    assert saved["name"] == "我的写作系统"
    assert saved["template_id"] in service._templates()


def test_import_code_round_trips_into_a_new_template(service, owner):
    exported = service.expand_to_code(owner, "writing-pipeline")
    out = service.import_code(owner, exported["code"], base_template_id="writing-pipeline",
                              name="我的写作流水线")
    assert out["source_template"] == "writing-pipeline"
    assert out["template"]["name"] == "我的写作流水线"
    assert out["template"]["template_id"] in service._templates()


# --------------------------------------------------------------------------- #
# 手册质量（需求 -09 / W8）
# --------------------------------------------------------------------------- #
def _compliant_manual() -> str:
    rows = "\n".join(f"| 故障{i} | 原因{i} | 恢复{i} | 预防{i} |" for i in range(1, 21))
    return (
        "# 使用手册\n\n## 故障目录\n\n| 现象 | 原因 | 恢复动作 | 预防 |\n|---|---|---|---|\n"
        + rows
        + "\n\n## 快速开始\n\n零基础用户 30 分钟内跑通第一个多 agent 系统。\n\n```bash\nfy run\n```\n"
    )


def test_manual_checker_passes_a_compliant_manual():
    report = check_manual_quality(_compliant_manual())
    assert report["ok"] is True
    assert report["fault_scenario_count"] >= 20
    assert report["zero_basis_path"] is True


def test_manual_checker_reports_specific_gaps():
    report = check_manual_quality("# 手册\n\n什么都没写。")
    assert report["ok"] is False
    joined = " ".join(report["gaps"])
    assert "fault_catalog_short" in joined
    assert "no_runnable_example" in joined
    assert "zero_basis_path_missing" in joined


def test_manual_report_without_content_or_file_is_not_found(service, owner):
    with pytest.raises(NotFound):
        service.manual_quality_report(owner, None)


# --------------------------------------------------------------------------- #
# 未认证 / 未知模板
# --------------------------------------------------------------------------- #
def test_unauthenticated_actor_is_rejected(service):
    from find_yourself.services.actor import Actor
    from find_yourself.services.errors import Unauthenticated

    anon = Actor(subject_type="anonymous")
    with pytest.raises(Unauthenticated):
        service.list_templates(anon)


def test_unknown_template_is_404(service, owner):
    with pytest.raises(NotFound):
        service.get_template(owner, "no-such-template")
