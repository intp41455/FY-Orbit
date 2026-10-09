"""F5 Comprehensive Verification: Companion, Assessment, Growth & Skills.

Tests:
1. U02: Conversation history recovery on refresh / re-query (exact match of text and IDs).
2. U07: Custom four-dimension exploratory assessment (accurate non-official label, MBTI disclaimer, no population norms).
3. U08: Listen-only mode without unsolicited psychiatric/personality diagnosis or forced tasks.
4. U09: Subconscious/psychoanalysis/metaphysics perspective framing with explicit metaphor disclaimer.
5. U10: High-risk psychological crisis support with reality hotlines and zero unauthorized third-party contact.
6. Profile correction proposal approval lifecycle (pending until approved, immutable audit).
7. Skill promotion and rollback lifecycle (A05-A10: evaluation gates, rollback to verified version, invoke checks).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from find_yourself.adapters.assessments import CATALOG, scorer
from find_yourself.db.models import Conversation, Message
from find_yourself.services.audit import AuditService
from find_yourself.services.companion import PERSPECTIVE_DISCLAIMER, CompanionService
from find_yourself.services.errors import Conflict
from find_yourself.services.proposal import ProposalService
from find_yourself.services.skill import SkillService

# ============================================================================
# 1. U02: Conversation History Refresh & Restoration
# ============================================================================

def test_u02_conversation_refresh_exact_restoration(session, owner):
    audit = AuditService(session)
    c = Conversation(id=uuid4().hex, owner_id=owner.owner_id, title="Reflect", domain="personal", mode="listen")
    session.add(c)
    session.flush()

    m1 = Message(id=uuid4().hex, conversation_id=c.id, role="user", content="Today felt overwhelming", source="user", client_message_id="cm-u02-1")
    m2 = Message(id=uuid4().hex, conversation_id=c.id, role="assistant", content="I hear you; taking things one breath at a time.", source="companion", client_message_id="cm-u02-2")
    session.add_all([m1, m2])
    session.commit()

    # Simulate UI refresh / re-query from DB
    loaded = session.execute(
        select(Message).where(Message.conversation_id == c.id, Message.deleted_at.is_(None)).order_by(Message.created_at.asc())
    ).scalars().all()

    assert len(loaded) == 2
    assert loaded[0].id == m1.id
    assert loaded[0].content == "Today felt overwhelming"
    assert loaded[0].role == "user"
    assert loaded[1].id == m2.id
    assert loaded[1].content == "I hear you; taking things one breath at a time."
    assert loaded[1].role == "assistant"


# ============================================================================
# 2. U07: Custom Four-Dimension Exploratory Assessment
# ============================================================================

def test_u07_fourdim_exploratory_labels_and_disclaimer():
    s = scorer.start("fourdim-exploratory")
    q = CATALOG["fourdim-exploratory"]

    # Fill all 4 dimension items
    answers = {item.id: 4 for item in q.items}
    scorer.record_answers(s.session_id, answers)
    res = scorer.submit(s.session_id)

    assert res.status == "scored"
    result = res.result
    assert result is not None

    # Check non-official type label
    assert "type_label" in result
    assert "非官方探索倾向" in result["type_label"]
    assert result["official_mbti"] is False
    assert result["clinical"] is False

    # Check explicit caveat and disclaimers
    assert "MBTI®" in result["caveat"]
    assert "绝非官方" in result["caveat"]
    assert result["norms"] is None
    assert "无匹配常模" in result["norm_note"]
    assert "scales" in result


# ============================================================================
# 3. U08: Listen-Only Mode (No Unsolicited Diagnosis / Forced Tasks)
# ============================================================================

class _CountingProvider:
    """Deterministic fake provider recording every prompt it receives."""

    def __init__(self):
        from decimal import Decimal
        self._decimal = Decimal
        self.calls: list[str] = []

    def complete(self, *, model: str, prompt: str, max_tokens: int = 1024,
                 timeout_seconds: float = 30.0):
        from find_yourself.runtime.gateway import CallResult

        self.calls.append(prompt)
        n = len(self.calls)
        return CallResult(
            text=f"（模型回复 #{n}）我在听，请继续说。",
            usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            settled_amount=self._decimal("0"),
            provider_request_id=f"fake-{n}",
        )


def _model_backed_companion(session, audit, provider: _CountingProvider | None = None):
    from find_yourself.config import Settings
    from find_yourself.runtime.gateway import ModelGateway

    provider = provider or _CountingProvider()
    settings = Settings(
        session_secret="x" * 32,
        model_api_key="test-key",
        model_base_url="http://127.0.0.1:9",
    )
    gateway = ModelGateway(settings, provider=provider)
    return CompanionService(session, audit, settings=settings, model_gateway=gateway), provider


def test_u08_listen_only_mode_no_forced_diagnosis(session, owner):
    audit = AuditService(session)
    companion, provider = _model_backed_companion(session, audit)
    conv = Conversation(id="c-listen-1", owner_id=owner.owner_id, title="Silent Walk", domain="personal", mode="listen")

    user_msg = "最近工作压力很大，我只想倾听，请不要给我做测评或者下心理诊断。"
    resp = companion.respond(owner, conv, user_msg)

    assert resp["role"] == "assistant"
    assert resp["mode"] == "listen"
    assert resp["is_diagnosis"] is False
    assert resp["assessment_triggered"] is False
    assert resp["prescribed_tasks"] == []
    assert resp["generated_by"] == "model"
    # U08 constraints actually reached the model prompt
    assert "只倾听模式" in provider.calls[-1]
    assert "绝不擅自做心理诊断" in provider.calls[-1]
    # and the old hardcoded template is gone
    assert "不会擅自为你下任何心理诊断" not in resp["content"]


# ============================================================================
# 4. U09: Subconscious / Metaphysical Discussion Perspective Framing
# ============================================================================

def test_u09_metaphysical_subconscious_perspective_framing(session, owner):
    audit = AuditService(session)
    companion, provider = _model_backed_companion(session, audit)
    conv = Conversation(id="c-meta-1", owner_id=owner.owner_id, title="Dream Reflection", domain="personal", mode="explore")

    user_msg = "我昨晚做梦梦见大蛇和高塔，这在荣格精神分析或塔罗潜意识里预示着我的什么宿命？"
    resp = companion.respond(owner, conv, user_msg)

    assert resp["role"] == "assistant"
    assert resp["is_diagnosis"] is False
    assert resp["perspective"] == "exploratory_metaphor"
    assert resp["profile_facts_mutated"] is False

    # Mandatory perspective disclaimer banner (service-side, not model-dependent)
    assert PERSPECTIVE_DISCLAIMER in resp["content"]
    assert "不构成医学/精神科诊断" in resp["content"]
    assert "非既定人生因果或宿命判定" in resp["content"]
    # U09 constraints actually reached the model prompt
    assert "潜意识/玄学/精神分析探讨" in provider.calls[-1]


# ============================================================================
# 4b. Model-backed honesty: no key -> explicit 503; distinct inputs -> distinct replies
# ============================================================================

def test_companion_without_provider_raises_honest_error(session, owner):
    from find_yourself.runtime.gateway import ModelNotConfigured

    audit = AuditService(session)
    companion = CompanionService(session, audit)
    conv = Conversation(id="c-nomodel", owner_id=owner.owner_id, title="T", domain="personal", mode="chat")

    with pytest.raises(ModelNotConfigured) as exc:
        companion.respond(owner, conv, "今天天气不错，我去公园散步了")
    assert "FY_MODEL_API_KEY" in str(exc.value)


def test_companion_distinct_inputs_get_distinct_replies(session, owner):
    audit = AuditService(session)
    companion, provider = _model_backed_companion(session, audit)
    conv = Conversation(id="c-distinct", owner_id=owner.owner_id, title="T", domain="personal", mode="chat")

    replies = [
        companion.respond(owner, conv, "我最近很焦虑，总觉得哪里不对")["content"],
        companion.respond(owner, conv, "今天天气不错，我去公园散步了")["content"],
        companion.respond(owner, conv, "asdkjh 12345 @@@ ###random###")["content"],
    ]
    assert len(set(replies)) == 3
    assert len(provider.calls) == 3


# ============================================================================
# 5. U10: High-Risk Psychological Crisis Reality Support
# ============================================================================

def test_u10_high_risk_crisis_support_and_no_third_party_leak(session, owner):
    audit = AuditService(session)
    companion = CompanionService(session, audit)
    conv = Conversation(id="c-crisis-1", owner_id=owner.owner_id, title="Crisis", domain="personal", mode="listen")

    user_msg = "我很绝望，活着太痛苦了，我不想活了，想自杀结束这一切。"
    resp = companion.respond(owner, conv, user_msg)

    assert resp["role"] == "assistant"
    assert resp["is_crisis"] is True
    assert resp["hotlines_provided"] is True
    assert resp["unauthorized_third_party_contact"] is False
    assert resp["is_diagnosis"] is False

    # Verified hotlines provided in reality support text
    assert "400-161-9995" in resp["content"]
    assert "010-82951332" in resp["content"]
    assert "严格守护你的隐私安全，绝不擅自联系未经授权的第三方" in resp["content"]


# ============================================================================
# 6. Profile Correction Proposal Lifecycle
# ============================================================================

def test_profile_correction_proposal_approval_lifecycle(session, owner):
    audit = AuditService(session)
    companion = CompanionService(session, audit)
    proposals = ProposalService(session, audit)

    # 1. Submit proposal for profile adjustment
    diff = {"attributes": {"communication_style": "introverted_reflective"}}
    prop = companion.propose_profile_correction(
        owner,
        profile_target_id="user_profile_main",
        expected_version=1,
        diff_payload=diff,
        reason="User reflection on conversation insights",
    )
    session.flush()

    assert prop.status == "pending"
    assert prop.operation == "memory.upsert"

    # 2. Re-compute digest matches stored canonical digest
    digest = proposals.compute_digest(prop)
    assert prop.digest == digest

    # 3. Owner approves proposal -> transitions to executed
    decided = proposals.decide(
        owner,
        prop.id,
        client_digest=prop.digest,
        approve=True,
    )
    assert decided.status == "executed"
    assert decided.decided_at is not None


# ============================================================================
# 7. Skill Staging, Multi-Dimensional Evaluation & Rollback (A05-A10)
# ============================================================================

def test_skill_evaluation_promotion_and_rollback_lifecycle(session, owner):
    audit = AuditService(session)
    skills = SkillService(session, audit)

    # 1. Stage v1.0.0
    pkg_v1 = {"name": "summarizer", "code": "def run(x): return x[:10]"}
    s1 = skills.stage(
        owner,
        name="summarizer",
        semantic_version="1.0.0",
        package=pkg_v1,
        source="local",
        license_="MIT",
        domain="work",
    )
    assert s1.state == "staged"
    # Unapproved skill cannot be invoked (A05)
    assert skills.can_invoke(s1.id) is False

    # 2. Evaluate v1.0.0 (static pass, functional pass)
    ev1 = skills.evaluate(owner, s1.id, static_passed=True, functional_passed=True)
    # Promote v1.0.0
    skills.promote(owner, s1.id, ev1.id)
    assert s1.state == "active"
    assert skills.can_invoke(s1.id) is True

    # 3. Stage v2.0.0 (regression candidate)
    pkg_v2 = {"name": "summarizer", "code": "def run(x): return x[:5]"}
    s2 = skills.stage(
        owner,
        name="summarizer",
        semantic_version="2.0.0",
        package=pkg_v2,
        source="local",
        license_="MIT",
        domain="work",
    )
    # Functional test failure prevents promotion (A07)
    ev_fail = skills.evaluate(owner, s2.id, static_passed=True, functional_passed=False)
    with pytest.raises(Conflict):
        skills.promote(owner, s2.id, ev_fail.id)
    assert s2.state == "staged"

    # Now pass functional test on fixed evaluation and promote
    ev2 = skills.evaluate(owner, s2.id, static_passed=True, functional_passed=True)
    skills.promote(owner, s2.id, ev2.id)
    assert s2.state == "active"

    # 4. Rollback v2.0.0 back to proven v1.0.0 (A09)
    curr, rolled_back = skills.rollback(owner, s2.id, s1.id)
    assert curr.state == "disabled"
    assert rolled_back.state == "active"

    # Disabled skill cannot be invoked; active rolled back skill can be invoked
    assert skills.can_invoke(s2.id) is False
    assert skills.can_invoke(s1.id) is True
