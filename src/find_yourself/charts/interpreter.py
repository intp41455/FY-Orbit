"""Structured, layered interpretation generator separating computed facts, public citations, and hypotheses."""

from __future__ import annotations

from typing import Any
from .models import ChartResult, InterpretationResult


DISCLAIMER_TEXT = (
    "【文化与娱乐免责声明】命理与星盘解读仅作为传统文化研究、生活自省隐喻与心理投射工具，"
    "不可作为医疗诊断、法律建议、财务投资或重大人生决策的确定性因果依据。"
)


class ChartInterpreter:
    """Produces layered, evidence-traceable interpretation reports."""

    @staticmethod
    def interpret(
        chart: ChartResult,
        perspective: str = "psychological",
        web_citations: list[dict[str, Any]] | None = None,
        personal_citations: list[dict[str, Any]] | None = None,
        user_notes: str | None = None,
    ) -> InterpretationResult:
        web_citations = web_citations or []
        personal_citations = personal_citations or []

        # Layer 1: [计算盘面] Deterministic Facts
        calc_lines = [f"=== 体系: {chart.system.upper()} (引擎版本: {chart.engine_version}) ==="]
        if chart.normalized_utc:
            calc_lines.append(f"计算对齐标准 UTC 时刻: {chart.normalized_utc}")
        if chart.unknown_time:
            calc_lines.append("【时辰标记】: 出生时间未提供或未知，时柱/分宫缺省，未做任何无根据猜测。")

        data = chart.computed_data
        if chart.system == "bazi":
            day_master = data.get("day_master", "")
            elem = data.get("day_master_element", "")
            calc_lines.append(f"日主天干: {day_master} ({elem})")
            pillars = data.get("pillars", {})
            for name in ["year", "month", "day", "hour"]:
                p = pillars.get(name)
                if p:
                    calc_lines.append(
                        f" - {name}柱: {p.get('stem')}{p.get('branch')} "
                        f"[{p.get('stem_element')}/{p.get('branch_element')}] "
                        f"(十神: {p.get('ten_god')})"
                    )
                else:
                    calc_lines.append(f" - {name}柱: 未知缺省")
        elif chart.system == "western":
            planets = data.get("planets", {})
            for p_name, p_info in planets.items():
                calc_lines.append(f" - {p_name}: {p_info.get('sign')} {p_info.get('sign_degrees')}°")
            asc = data.get("ascendant")
            if asc:
                calc_lines.append(f" - 上升星座 (Ascendant): {asc.get('sign')} {asc.get('sign_degrees')}°")
            else:
                calc_lines.append(" - 上升星座: 出生时刻或坐标未知，未予推算")
        else:
            calc_lines.append(f"原始结构化数据: {list(data.keys())}")

        section_computed = "\n".join(calc_lines)

        # Layer 2: [公开资料] Public Literature & Citations
        pub_lines = ["已检索并核验的公开文献与学术参考源："]
        if web_citations:
            for idx, c in enumerate(web_citations, 1):
                pub_lines.append(f"{idx}. 【{c.get('title')}】(来源: {c.get('source')})")
                pub_lines.append(f"   引用链接: {c.get('url')}")
                pub_lines.append(f"   文献摘要: {c.get('snippet')}")
        else:
            pub_lines.append("未引入或无需全网公开参考源。")
        section_public = "\n".join(pub_lines)

        # Layer 3: [个人记录] Authorized Personal Background
        priv_lines = ["用户显式授权召回的个人背景记录（严格域隔离）："]
        if personal_citations:
            for idx, m in enumerate(personal_citations, 1):
                priv_lines.append(f"{idx}. 记录ID: {m.get('record_id')} [域: {m.get('domain')}]")
                priv_lines.append(f"   内容摘要: {m.get('content')}")
        else:
            priv_lines.append("未加载或无匹配的授权个人私有记录。")
        if user_notes:
            priv_lines.append(f"用户补充提问/自述: {user_notes}")
        section_personal = "\n".join(priv_lines)

        # Layer 4: [解释/假设] Synthesis & Perspective
        hyp_lines = [f"=== 视角分析模式: {perspective.upper()} ==="]
        if perspective == "psychological":
            hyp_lines.append(
                "本解读采用心理动力学与荣格原型视角。盘面中的符号并非命运预设，"
                "而是将个体内在性格张力、认知偏好与未分化潜能进行投射与具象化的反思棱镜。"
            )
            if chart.system == "bazi":
                hyp_lines.append(
                    f"以日主【{data.get('day_master')}】为例，它在心理象征中映射出当事人核心的自我认同风格与能量倾向。"
                )
            elif chart.system == "western":
                sun_info = data.get("planets", {}).get("Sun", {})
                hyp_lines.append(
                    f"以太阳落入【{sun_info.get('sign')}】为例，象征当事人在意识层面追求实现的主体生命力与成长动机。"
                )
        elif perspective == "traditional":
            hyp_lines.append(
                "本解读采用传统命理格局的考据梳理视角，依据古籍十神生克模型进行象征性推导。"
                "古法辞章带有历史时代的社会分层烙印，不可机械套用于现代生活。"
            )
        else:
            hyp_lines.append(
                "本解读采用多流派比较对照视角，结合东西方符号学象征意义，探索不同文化模型下的自我观察维度。"
            )
        hyp_lines.append(
            "提示：所有推论均为认知反思假设，旨在促进主观能动性与自我接纳，而非因果定论。"
        )
        section_hyp = "\n".join(hyp_lines)

        return InterpretationResult(
            chart_id=chart.chart_id,
            system=chart.system,
            perspective=perspective,
            sections={
                "computed_chart": section_computed,
                "public_reference": section_public,
                "personal_records": section_personal,
                "hypothesis_reasoning": section_hyp,
            },
            web_citations=web_citations,
            personal_citations=personal_citations,
            disclaimer=DISCLAIMER_TEXT,
        )
