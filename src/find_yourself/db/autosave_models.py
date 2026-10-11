"""最新快照和最近 100 个版本元数据；历史内容回滚另属流程版本功能。"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class CanvasAutosave(Base):
    __tablename__ = "canvas_autosaves"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: uuid4().hex)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    canvas_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("owner_id", "canvas_id"),)
