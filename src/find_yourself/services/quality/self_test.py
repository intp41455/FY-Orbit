"""本地性能自测脚本（A-基座质保-13 / W3 验收 ④）。

为什么要一个**脚本**而不只是接口
--------------------------------

验收 ④ 要「开发者可提前自查」。查的前提是：本地判定与 CI 判定**不可能不一致**。
所以本脚本**不自己写一份阈值逻辑**，而是把样本交给
:func:`~find_yourself.services.quality.budget.evaluate`（CI 用的同一个函数、同一份
``performance_baseline.json``）——本地过了 CI 必过，反之亦然。

用法::

    # 1) 看五项指标各自怎么量（不用读代码）
    python -m find_yourself.services.quality.self_test --plan
    python -m find_yourself.services.quality.self_test --plan --tier low

    # 2) 把实测样本写成 JSON 后自查（超限时退出码 1，可直接挂进 CI / pre-commit）
    python -m find_yourself.services.quality.self_test --samples samples.json
    python -m find_yourself.services.quality.self_test --tier high --samples samples.json

``samples.json`` 形如（五项必须齐全；缺项判失败，不静默跳过）::

    {
      "realtime_save_p95_ms": 38,
      "recover_gap_ms": 240,
      "conflict_compare_ms": 150,
      "log_filter_p95_ms": 90,
      "import_export_block_ms": 60
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .budget import METRICS, evaluate, self_test_plan

#: 路由与报告里引用的**可复制命令**（保持与 ``main`` 的参数一致）。
SELF_TEST_COMMAND = (
    "python -m find_yourself.services.quality.self_test "
    "--samples samples.json --tier standard"
)


def _load_samples(source: str) -> dict[str, Any]:
    if source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def render_plan(tier: str) -> str:
    plan = self_test_plan(tier)
    lines = [f"本地性能自测计划 · 档位 {plan['tier']} · 单位 {plan['unit']}", ""]
    for step in plan["steps"]:
        lines.append(f"· {step['metric']}（{step['label']}）阈值 {step['limit']}{plan['unit']}")
        lines.append(f"    怎么量：{step['how']}")
    lines.append("")
    lines.append(f"提交：{plan['submit']}")
    return "\n".join(lines)


def render_report(result: dict[str, Any]) -> str:
    unit = result["unit"]
    lines = [f"性能预算判定 · 档位 {result['tier']}（{result['tier_label']}）· 基线 "
             f"{result['baseline_version']}", ""]
    for check in result["checks"]:
        mark = "OK  " if check["ok"] else "FAIL"
        observed = check["observed"]
        shown = "缺样本" if observed is None else f"{observed}{unit}"
        lines.append(f"[{mark}] {check['metric']:<26} 观测 {shown:<12} 阈值 {check['limit']}{unit}")
    lines.append("")
    lines.append(result["summary"])
    if not result["ok"]:
        lines.append("")
        lines.append("逐项失败明细（指标 / 观测 / 阈值 / 超出量）：")
        for fail in result["failures"]:
            over = "—" if fail["over"] is None else f"{fail['over']}{unit}"
            lines.append(f"  - {fail['metric']}：观测 {fail['observed']}，"
                         f"阈值 {fail['limit']}，超出 {over}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quality_self_test",
        description="质量机制性能预算本地自查（与 CI 判定完全同源）。",
    )
    parser.add_argument("--tier", default="standard",
                        help="硬件档位（low / standard / high；默认 standard）")
    parser.add_argument("--samples", default=None,
                        help="实测样本 JSON 路径，或 - 从 stdin 读")
    parser.add_argument("--plan", action="store_true", help="只打印五项指标的测法，不判定")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出判定结果")
    args = parser.parse_args(argv)

    if args.plan or not args.samples:
        print(render_plan(args.tier))
        if not args.samples and not args.plan:
            print("")
            print("未提供 --samples，只打印计划。")
        return 0

    raw = _load_samples(args.samples)
    samples = raw.get("samples", raw) if isinstance(raw, dict) else raw
    if not isinstance(samples, dict):
        print("样本必须是 {指标: 数值} 映射，或含 samples 键的对象。", file=sys.stderr)
        return 2

    result = evaluate(samples, args.tier)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(render_report(result))
    # 验收 ②：超限即失败（非零退出码），并已列出具体指标与阈值。
    return 0 if result["ok"] else 1


__all__ = ["SELF_TEST_COMMAND", "main", "render_plan", "render_report", "METRICS"]


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
