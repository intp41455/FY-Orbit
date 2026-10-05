from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import TZDateTime, utcnow


class FeatureFlagRecord(Base):
    """Persisted dynamic feature flag (批次 F / §10.5).

    Supports runtime toggles without application restart, multi-instance
    consistency, and automatic audit attribution.
    """

    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[Any] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
