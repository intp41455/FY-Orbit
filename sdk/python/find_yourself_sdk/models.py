"""FindYourself SDK 数据模型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RatingSummary:
    average_rating: float
    rating_count: int
    score: float
    distribution: dict[str, int] = field(default_factory=dict)


@dataclass
class PluginCard:
    skill_id: str
    name: str
    version: str
    domain: str
    risk_level: str
    rating: RatingSummary
    capabilities: list[str] = field(default_factory=list)
    signature_verified: bool = False


@dataclass
class TemplateCard:
    template_id: str
    name: str
    scenario: str
    summary: str
    quality_tier: str
    rating: RatingSummary
    member_count: int = 0
    is_factory: bool = False


@dataclass
class SecurityReviewReport:
    passed: bool
    risk_level: str
    checksum_verified: bool
    can_import: bool
    requested_tools: list[str] = field(default_factory=list)
    dangerous_tools: list[str] = field(default_factory=list)
    sensitive_tools: list[str] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)
    requires_user_confirmation: bool = False


@dataclass
class ContextSlice:
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_kind: str
    relevance_score: float
    content: str = ""
