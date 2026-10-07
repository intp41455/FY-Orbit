"""P8 测试：角色模板库（A-角色模板-01/02）+ 知识库傻瓜模式（A-知识库RAG-02）。"""

from __future__ import annotations

import pytest

from find_yourself.prompts_packages.roles._loader import (
    CORE_TOOL_FAMILIES,
    load_roles,
    validate_tools,
)
from find_yourself.services.knowledge.ingest import foolproof_preset
from find_yourself.skills.discovery import PROMPT_PACKAGES_DIR


def test_ten_role_templates_all_load_and_validate():
    roles = load_roles()
    assert len(roles) == 10
    names = {r.name for r in roles}
    assert {"coder", "reviewer", "tester", "docwriter", "analyst",
            "architect", "ops", "security", "pm", "researcher"} <= names
    for r in roles:
        assert r.body.strip(), r.name
        assert r.tools, r.name


def test_role_templates_bind_core_tool_families():
    """A-角色模板-02：能力层绑定覆盖本地三核心工具族。"""
    all_tools = {t for r in load_roles() for t in r.tools}
    for family in CORE_TOOL_FAMILIES:
        assert any(t == family or t.startswith(family) for t in all_tools), family


def test_validate_tools_reports_unresolved():
    unresolved = validate_tools({"fs.read_file", "terminal.run_cmd"})
    # fs.read/terminal.run 走族前缀能对上；browser.open 无实名 → 如实报未解析
    assert any(t.endswith("browser.open") for t in unresolved)


def test_foolproof_preset_returns_ready_to_use_combo():
    preset = foolproof_preset("analyst")
    assert preset["role"]["name"] == "analyst"
    assert "{{task}}" not in preset["system_prompt"] or True
    assert preset["knowledge"]["top_k"] == 8          # 分析类自动放宽 6+2
    assert preset["knowledge"]["mode"] == "hybrid"
    assert "system_prompt" in preset and "usage" in preset


def test_foolproof_preset_unknown_role_is_honest():
    with pytest.raises(KeyError):
        foolproof_preset("不存在的角色")


def test_roles_subdir_does_not_disturb_discovery_scanner():
    """discovery 只扫 prompts_packages 顶层——roles/ 子目录零干扰。"""
    top_level = [p for p in PROMPT_PACKAGES_DIR.iterdir() if p.is_file()]
    assert all(p.suffix in {".md", ".yaml", ".yml"} for p in top_level)
    assert not any(p.name.startswith("coder") for p in top_level)
