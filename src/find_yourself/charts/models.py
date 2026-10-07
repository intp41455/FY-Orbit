"""Pydantic and data models for deterministic chart calculation, external import, and dual-path interpretation."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ChartRequest(BaseModel):
    """Input parameters for deterministic astrological/bazi chart computation."""

    birth_date: str = Field(..., description="Date of birth in YYYY-MM-DD format", pattern=r"^\d{4}-\d{2}-\d{2}$")
    birth_time: str | None = Field(default=None, description="Time of birth in HH:MM or HH:MM:SS format")
    timezone_str: str = Field(default="Asia/Shanghai", description="IANA timezone name, e.g., Asia/Shanghai, UTC")
    longitude: float | None = Field(default=120.0, description="Longitude in decimal degrees")
    latitude: float | None = Field(default=30.0, description="Latitude in decimal degrees")
    location_name: str | None = Field(default=None, description="Descriptive birthplace name")
    calendar: str = Field(default="solar", description="Input calendar type: 'solar' or 'lunar'")
    system: str = Field(default="bazi", description="Astrological system: 'bazi', 'western', 'ziwei'")
    house_system: str = Field(default="placidus", description="House system for western astrology")
    unknown_time: bool = Field(default=False, description="Flag indicating birth time is unknown")
    gender: str = Field(default="unknown", description="Gender: 'male', 'female', 'unknown'")


class ExternalChartImportRequest(BaseModel):
    """Input for importing externally computed or scanned chart data."""

    raw_text: str | None = Field(default=None, description="Unstructured or OCR text from external software")
    raw_json: dict[str, Any] | None = Field(default=None, description="Structured chart JSON from third-party tools")
    source_software: str = Field(default="external_input", description="Name of originating tool/software")
    system: str = Field(default="bazi", description="Astrological system: 'bazi', 'western', 'ziwei'")


class ChartResult(BaseModel):
    """Immutable, versioned result of a chart calculation or external import."""

    chart_id: str
    system: str
    computed_at: str
    engine_version: str
    normalized_utc: str | None
    unknown_time: bool
    is_external_imported: bool = False
    external_source: str | None = None
    computed_data: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    data_hash: str


class InterpretRequest(BaseModel):
    """Request for multi-layered dual-path chart interpretation."""

    chart_id: str
    perspective: str = Field(default="psychological", description="'psychological', 'traditional', or 'comparative'")
    user_notes: str | None = Field(default=None, description="Optional user inquiry context or notes")
    authorized_domain: str = Field(default="personal", description="Domain boundary for personal memory retrieval")
    include_web_search: bool = Field(default=True, description="Whether to include public web references")
    include_personal_memory: bool = Field(default=True, description="Whether to include authorized personal memory")


class InterpretationResult(BaseModel):
    """Structured, layered interpretation response separating facts, public citations, and hypotheses."""

    chart_id: str
    system: str
    perspective: str
    sections: dict[str, str] = Field(
        ...,
        description="Keys: 'computed_chart', 'public_reference', 'personal_records', 'hypothesis_reasoning'",
    )
    web_citations: list[dict[str, Any]] = Field(default_factory=list)
    personal_citations: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str = Field(
        default="【文化与娱乐免责声明】命理与星盘解读仅作为传统文化研究、生活自省隐喻与心理投射工具，不可作为医疗诊断、法律建议、财务投资或重大人生决策的确定性因果依据。"
    )


class DailyFortuneRequest(BaseModel):
    """Request for deterministic daily fortune computation."""

    birth_date: str = Field(..., description="Date of birth in YYYY-MM-DD format", pattern=r"^\d{4}-\d{2}-\d{2}$")
    target_date: str | None = Field(default=None, description="Target date in YYYY-MM-DD format (defaults to today)")
    birth_time: str | None = Field(default=None, description="Time of birth in HH:MM format")
    timezone_str: str = Field(default="Asia/Shanghai", description="IANA timezone name")


class DailyFortuneResult(BaseModel):
    """Deterministic daily fortune result with zero external inference cost."""

    date: str
    solar_date: str
    lunar_date: str
    day_pillar: str
    day_element: str
    user_element: str
    relation: str
    luck_score: int
    favorable: list[str]
    unfavorable: list[str]
    oracle_message: str
    lucky_color: str
    lucky_direction: str
    cabin_event: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = "【娱乐与文化参考】每日运势基于干支与五行象征推导，仅供自我调节与日常启发。"


class TarotCard(BaseModel):
    """Tarot card drawn with orientation and meaning."""

    card_id: int
    name: str
    arcana: str = "major"
    is_reversed: bool = False
    position_label: str = "当前指引"
    keywords: list[str] = Field(default_factory=list)
    meaning: str
    guidance: str


class TarotDrawRequest(BaseModel):
    """Request to draw tarot oracle cards."""

    spread: str = Field(default="single", description="'single' (每日单张签) or 'three_cards' (时间流三牌阵)")
    question: str | None = Field(default=None, description="Optional focus question or theme")
    seed: int | None = Field(default=None, description="Deterministic seed for reproducibility")


class TarotDrawResult(BaseModel):
    """Tarot oracle reading linked with deskpet/cabin interactions."""

    draw_id: str
    drawn_at: str
    spread: str
    question: str | None
    cards: list[TarotCard]
    overall_reading: str
    cabin_interaction: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = "【娱乐与自省隐喻】塔罗牌抽卡结果为象征意象与投射练习，请保持理性思考。"


class SynastryRequest(BaseModel):
    """Request for synastry energy analysis between two birth charts."""

    chart_a: ChartRequest
    chart_b: ChartRequest
    relationship_type: str = Field(default="collaboration", description="'collaboration', 'friendship', 'partner'")


class SynastryResult(BaseModel):
    """Deterministic synastry balance analysis."""

    compatibility_score: int
    element_balance: dict[str, Any]
    synergy_points: list[str]
    friction_points: list[str]
    actionable_advice: str
    disclaimer: str = "【文化与娱乐参考】合盘能量基于古典五行比对，人际关系关键在于坦诚沟通与相互包容。"
