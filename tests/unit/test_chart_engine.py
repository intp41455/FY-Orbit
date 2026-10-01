"""Unit tests for deterministic chart computation engine and layered interpretation."""

from __future__ import annotations

import pytest
from find_yourself.charts.engine import ChartImporter, DeterministicChartEngine
from find_yourself.charts.interpreter import ChartInterpreter, DISCLAIMER_TEXT
from find_yourself.charts.models import (
    ChartRequest,
    ExternalChartImportRequest,
)


def test_bazi_deterministic_calculation() -> None:
    req = ChartRequest(
        birth_date="1990-06-15",
        birth_time="12:00",
        timezone_str="Asia/Shanghai",
        system="bazi",
    )
    result = DeterministicChartEngine.compute_bazi(req)

    assert result.system == "bazi"
    assert result.is_external_imported is False
    assert result.unknown_time is False
    assert len(result.data_hash) == 64

    data = result.computed_data
    assert "day_master" in data
    assert "day_master_element" in data
    pillars = data["pillars"]
    assert pillars["year"]["stem"] is not None
    assert pillars["year"]["branch"] is not None
    assert pillars["month"]["stem"] is not None
    assert pillars["day"]["stem"] == data["day_master"]
    assert pillars["hour"] is not None
    assert "ten_god" in pillars["year"]


def test_bazi_unknown_time_safe_handling() -> None:
    req = ChartRequest(
        birth_date="1988-11-20",
        birth_time=None,
        unknown_time=True,
        timezone_str="Asia/Shanghai",
        system="bazi",
    )
    result = DeterministicChartEngine.compute_bazi(req)

    assert result.unknown_time is True
    pillars = result.computed_data["pillars"]
    assert pillars["hour"] is None  # Hour pillar must NOT be guessed
    assert any("出生时辰未知" in w for w in result.warnings)


def test_western_astrology_deterministic() -> None:
    req = ChartRequest(
        birth_date="1995-03-21",
        birth_time="08:30",
        timezone_str="UTC",
        longitude=0.0,
        latitude=51.5,
        system="western",
    )
    result = DeterministicChartEngine.compute_western(req)

    assert result.system == "western"
    planets = result.computed_data["planets"]
    assert "Sun" in planets
    assert "Moon" in planets
    assert "Mercury" in planets
    # On March 21, Sun is around Aries (0 degrees)
    sun = planets["Sun"]
    assert "Aries" in sun["sign"] or "Pisces" in sun["sign"]
    assert result.computed_data["ascendant"] is not None


def test_external_chart_importer_non_fabrication() -> None:
    # 1. 4-pillar text import
    req = ExternalChartImportRequest(
        raw_text="用户提供八字：庚午年 壬午月 辛亥日 甲午时",
        source_software="other_bazi_app",
        system="bazi",
    )
    result = ChartImporter.parse_and_import(req)
    assert result.is_external_imported is True
    assert result.external_source == "other_bazi_app"
    pillars = result.computed_data["pillars"]
    assert pillars["year"]["stem"] == "庚"
    assert pillars["year"]["branch"] == "午"
    assert pillars["month"]["stem"] == "壬"
    assert pillars["day"]["stem"] == "辛"
    assert pillars["hour"]["stem"] == "甲"

    # 2. 3-pillar text import (missing hour pillar)
    req_3 = ExternalChartImportRequest(
        raw_text="四柱前三柱：庚午 壬午 辛亥",
        source_software="ocr_scan",
        system="bazi",
    )
    result_3 = ChartImporter.parse_and_import(req_3)
    assert result_3.is_external_imported is True
    assert result_3.computed_data["pillars"]["hour"] is None
    assert any("时柱未提供" in w for w in result_3.warnings)


def test_chart_interpreter_layered_output_and_disclaimer() -> None:
    req = ChartRequest(
        birth_date="1990-06-15",
        birth_time="12:00",
        system="bazi",
    )
    chart = DeterministicChartEngine.compute_bazi(req)

    web_citations = [{
        "title": "《滴天髓阐微》",
        "source": "四库全书",
        "url": "https://ctext.org/wiki.pl?if=gb&chapter=249012",
        "snippet": "五行中和",
    }]
    personal_citations = [{
        "record_id": "mem-101",
        "domain": "personal",
        "content": "追求结构化逻辑与创造性工作",
    }]

    interp = ChartInterpreter.interpret(
        chart=chart,
        perspective="psychological",
        web_citations=web_citations,
        personal_citations=personal_citations,
        user_notes="如何平衡工作与生活？",
    )

    assert interp.chart_id == chart.chart_id
    assert interp.perspective == "psychological"
    assert interp.disclaimer == DISCLAIMER_TEXT

    sections = interp.sections
    assert "computed_chart" in sections
    assert "public_reference" in sections
    assert "personal_records" in sections
    assert "hypothesis_reasoning" in sections

    # Check that deterministic facts are in computed_chart
    assert chart.computed_data["day_master"] in sections["computed_chart"]
    # Check that citations are in public_reference
    assert "《滴天髓阐微》" in sections["public_reference"]
    # Check that personal memory is in personal_records
    assert "追求结构化逻辑" in sections["personal_records"]
    # Check that reasoning emphasizes hypothetical perspective
    assert "荣格" in sections["hypothesis_reasoning"] or "心理" in sections["hypothesis_reasoning"]
