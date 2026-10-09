"""A-Claw主控-01~05 · 主控岗五件套（让推进"像真人项目经理"）。

* -01 拆解颗粒度校验：大任务拆"大节点+小块"，子任务太粗/太机械自动提示合并
* -02 目标对齐检查点：每完成 3–5 个子任务，把产出与最初需求比对，偏差超阈值暂停
* -03 按复杂度动态选模型：简单活用便宜小模型，复杂活才用贵大模型（成本=核心体验）
* -04 节点备选方案自动切换：每节点预留备选方案，"这招不行换下一招"
* -05 收口校验防口径漂移：交付前统一格式/对齐口径/核对硬性要求一条不漏

全部为纯函数/轻状态服务——可独立测试，供主控编排层与内核 Hook 消费。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

# ---------------------------------------------------------------------------
# -01 拆解颗粒度校验
# ---------------------------------------------------------------------------

@dataclass
class GranularityIssue:
    subtask: str
    kind: str       # too_coarse / too_mechanical / too_long
    message: str


def check_granularity(
    plan: list[dict[str, Any]],
    *,
    goal: str = "",
    max_steps_per_subtask: int = 8,
    min_subtasks: int = 2,
    max_title_len: int = 120,
) -> list[GranularityIssue]:
    """校验拆解颗粒度。规则：单子任务步数超上限=太粗要再拆；大目标子任务
    数不足=拆得太粗；标题超长=机械搬运非人话拆解。"""
    issues: list[GranularityIssue] = []
    for st in plan or []:
        title = str(st.get("title") or st.get("name") or "")
        steps = int(st.get("steps") or st.get("step_count") or 0)
        if steps > max_steps_per_subtask:
            issues.append(GranularityIssue(
                title, "too_coarse",
                f"子任务 {title[:40]!r} 含 {steps} 步（>{max_steps_per_subtask}），应再拆成大节点+小块"))
        if len(title) > max_title_len:
            issues.append(GranularityIssue(
                title, "too_long",
                f"子任务标题 {len(title)} 字符，疑似机械搬运而非拆解"))
    if len(plan or []) < min_subtasks and len(goal) >= 60:
        issues.append(GranularityIssue(
            "(整体)", "too_coarse",
            f"长目标（{len(goal)} 字）仅拆出 {len(plan or [])} 个子任务，颗粒度过粗"))
    return issues


# ---------------------------------------------------------------------------
# -02 目标对齐检查点
# ---------------------------------------------------------------------------

_STOPWORDS = {"的", "了", "和", "与", "并", "或", "在", "对", "把", "是", "要", "能",
              "the", "and", "for", "with", "that", "this"}


def _keywords(text: str) -> set[str]:
    """提取关键词：goal 先按连接词切词段（消跨界碎 gram），段内取 2-gram；
    英文按 3+ 字母词；去停用词。"""
    words: set[str] = set()
    for w in re.findall(r"[A-Za-z]{3,}", text or ""):
        if w.lower() not in _STOPWORDS:
            words.add(w.lower())
    for seg in re.split(r"[，,。；;、\s（）()]|与|和|或|的|了|并|及", text or ""):
        seg = seg.strip()
        if len(seg) < 2:
            continue
        for i in range(len(seg) - 1):
            gram = seg[i:i + 2]
            if gram not in _STOPWORDS:
                words.add(gram)
    return words


def alignment_checkpoint(
    goal: str,
    outputs: list[str | dict[str, Any]],
    *,
    coverage_threshold: float = 0.5,
) -> dict[str, Any]:
    """把已完成产出与最初需求做**词面覆盖**比对：goal 关键词（2-gram 集）
    被产出覆盖过半为对齐，偏差超阈值 → ``should_pause=True``（AC：立刻暂停）。

    诚实边界：这是词面近似——语义级"跑题"判定由 LLM 通道完成（后续经
    Hook 总线接入），本函数只做零成本的确定性第一道筛查。
    """
    keys = _keywords(goal)
    if not keys:
        return {"coverage": 1.0, "should_pause": False, "missing_keywords": [],
                "checked_keywords": 0, "note": "目标无可提取关键词，跳过对齐检查"}
    blob = []
    for o in outputs or []:
        blob.append(o if isinstance(o, str) else str(o.get("text") or "") +
                    " " + " ".join(str(c.get("value", "")) for c in (o.get("claims") or [])
                                   if isinstance(c, dict)))
    corpus = " ".join(blob)
    missing = sorted(k for k in keys if k not in corpus)
    coverage = 1.0 - (len(missing) / len(keys))
    return {
        "coverage": round(coverage, 4),
        "should_pause": coverage < coverage_threshold,
        "missing_keywords": missing[:20],
        "checked_keywords": len(keys),
    }


# ---------------------------------------------------------------------------
# -03 按复杂度动态选模型
# ---------------------------------------------------------------------------

_COMPLEX_HINTS = ("架构", "设计", "审查", "评审", "重构", "安全", "迁移", "抽象",
                  "architecture", "design", "review", "refactor", "security")
_LIGHT_HINTS = ("列出", "数一数", "统计", "目录", "行数", "list", "count", "ls")


def classify_complexity(text: str) -> str:
    """启发式复杂度分类：light / standard / complex（确定性，可测试）。"""
    t = (text or "").lower()
    score = 0
    score += 2 * sum(1 for h in _COMPLEX_HINTS if h in t)
    score -= 2 * sum(1 for h in _LIGHT_HINTS if h in t)
    if len(t) > 400:
        score += 1
    if score >= 2:
        return "complex"
    if score <= -1:
        return "light"
    return "standard"


def select_model(
    text: str,
    tiers: dict[str, str] | None = None,
) -> dict[str, str]:
    """按复杂度选模型档位。``tiers`` 由装配方提供（不硬编码模型名——
    成本策略是配置不是代码）。缺省档位时返回 tier 但 model 留空由调用方解析。"""
    tiers = tiers or {}
    tier = classify_complexity(text)
    return {"tier": tier, "model": tiers.get(tier, ""),
            "reason": f"复杂度分类={tier}（提示词命中启发式规则）",
            "parallel_eligible": tier != "complex"}


# ---------------------------------------------------------------------------
# -04 节点备选方案自动切换
# ---------------------------------------------------------------------------

class FallbackExhausted(Exception):
    """所有备选方案都失败。"""


@dataclass
class FallbackAttempt:
    strategy: str
    ok: bool
    error: str = ""


def run_fallback_chain(
    strategies: list[tuple[str, Callable[[], Any]]],
) -> tuple[Any, list[FallbackAttempt]]:
    """-04：按序尝试备选方案，"这招不行换下一招"；第一招成功即返回。
    全部失败抛 FallbackExhausted（带完整尝试轨迹——诚实不静默）。"""
    attempts: list[FallbackAttempt] = []
    for name, fn in strategies or []:
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 —— 失败换下一招，轨迹如实记录
            attempts.append(FallbackAttempt(strategy=name, ok=False,
                                            error=f"{type(exc).__name__}: {exc}"[:200]))
            continue
        attempts.append(FallbackAttempt(strategy=name, ok=True))
        return value, attempts
    raise FallbackExhausted(
        "所有备选方案均失败: " +
        "; ".join(f"{a.strategy}({a.error})" for a in attempts))


# ---------------------------------------------------------------------------
# -05 收口校验防口径漂移
# ---------------------------------------------------------------------------

@dataclass
class ConsistencyViolation:
    kind: str            # data_inconsistency / missing_requirement / format_mismatch
    message: str


def delivery_consistency_check(
    deliverables: list[dict[str, Any]],
    *,
    hard_requirements: tuple[str, ...] = (),
) -> list[ConsistencyViolation]:
    """交付前收口校验：①同一口径（同 key 结论）跨产物必须一致；
    ②用户硬性要求逐条核对（contains 语义）一条不漏；③同组产物格式统一。"""
    violations: list[ConsistencyViolation] = []
    claim_values: dict[str, tuple[Any, str]] = {}
    texts: list[str] = []
    formats: dict[str, str] = {}
    for d in deliverables or []:
        name = str(d.get("name") or d.get("title") or "")
        texts.append(str(d.get("text") or ""))
        ctype = str(d.get("content_type") or "")
        if ctype:
            group = ctype.split("/")[0]
            if formats.setdefault(group, ctype) != ctype:
                violations.append(ConsistencyViolation(
                    "format_mismatch",
                    f"产物 {name!r} 内容类型 {ctype!r} 与同组 {formats[group]!r} 不统一"))
        for c in d.get("claims") or []:
            if not isinstance(c, dict) or not c.get("key"):
                continue
            key, val = str(c["key"]), c.get("value")
            if key in claim_values and _differ(claim_values[key][0], val):
                violations.append(ConsistencyViolation(
                    "data_inconsistency",
                    f"口径漂移: {key!r} 在 {claim_values[key][1]!r}="
                    f"{claim_values[key][0]!r} 与 {name!r}={val!r} 不一致"))
            else:
                claim_values.setdefault(key, (val, name))
    corpus = "\n".join(texts)
    for req in hard_requirements:
        if req and req not in corpus:
            violations.append(ConsistencyViolation(
                "missing_requirement",
                f"用户硬性要求未满足: {req[:80]}"))
    return violations


def _differ(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) != bool(b)
    try:
        return float(a) != float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(a) != str(b)
