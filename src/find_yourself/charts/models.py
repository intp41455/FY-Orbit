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
