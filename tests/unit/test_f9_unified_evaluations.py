"""Tests and Matrix Execution for Phase F9: Unified Evaluation Matrix (Execution Manual F9, R06, R07, R08, O07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from find_yourself.runtime.evaluation import UnifiedEvaluator, EVAL_SAMPLES


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

    # Save artifact for finalization evidence
    evidence_dir = Path("evidence/finalization")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out_file = evidence_dir / "F9-unified-evaluations.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    assert out_file.exists()
