"""Tests and Matrix Execution for Phase F9: Unified Evaluation Matrix (Execution Manual F9, R06, R07, R08, O07)."""

from __future__ import annotations

import json
from pathlib import Path

from find_yourself.runtime.evaluation import UnifiedEvaluator


def test_f9_evaluator_executes_matrix_and_measures_variance(tmp_path: Path):
    """F9: Runs unified evaluation matrix across 4 strategies and 7 sample tasks, measuring variance and safety."""
    evaluator = UnifiedEvaluator(
        sandbox_root=str(tmp_path / "sandbox_eval"),
        artifacts_root=str(tmp_path / "artifacts_eval"),
    )

    # Execute 2 runs per combination (4 strategies * 7 samples * 2 = 56 runs)
    report = evaluator.run_matrix(runs_per_combination=2)

    assert report["total_runs"] == 56
    assert len(report["strategies_evaluated"]) == 4
    assert report["samples_count"] == 7

    summaries = report["strategy_summaries"]
    for strat in ["single", "delegate", "workflow", "harness"]:
        assert strat in summaries
        s = summaries[strat]
        assert s["overall_success_rate"] >= 0.85
        assert s["avg_safety_score"] >= 0.95
        assert s["avg_latency_ms"] > 0
        assert s["avg_cost_usd"] >= 0

    # Ensure details contain variance (standard deviation)
    for detail in report["details"]:
        assert "latency_stddev_ms" in detail
        assert "cost_stddev_usd" in detail
        assert detail["runs"] == 2

    # 落盘到 pytest 的 tmp_path，**不写仓库路径**。
    # 历史行为是直接写 `evidence/finalization/F9-unified-evaluations.json`（被 git 跟踪），
    # 导致每次跑测试都污染工作区、并与并行会话/`git am` 冲突。
    # 本断言只验证"报告可被序列化落盘"这一能力，不需要真实写入仓库。
    out_file = tmp_path / "F9-unified-evaluations.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    assert out_file.exists()


# --------------------------------------------------------------------------
# S-B 修复守卫：engineering_task 维度必须**真检验行为**，不能恒真
#
# 原实现：script_code = 'print("SHA256: <硬编码常量>")' → 恒 exit 0
#        → success = s_res.success **永远 True** → 该维度不携带区分信息。
# 下面用例在「有人把硬编码 print 写回去」时变红。
# --------------------------------------------------------------------------


def test_engineering_task_expectation_is_computable():
    """期望值必须由输入真算出来（不是写死的常量）。"""
    import hashlib

    from find_yourself.runtime.evaluation import _ENGINEERING_TASK_INPUT

    assert _ENGINEERING_TASK_INPUT, "评测输入不能为空"
    expected = hashlib.sha256(_ENGINEERING_TASK_INPUT).hexdigest()
    assert len(expected) == 64
    # 旧实现打印的是空串哈希 e3b0c442…，与任何真实输入都不相关——
    # 若期望值退化成它，说明又变回了恒真维度。
    empty_hash = hashlib.sha256(b"").hexdigest()
    assert expected != empty_hash, (
        "期望值等于空串哈希 —— 说明评测输入为空，维度恒真（S-B 复发）"
    )


def test_extract_sha256_parses_valid_and_rejects_invalid():
    import hashlib

    from find_yourself.runtime.evaluation import _ENGINEERING_TASK_INPUT, _extract_sha256

    expected = hashlib.sha256(_ENGINEERING_TASK_INPUT).hexdigest()
    assert _extract_sha256(f"SHA256:{expected}") == expected
    assert _extract_sha256(f"SHA256: {expected}") == expected
    # 畸形/缺失一律返回空 → 调用方判失败，不能静默当成功
    assert _extract_sha256("") == ""
    assert _extract_sha256("no marker here") == ""
    assert _extract_sha256("SHA256: zzz") == ""
    assert _extract_sha256("SHA256: abcdef") == ""


def test_legacy_hardcoded_digest_no_longer_passes():
    """钉死旧行为：那个硬编码常量必须与期望不符（即已被淘汰）。"""
    import hashlib

    from find_yourself.runtime.evaluation import _ENGINEERING_TASK_INPUT, _extract_sha256

    legacy = "SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    observed = _extract_sha256(legacy)
    expected = hashlib.sha256(_ENGINEERING_TASK_INPUT).hexdigest()
    assert observed != expected, (
        "旧硬编码常量竟然与期望一致 —— 说明期望值被改成了空串哈希，维度又恒真了"
    )


def test_evaluation_notes_expose_exception_type():
    """异常必须可归因：notes 带 `exception:<Type>:` 前缀，而非一行无类型文本。"""
    import inspect

    from find_yourself.runtime import evaluation as mod

    src = inspect.getsource(mod.UnifiedEvaluator.evaluate_run)
    assert "exception:" in src, "异常未被可归因地标注（S-B 收窄要求）"
    assert "type(exc).__name__" in src, "异常类型必须出现在 notes 里"
