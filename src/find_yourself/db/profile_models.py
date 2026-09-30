"""Database models for 04 Profile (个人与对象多维画像).

Defines:
- ProfileSubject: Subjects (self, person, project, org, topic).
- ProfileImport: Uploaded documents/conversations for profiling.
- SourceSegment: Fine-grained parsed text segments with speaker and content hash.
- ProfileEvidence: Discrete claims/evidence extracted from segments.
- ProfileRun: Profiling execution lifecycle.
- ProfileRevision: Versioned profile snapshots (JSON Schema 1.0 compliant).
- ProfileFeedback: User corrections, endorsements, or rejections on evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from find_yourself.db.base import Base
from find_yourself.db.types import HASH64, ID, TZDateTime, utcnow

SUBJECT_KINDS = ("self", "person", "project", "org", "work", "topic", "other")
EVIDENCE_KINDS = (
    "self_report", "observed_stat", "assessment", "third_party_statement", "model_hypothesis"
)
POLARITIES = ("positive", "neutral", "negative")
REVIEW_STATUSES = ("candidate", "accepted", "edited", "rejected", "uncertain")


class ProfileSubject(Base):
    __tablename__ = "profile_subjects"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="self")
    label: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(f"kind IN {SUBJECT_KINDS}", name="ck_profile_subject_kind"),
    )


class ProfileImport(Base):
    __tablename__ = "profile_imports"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_subjects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subject_candidates: Mapped[list[str]] = mapped_column(JSON, default=list)
    original_asset_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="text")
    mime: Mapped[str] = mapped_column(String(64), default="text/plain")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(HASH64)
    parser_version: Mapped[str] = mapped_column(String(32), default="v1.0")
    consent_scope: Mapped[str] = mapped_column(String(64), default="personal_profile")
    privacy_domain: Mapped[str] = mapped_column(String(16), default="personal")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class SourceSegment(Base):
    __tablename__ = "source_segments"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    import_id: Mapped[str] = mapped_column(
        ForeignKey("profile_imports.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    speaker: Mapped[str] = mapped_column(String(100), default="unknown")
    raw_speaker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    text_content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(HASH64)
    locator: Mapped[str] = mapped_column(String(200), default="L1")


class ProfileEvidence(Base):
    __tablename__ = "profile_evidence"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("profile_subjects.id", ondelete="CASCADE"), index=True
    )
    source_segment_id: Mapped[str] = mapped_column(
        ForeignKey("source_segments.id", ondelete="CASCADE"), index=True
    )
    claim: Mapped[str] = mapped_column(Text)
    evidence_kind: Mapped[str] = mapped_column(String(32), default="observed_stat")
    polarity: Mapped[str] = mapped_column(String(16), default="neutral")
    proposed_dimension: Mapped[str] = mapped_column(String(64), default="general")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=1.0)
    counter_evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(32), default="candidate")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        CheckConstraint(f"evidence_kind IN {EVIDENCE_KINDS}", name="ck_evidence_kind"),
        CheckConstraint(f"polarity IN {POLARITIES}", name="ck_evidence_polarity"),
        CheckConstraint(f"review_status IN {REVIEW_STATUSES}", name="ck_evidence_review_status"),
    )


class ProfileRun(Base):
    __tablename__ = "profile_runs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("profile_subjects.id", ondelete="CASCADE"), index=True
    )
    input_snapshot_hash: Mapped[str] = mapped_column(HASH64)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    rule_version: Mapped[str] = mapped_column(String(32), default="rules_v1")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    workflow_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class ProfileRevision(Base):
    __tablename__ = "profile_revisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("profile_subjects.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    profile_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_runs.id", ondelete="SET NULL"), nullable=True
    )
    core_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    clusters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    user_review_state: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_profile_rev_subject", "subject_id", "revision", unique=True),
    )


class ProfileFeedback(Base):
    __tablename__ = "profile_feedback"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("profile_evidence.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("profile_subjects.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(32))
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
