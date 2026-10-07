"""P1 质量机制性能预算门禁（A-基座质保-13 / W3）单元测试。

需求四条验收：

① 五项时延上限写入**验收基线文件**（``performance_baseline.json``），CI 跑性能回归
   —— 断言基线文件里五项齐全、版本锁定、档位齐全。
② 超限即失败并报告**具体指标与阈值** —— 断言 ``ok=False`` + 失败项含指标/观测/阈值/超出量。
③ 性能预算可随硬件档位配置 —— 断言档位是基线的确定性派生（低配 ×2 / 高配 ×0.6）。
④ 提供本地性能自测脚本 —— 断言 ``python -m …self_test`` 的退出码与报告内容。

一条**不会漂移**的关键断言：本地自测与 CI 走同一个 ``evaluate``，阈值只有一个来源。
"""

from __future__ import annotations

import json

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.quality import budget as b
from find_yourself.services.quality import self_test as st

FIVE_OK = {
    "realtime_save_p95_ms": 38,
    "recover_gap_ms": 240,
    "conflict_compare_ms": 150,
    "log_filter_p95_ms": 90,
    "import_export_block_ms": 60,
}


# --------------------------------------------------------------------------- #
# ① 基线文件
# --------------------------------------------------------------------------- #
def test_baseline_file_pins_the_five_latency_budgets():
    data = b.load_baseline()
    assert data["version"] == "1.0.0"
    assert data["unit"] == "ms"
    assert set(data["metrics"]) == set(b.METRICS)
    assert len(b.METRICS) == 5
    for name in b.METRICS:
        spec = data["metrics"][name]
        assert isinstance(spec["limit"], (int, float)) and spec["limit"] > 0
        assert spec["label"] and spec["why"]      # 每项都要能解释「为什么有这个上限」
    # 「唯一真源」：基线文件就在服务包里，不存在第二份阈值文件
    assert b.BASELINE_FILE.is_file()
    assert b.BASELINE_FILE.name == "performance_baseline.json"


def test_baseline_failures_never_silently_fall_back(tmp_path):
    with pytest.raises(ValidationFailed) as e1:
        b.load_baseline(tmp_path / "nope.json")
    assert e1.value.code == "baseline_missing"

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationFailed) as e2:
        b.load_baseline(broken)
    assert e2.value.code == "baseline_invalid"

    missing_metric = tmp_path / "missing_metric.json"
    missing_metric.write_text(json.dumps({
        "metrics": {"realtime_save_p95_ms": {"limit": 50}},
        "tiers": {"standard": {"multiplier": 1.0}},
    }), encoding="utf-8")
    with pytest.raises(ValidationFailed) as e3:
        b.load_baseline(missing_metric)
    assert e3.value.code == "baseline_metric_missing"

    no_tiers = tmp_path / "no_tiers.json"
    no_tiers.write_text(json.dumps({
        "metrics": {m: {"limit": 1} for m in b.METRICS},
    }), encoding="utf-8")
    with pytest.raises(ValidationFailed) as e4:
        b.load_baseline(no_tiers)
    assert e4.value.code == "baseline_tiers_missing"


# --------------------------------------------------------------------------- #
# ③ 硬件档位
# --------------------------------------------------------------------------- #
def test_hardware_tiers_and_derived_limits():
    tiers = b.hardware_tiers()
    assert tiers["default_tier"] == "standard"
    assert [t["id"] for t in tiers["tiers"]] == ["low", "standard", "high"]

    std = b.effective_limits("standard")
    low = b.effective_limits("low")
    high = b.effective_limits("high")
    name = "realtime_save_p95_ms"
    assert std["limits"][name]["base_limit"] == 50
    assert std["limits"][name]["limit"] == 50
    # 档位是确定性派生，不是第二份配置
    assert low["limits"][name]["limit"] == 100
    assert high["limits"][name]["limit"] == 30
    assert low["multiplier"] == 2.0 and high["multiplier"] == 0.6
    assert std["baseline_version"] == "1.0.0"


def test_unknown_tier_is_rejected():
    with pytest.raises(ValidationFailed) as exc:
        b.effective_limits("quantum")
    assert exc.value.code == "tier_unknown"


# --------------------------------------------------------------------------- #
# ② 判定
# --------------------------------------------------------------------------- #
def test_all_five_within_budget_passes():
    result = b.evaluate(FIVE_OK)
    assert result["ok"] is True
    assert result["failures"] == []
    assert [c["metric"] for c in result["checks"]] == list(b.METRICS)
    assert all(c["ok"] for c in result["checks"])
    assert result["summary"] == "五项均在预算内"


def test_over_budget_reports_metric_observed_limit_and_overshoot():
    result = b.evaluate({**FIVE_OK, "log_filter_p95_ms": 400})
    assert result["ok"] is False
    assert len(result["failures"]) == 1
    fail = result["failures"][0]
    assert fail["metric"] == "log_filter_p95_ms"
    assert fail["observed"] == 400
    assert fail["limit"] == 150
    assert fail["over"] == 250
    assert "400" in fail["message"] and "150" in fail["message"]
    assert "log_filter_p95_ms" in result["summary"]


def test_missing_sample_fails_instead_of_being_skipped():
    partial = dict(FIVE_OK)
    partial.pop("recover_gap_ms")
    result = b.evaluate(partial)
    assert result["ok"] is False
    missing = next(c for c in result["checks"] if c["metric"] == "recover_gap_ms")
    assert missing["observed"] is None and missing["ok"] is False
    assert "缺少样本" in missing["message"]


def test_bad_samples_are_rejected_not_coerced():
    with pytest.raises(ValidationFailed) as e1:
        b.evaluate(["not", "a", "mapping"])  # type: ignore[arg-type]
    assert e1.value.code == "samples_invalid"
    with pytest.raises(ValidationFailed) as e2:
        b.evaluate({**FIVE_OK, "recover_gap_ms": "fast"})  # type: ignore[dict-item]
    assert e2.value.code == "sample_not_numeric"
    with pytest.raises(ValidationFailed) as e3:
        b.evaluate({**FIVE_OK, "recover_gap_ms": True})
    assert e3.value.code == "sample_not_numeric"


def test_budget_follows_the_hardware_tier():
    slow = {**FIVE_OK, "realtime_save_p95_ms": 80}
    assert b.evaluate(slow, "standard")["ok"] is False      # 标准档 50ms
    assert b.evaluate(slow, "low")["ok"] is True            # 低配档放宽到 100ms
    strict = {**FIVE_OK, "realtime_save_p95_ms": 40}
    assert b.evaluate(strict, "high")["ok"] is False        # 高配档收紧到 30ms


# --------------------------------------------------------------------------- #
# ④ 本地自测脚本
# --------------------------------------------------------------------------- #
def test_self_test_plan_tells_you_how_to_measure_each_metric():
    plan = b.self_test_plan("low")
    assert [s["metric"] for s in plan["steps"]] == list(b.METRICS)
    assert plan["unit"] == "ms"
    for step in plan["steps"]:
        assert step["how"].strip() and step["limit"] > 0
    assert "evaluate" in plan["submit"]


def test_self_test_script_exit_codes_match_ci(capsys):
    assert st.main(["--plan"]) == 0
    out = capsys.readouterr().out
    for name in b.METRICS:
        assert name in out

    assert st.main(["--plan", "--tier", "high"]) == 0
    assert "30" in capsys.readouterr().out

    # 无样本：只打印计划，不算失败
    assert st.main([]) == 0
    capsys.readouterr()


def test_self_test_script_returns_one_when_over_budget(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(FIVE_OK), encoding="utf-8")
    assert st.main(["--samples", str(good)]) == 0
    assert "五项均在预算内" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**FIVE_OK, "import_export_block_ms": 999}), encoding="utf-8")
    assert st.main(["--samples", str(bad)]) == 1
    report = capsys.readouterr().out
    assert "import_export_block_ms" in report
    assert "999" in report and "100" in report
    assert "超出" in report


def test_self_test_script_accepts_a_wrapped_samples_object(tmp_path, capsys):
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"tier": "low", "samples": FIVE_OK}), encoding="utf-8")
    assert st.main(["--samples", str(wrapped), "--tier", "low"]) == 0
    assert "五项均在预算内" in capsys.readouterr().out


def test_self_test_script_rejects_non_mapping_samples(tmp_path, capsys):
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert st.main(["--samples", str(junk)]) == 2
    assert "映射" in capsys.readouterr().err


def test_self_test_json_output_is_machine_readable(tmp_path, capsys):
    sample = tmp_path / "s.json"
    sample.write_text(json.dumps(FIVE_OK), encoding="utf-8")
    assert st.main(["--samples", str(sample), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["checks"]) == 5
