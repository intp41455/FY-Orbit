"""T6-A 中断分类与处置矩阵测试 + 台账落库 / 审计挂帧。"""

from __future__ import annotations

import asyncio

import pytest

import find_yourself.db.resilience_models  # noqa: F401  (确保表在 metadata)
from find_yourself.db.models import AuditEvent
from find_yourself.db.resilience_models import InterruptionEvent
from find_yourself.runtime.interruption import (
    INTERRUPTION_PLAYBOOK,
    RESUME_POLICIES,
    InterruptionClass,
    classify_provider_error,
    classify_stream_exception,
    mark_resumed,
    playbook_for,
    provider_fingerprint,
    record_interruption,
)
from find_yourself.runtime.providers.base import ProviderError

# ---------------------------------------------------------------------------
# 处置矩阵完整性（T6.1：六类场景逐条覆盖，缺一条即未交付）
# ---------------------------------------------------------------------------

def test_playbook_covers_all_six_scenes_and_unknown():
    scenes = {entry.scene for entry in INTERRUPTION_PLAYBOOK.values()}
    assert {"S1", "S2", "S3", "S4", "S5", "S6"} <= scenes
    assert InterruptionClass.UNKNOWN.value in INTERRUPTION_PLAYBOOK
    for key, entry in INTERRUPTION_PLAYBOOK.items():
        assert entry.resume_policy in RESUME_POLICIES, key
        assert entry.description and entry.on_interrupt and entry.resume_description


def test_playbook_policy_semantics():
    # S5 换供应商必须 confirm（红线 6：成本与输出分布变了，恢复前必须用户确认）
    assert INTERRUPTION_PLAYBOOK["provider_reset"].resume_policy == "confirm"
    # S1/S3/S4 是自动续作的主力场景（陛下：自动找到并继续开工）
    for cls in ("rate_limited", "network_lost", "process_killed"):
        assert INTERRUPTION_PLAYBOOK[cls].resume_policy == "auto"
    # S6 误操作涉及覆盖用户数据，必须人工发起
    assert INTERRUPTION_PLAYBOOK["agent_misuse"].resume_policy == "manual"


def test_playbook_for_accepts_enum_and_str():
    assert playbook_for(InterruptionClass.RATE_LIMITED) is \
        INTERRUPTION_PLAYBOOK["rate_limited"]
    assert playbook_for("stream_broken") is INTERRUPTION_PLAYBOOK["stream_broken"]


# ---------------------------------------------------------------------------
# 分类器
# ---------------------------------------------------------------------------

def test_classify_provider_error():
    assert classify_provider_error(
        ProviderError("rl", status_code=429)) is InterruptionClass.RATE_LIMITED

    class QuotaErr(ProviderError):
        kind = "quota_exhausted"

    assert classify_provider_error(QuotaErr("quota gone")) is InterruptionClass.RATE_LIMITED

    class TransportErr(ProviderError):
        kind = "transport"

    assert classify_provider_error(TransportErr("conn reset")) is InterruptionClass.NETWORK_LOST
    # 4xx 语义错误不是六类里的中断场景——诚实归 unknown，不许硬塞
    assert classify_provider_error(
        ProviderError("bad request", status_code=400)) is InterruptionClass.UNKNOWN


def test_classify_stream_exception():
    assert classify_stream_exception(GeneratorExit()) is InterruptionClass.NETWORK_LOST
    assert classify_stream_exception(asyncio.CancelledError()) is InterruptionClass.NETWORK_LOST
    assert classify_stream_exception(ConnectionError("wire cut")) is InterruptionClass.NETWORK_LOST
    assert classify_stream_exception(
        ProviderError("rl", status_code=429)) is InterruptionClass.RATE_LIMITED
    assert classify_stream_exception(RuntimeError("generator exploded")) is \
        InterruptionClass.STREAM_BROKEN


def test_provider_fingerprint_is_sensitive_to_provider_change_and_stores_no_secret():
    class ConfigA:
        model_provider = "openai"
        model_base_url = "https://api.a.example"
        model_name = "m1"

    class ConfigB(ConfigA):
        model_name = "m2"

    assert provider_fingerprint(ConfigA) == provider_fingerprint(ConfigA)
    assert provider_fingerprint(ConfigA) != provider_fingerprint(ConfigB)
    assert "api.a.example" not in provider_fingerprint(ConfigA)  # 只存摘要，不存原文


# ---------------------------------------------------------------------------
# 台账落库 + 审计挂帧（红线 4：每次中断与每次恢复都留痕）
# ---------------------------------------------------------------------------

def test_record_and_resume_roundtrip(session, audit, owner):
    event = record_interruption(
        session, audit, owner,
        cls=InterruptionClass.RATE_LIMITED,
        detail="429 retry-after 2; retries exhausted",
        task_id="task-1", thread_id="th-1", message_id="msg-1",
        provider_fp="fp-123",
    )
    session.commit()

    row = session.get(InterruptionEvent, event.id)
    assert row is not None
    assert row.interruption_class == "rate_limited"
    assert row.resume_policy == "auto"
    assert row.status == "open"
    assert row.provider_fp == "fp-123"
    frames = session.query(AuditEvent).filter(
        AuditEvent.action == "interruption.recorded",
        AuditEvent.target == event.id,
    ).all()
    assert len(frames) == 1
    assert frames[0].details["interruption_class"] == "rate_limited"

    mark_resumed(session, audit, owner, row, ok=True, note="resumed from checkpoint")
    assert row.status == "resumed"
    assert row.resumed_at is not None
    resumed_frames = session.query(AuditEvent).filter(
        AuditEvent.action == "interruption.resumed",
        AuditEvent.target == event.id,
    ).all()
    assert len(resumed_frames) == 1


def test_resume_failure_stays_open_and_is_audited(session, audit, owner):
    event = record_interruption(session, audit, owner, cls="unknown", detail="mystery")
    mark_resumed(session, audit, owner, event, ok=False, note="no checkpoint on disk")
    session.commit()
    assert event.status == "open"  # 诚实：失败不算恢复，仍然可再续
    frames = session.query(AuditEvent).filter(
        AuditEvent.action == "interruption.resume_failed",
        AuditEvent.target == event.id,
    ).all()
    assert len(frames) == 1


def test_invalid_resume_policy_rejected(session, audit, owner):
    with pytest.raises(ValueError):
        record_interruption(
            session, audit, owner, cls="rate_limited", resume_policy="yolo")
