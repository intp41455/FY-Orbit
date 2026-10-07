"""模板市场、分层视图与安全审查导入导出测试（A-开箱模板-04 · A-开箱模板-06 · P12）。

覆盖要点：
1. 三层架构视图（新手默认 / 进阶可换 / 技术可拆 一级入口）与单一真源。
2. 模板切换影响预览（preview_switch_impact）与保存点联动。
3. 模板导出：单文件 .fytemplate，SHA-256 校验和与审计。
4. 安全审查门禁：防篡改、高危工具（exec_shell）拦截、敏感工具标记。
5. 安全导入：高危未裁剪阻断，用户权限/工具裁剪（confirmed_tools）后安全落地。
6. 模板市场与评分：打分、评语、贝叶斯排序（好用被顶上来）。
"""

import json
import pytest
from sqlalchemy import select

from find_yourself.db.models import AuditEvent
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.marketplace_rating import MarketplaceRatingStore
from find_yourself.services.templates.market import (
    CRITICAL_DANGEROUS_TOOLS,
    TEMPLATE_PACKAGE_MAGIC,
    TemplateMarketService,
)
from find_yourself.services.templates.scaffold import (
    ScaffoldTemplateService,
    user_templates_dir,
)


@pytest.fixture()
def rating_store(tmp_path):
    return MarketplaceRatingStore(str(tmp_path / "ratings.db"))


@pytest.fixture()
def scaffold_service(tmp_path, audit):
    user_dir = tmp_path / "user_tpl"
    return ScaffoldTemplateService(user_dir=user_dir, audit=audit)


@pytest.fixture()
def template_market(scaffold_service, audit, rating_store):
    return TemplateMarketService(scaffold_service, audit, ratings=rating_store)


# ---------------------------------------------------------------------------
# 1. 模板分层体系测试（A-开箱模板-04 🔒 GATE 上市门禁）
# ---------------------------------------------------------------------------

def test_hierarchy_exposes_three_layers_and_first_class_tech_portal(template_market, owner):
    h = template_market.get_hierarchy(owner)
    assert h["single_source"] is True
    layer_ids = [layer["id"] for layer in h["layers"]]
    assert layer_ids == ["novice_default", "advanced_swappable", "technical_removable"]

    # 新手默认层：开箱即用，复杂度隐藏
    novice = h["layers"][0]
    assert novice["items"]
    assert all(item["complexity_hidden"] is True for item in novice["items"])

    # 进阶可换层：按场景矩阵供给
    advanced = h["layers"][1]
    scenario_ids = [sc["id"] for sc in advanced["scenarios"]]
    assert set(scenario_ids) == {"writing", "research", "development", "data"}

    # 技术可拆层：陛下原话「一级可见技术入口」，可拆模板、改拓扑、改状态机、直接写代码
    tech = h["layers"][2]
    portal = tech["portal"]
    assert portal["first_class_entry"] is True
    assert "agent" in portal["supported_verbs"]
    assert any(f["id"] == "expand_dsl" for f in portal["features"])


def test_preview_switch_impact_details_changes_and_savepoint(template_market, owner):
    """切换模板时明确提示「哪些已做的内容会被影响」，并与保存点联动。"""
    impact = template_market.preview_switch_impact(
        owner, "writing-pipeline", "development-pipeline"
    )
    assert impact["safe_to_switch"] is True
    assert impact["snapshot_action"] == "auto_savepoint_before_switch"

    # writing -> development 涉及成员变动
    mem_changes = impact["member_changes"]
    assert "requirements" in mem_changes["added"]
    assert "drafter" in mem_changes["removed"]
    assert impact["warning"]


# ---------------------------------------------------------------------------
# 2. 模板导出与校验和（A-开箱模板-06①）
# ---------------------------------------------------------------------------

def test_export_package_format_and_checksum(template_market, owner, session):
    pkg = template_market.export_package(owner, "development-pipeline", author="Tester", notes="V1")
    assert pkg["magic"] == TEMPLATE_PACKAGE_MAGIC
    assert pkg["schema_version"] == "1.0.0"
    assert pkg["template_id"] == "development-pipeline"
    assert pkg["checksum"] and len(pkg["checksum"]) == 64
    assert pkg["template"]["members"]

    events = session.execute(select(AuditEvent)).scalars().all()
    exported_events = [e for e in events if e.action == "template.exported"]
    assert len(exported_events) == 1
    assert exported_events[0].target == "development-pipeline"


def test_export_package_file_writes_to_disk(template_market, owner, tmp_path):
    path = template_market.export_package_file(owner, "writing-pipeline", target_dir=tmp_path)
    assert path.exists()
    assert path.name == "writing-pipeline.fytemplate"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["magic"] == TEMPLATE_PACKAGE_MAGIC


# ---------------------------------------------------------------------------
# 3. 模板导入强制安全审查门禁（A-开箱模板-06②③④⑤）
# ---------------------------------------------------------------------------

def test_security_review_passes_clean_package(template_market, owner):
    pkg = template_market.export_package(owner, "research-pipeline")
    report = template_market.security_review(owner, pkg)
    assert report["passed"] is True
    assert report["risk_level"] == "low"
    assert report["checksum_verified"] is True
    assert report["can_import"] is True
    assert not report["dangerous_tools"]


def test_security_review_detects_checksum_tampering(template_market, owner):
    pkg = template_market.export_package(owner, "research-pipeline")
    # 恶意篡改模板内容
    pkg["template"]["name"] = "Tampered Name"
    report = template_market.security_review(owner, pkg)
    assert report["passed"] is False
    assert report["checksum_verified"] is False
    assert any(f["code"] == "checksum_corrupted" for f in report["findings"])


def test_security_review_detects_critical_dangerous_tools(template_market, owner):
    pkg = template_market.export_package(owner, "writing-pipeline")
    # 注入高危系统工具 exec_shell
    pkg["template"]["members"][0]["tool_allowlist"].append("exec_shell")
    # 重新计算 checksum 伪装防篡改
    from find_yourself.services.templates.market import _compute_checksum
    pkg["checksum"] = _compute_checksum(pkg["template"])

    report = template_market.security_review(owner, pkg)
    assert report["risk_level"] == "high"
    assert "exec_shell" in report["dangerous_tools"]
    assert report["can_import"] is False


def test_import_package_blocks_untrimmed_dangerous_tools(template_market, owner):
    """高危工具未裁剪时导入被强制拦截。"""
    pkg = template_market.export_package(owner, "writing-pipeline")
    pkg["template"]["members"][0]["tool_allowlist"].append("exec_shell")
    from find_yourself.services.templates.market import _compute_checksum
    pkg["checksum"] = _compute_checksum(pkg["template"])

    with pytest.raises(ValidationFailed) as ei:
        template_market.import_package(owner, pkg)
    assert ei.value.code == "security_gate_rejected"


def test_import_package_with_tool_trimming_succeeds(template_market, owner, session):
    """用户逐项确认/裁剪工具白名单（confirmed_tools），剔除高危项后安全落地。"""
    pkg = template_market.export_package(owner, "writing-pipeline")
    pkg["template"]["members"][0]["tool_allowlist"].extend(["exec_shell", "format_disk"])
    from find_yourself.services.templates.market import _compute_checksum
    pkg["checksum"] = _compute_checksum(pkg["template"])

    # 用户显式裁剪，只确认安全的基础工具
    confirmed = ["text_search", "summarize"]
    res = template_market.import_package(
        owner, pkg, confirmed_tools=confirmed, custom_name="Safe Writing System"
    )
    assert res["imported"] is True
    assert res["name"] == "Safe Writing System"

    # 验证落地的模板中高危工具已被完全剔除
    saved_doc = template_market.scaffold.get_template(owner, res["template_id"])
    member_tools = [
        t for m in saved_doc["overview"]["members"] for t in m.get("tool_allowlist", [])
    ]
    assert "exec_shell" not in member_tools
    assert "format_disk" not in member_tools

    # 验证审计日志
    events = session.execute(select(AuditEvent)).scalars().all()
    import_events = [e for e in events if e.action == "template.imported"]
    assert len(import_events) == 1


# ---------------------------------------------------------------------------
# 4. 模板市场与评分体系（A-开箱模板-06⑥ · A-工具市场-03）
# ---------------------------------------------------------------------------

def test_template_market_listing_and_ratings(template_market, owner, session):
    # 初始状态
    market_items = template_market.list_market(owner, sort_by="score")
    assert market_items["total"] >= 3

    # 打分：将 writing-pipeline 打为 5 星
    res = template_market.rate_template(owner, "writing-pipeline", 5.0, comment="写作神器！")
    assert res["rating"] == 5.0
    assert res["summary"]["score"] > 3.0

    # 查评价列表
    ratings_info = template_market.get_template_ratings(owner, "writing-pipeline")
    assert ratings_info["total"] == 1
    assert ratings_info["items"][0]["comment"] == "写作神器！"

    # 好用被顶上来：再次查询列表，writing-pipeline 排在首位
    ordered = template_market.list_market(owner, sort_by="score")
    assert ordered["items"][0]["template_id"] == "writing-pipeline"
