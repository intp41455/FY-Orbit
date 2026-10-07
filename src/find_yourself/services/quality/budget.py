"""质量机制性能预算门禁（A-基座质保-13 / W3）。

需求验收逐条落地：

| 验收 | 落点 |
|---|---|
| ① 五项时延上限写入验收基线文件，CI 跑性能回归 | ``performance_baseline.json``（唯一真源）+ 本模块 + 路由 |
| ② 超限即失败并报告具体指标与阈值 | :func:`evaluate` 返回逐项 ``observed/limit/over`` |
| ③ 性能预算可随硬件档位配置 | ``tiers``（low/standard/high）= 基线 × 倍率 |
| ④ 提供本地性能自测脚本，开发者可提前自查 | :func:`self_test_plan` + routes 的 evaluate 接口 |

**为什么阈值只有一份**：两处各写一份阈值，迟早漂移成「CI 说过了、本地说没过」。
所以基线文件是唯一真源，档位是它的**确定性派生**（乘倍率），不是第二份配置。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import ValidationFailed

BASELINE_FILE = Path(__file__).with_name("performance_baseline.json")

#: 五项时延指标名（与基线文件 ``metrics`` 的键一致；顺序即报告顺序）。
METRICS: tuple[str, ...] = (
    "realtime_save_p95_ms",
    "recover_gap_ms",
    "conflict_compare_ms",
    "log_filter_p95_ms",
    "import_export_block_ms",
)


def load_baseline(path: str | Path | None = None) -> dict[str, Any]:
    """读验收基线（缺文件 / 坏 JSON 都明确报错，绝不回落成内置默认值掩盖问题）。"""
    p = Path(path) if path else BASELINE_FILE
    if not p.is_file():
        raise ValidationFailed("baseline_missing", f"性能基线文件不存在：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValidationFailed("baseline_invalid", f"性能基线不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise ValidationFailed("baseline_invalid", "性能基线必须含 metrics 映射")
    missing = [m for m in METRICS if m not in data["metrics"]]
    if missing:
        raise ValidationFailed("baseline_metric_missing",
                              f"性能基线缺指标 {missing}（五项必须齐全）")
    if not isinstance(data.get("tiers"), dict) or not data["tiers"]:
        raise ValidationFailed("baseline_tiers_missing", "性能基线必须声明硬件档位")
    return data


def hardware_tiers(path: str | Path | None = None) -> dict[str, Any]:
    data = load_baseline(path)
    return {
        "default_tier": data.get("default_tier", "standard"),
        "tiers": [
            {"id": key, "label": tier.get("label", key),
             "multiplier": float(tier.get("multiplier", 1.0))}
            for key, tier in data["tiers"].items()
        ],
        "unit": data.get("unit", "ms"),
    }


def effective_limits(tier: str = "standard", *, path: str | Path | None = None) -> dict[str, Any]:
    """某硬件档位下的**有效**阈值 = 基线 × 档位倍率。"""
    data = load_baseline(path)
    tiers = data["tiers"]
    if tier not in tiers:
        raise ValidationFailed("tier_unknown",
                              f"未知硬件档位 {tier!r}；可选 {sorted(tiers)}")
    multiplier = float(tiers[tier].get("multiplier", 1.0))
    limits: dict[str, Any] = {}
    for name in METRICS:
        spec = data["metrics"][name]
        base = float(spec["limit"])
        limits[name] = {
            "label": spec.get("label", name),
            "why": spec.get("why", ""),
            "base_limit": base,
            "limit": round(base * multiplier, 3),
        }
    return {
        "tier": tier,
        "tier_label": tiers[tier].get("label", tier),
        "multiplier": multiplier,
        "unit": data.get("unit", "ms"),
        "baseline_version": data.get("version"),
        "limits": limits,
    }


def evaluate(samples: dict[str, float], tier: str = "standard", *,
             path: str | Path | None = None) -> dict[str, Any]:
    """按档位阈值逐项判定；**超限即失败**并报告指标、观测值、阈值与超出量。"""
    if not isinstance(samples, dict):
        raise ValidationFailed("samples_invalid", "samples 必须是 {指标: 观测值} 映射")
    spec = effective_limits(tier, path=path)
    checks: list[dict[str, Any]] = []
    for name in METRICS:
        limit = spec["limits"][name]["limit"]
        observed = samples.get(name)
        if observed is None:
            checks.append({
                "metric": name, "label": spec["limits"][name]["label"],
                "observed": None, "limit": limit, "unit": spec["unit"],
                "ok": False, "over": None,
                "message": f"缺少样本：{name} 必须实测（不静默跳过）",
            })
            continue
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            raise ValidationFailed("sample_not_numeric", f"{name} 的观测值必须是数字")
        over = round(float(observed) - limit, 3)
        ok = over <= 0
        checks.append({
            "metric": name, "label": spec["limits"][name]["label"],
            "observed": float(observed), "limit": limit, "unit": spec["unit"],
            "ok": ok, "over": over,
            "message": ("在预算内" if ok
                        else f"超预算 {over}{spec['unit']}：{name} 观测 {observed} > 阈值 {limit}"),
        })
    failures = [c for c in checks if not c["ok"]]
    return {
        "ok": not failures,
        "tier": spec["tier"],
        "tier_label": spec["tier_label"],
        "unit": spec["unit"],
        "checks": checks,
        "failures": failures,
        "summary": ("五项均在预算内" if not failures
                    else f"{len(failures)}/{len(METRICS)} 项超预算：" +
                         "；".join(c["message"] for c in failures)),
        "baseline_version": spec["baseline_version"],
    }


def self_test_plan(tier: str = "standard", *, path: str | Path | None = None) -> dict[str, Any]:
    """本地性能自测脚本的**可执行计划**（开发者提前自查；与 CI 同一套阈值）。"""
    spec = effective_limits(tier, path=path)
    return {
        "tier": spec["tier"],
        "unit": spec["unit"],
        "steps": [
            {"metric": name, "label": spec["limits"][name]["label"],
             "limit": spec["limits"][name]["limit"],
             "how": _HOW[name]}
            for name in METRICS
        ],
        "submit": "把实测 {指标: 数值} POST 到 /api/quality/performance-budget/evaluate，"
                  "与 CI 判定完全一致（同一份阈值）。",
    }


#: 每项指标的本地测法（照做即可，不需要读代码）。
_HOW: dict[str, str] = {
    "realtime_save_p95_ms": "在编辑器里连续输入 200 次，量「按键到落盘完成」的 p95（浏览器 Performance 面板）。",
    "recover_gap_ms": "强杀进程后重开，量「启动到界面可编辑」的毫秒数（≥3 次取中位）。",
    "conflict_compare_ms": "造一次本地/远端冲突，量「点比对到差异列表出现」。",
    "log_filter_p95_ms": "对 1 万条日志用五维筛选各跑 20 次，取 p95。",
    "import_export_block_ms": "导出排查包时量主线程最长阻塞（Long Tasks API）。",
}


__all__ = [
    "BASELINE_FILE", "METRICS", "load_baseline", "hardware_tiers",
    "effective_limits", "evaluate", "self_test_plan",
]
