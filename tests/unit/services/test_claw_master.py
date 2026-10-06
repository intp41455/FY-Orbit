"""Claw 主控五件套测试（A-Claw主控-01~05）。"""

from __future__ import annotations

import pytest

from find_yourself.services.claw.master import (
    FallbackExhausted,
    alignment_checkpoint,
    check_granularity,
    classify_complexity,
    delivery_consistency_check,
    run_fallback_chain,
    select_model,
)


def test_granularity_flags_coarse_and_mechanical():
    issues = check_granularity(
        [{"title": "实现全部功能", "steps": 20},
         {"title": "x" * 150, "steps": 2}],
        goal="构建一个完整的alisas多智能体调度系统并且支持画布与代码双向同源与抗中断恢复",
    )
    kinds = {i.kind for i in issues}
    assert "too_coarse" in kinds and "too_long" in kinds
    clean = check_granularity([{"title": "a" * 10, "steps": 3} for _ in range(3)],
                              goal="短目标")
    assert clean == []


def test_alignment_checkpoint_pause_on_drift():
    goal = "实现画布双向同源与抗中断恢复能力"
    aligned = alignment_checkpoint(
        goal, ["实现画布双向同源的转换层", "抗中断恢复：检查点落盘+断点续跑"])
    assert aligned["should_pause"] is False
    drifted = alignment_checkpoint(goal, ["今天天气不错顺便写了点别的文档而已"])
    assert drifted["should_pause"] is True
    assert drifted["missing_keywords"]


def test_complexity_selects_model_tier():
    assert classify_complexity("请列出当前目录的文件并数一数行数") == "light"
    assert classify_complexity("对支付模块做安全审查与重构设计") == "complex"
    tiers = {"light": "mock-mini", "standard": "mock-std", "complex": "mock-max"}
    pick = select_model("架构设计评审", tiers=tiers)
    assert pick["tier"] == "complex" and pick["model"] == "mock-max"


def test_fallback_chain_switches_on_failure():
    def boom():
        raise RuntimeError("第一招不行")
    value, attempts = run_fallback_chain([
        ("找配置文件", boom),
        ("扫特征文件", lambda: "found-root"),
    ])
    assert value == "found-root"
    assert [a.strategy for a in attempts] == ["找配置文件", "扫特征文件"]
    assert attempts[0].ok is False and attempts[1].ok is True


def test_fallback_chain_exhausted_reports_all():
    with pytest.raises(FallbackExhausted) as ei:
        run_fallback_chain([("一", lambda: 1 / 0), ("二", lambda: 1 / 0)])
    assert "一" in str(ei.value) and "二" in str(ei.value)


def test_delivery_consistency_hard_requirements():
    bad = delivery_consistency_check(
        [{"name": "报告", "text": "正文", "claims": [{"key": "用户数", "value": 1200}]},
         {"name": "附录", "text": "附录",
          "claims": [{"key": "用户数", "value": 9999}]}],
        hard_requirements=("必须有 DDL",),
    )
    kinds = {v.kind for v in bad}
    assert "data_inconsistency" in kinds      # 口径漂移
    assert "missing_requirement" in kinds     # 硬性要求一条不漏

    good = delivery_consistency_check(
        [{"name": "报告", "text": "含 DDL 字样", "content_type": "text/markdown",
          "claims": [{"key": "用户数", "value": 1200}]},
         {"name": "附录", "text": "补", "content_type": "text/markdown",
          "claims": [{"key": "用户数", "value": 1200}]}],
        hard_requirements=("DDL",),
    )
    assert good == []
