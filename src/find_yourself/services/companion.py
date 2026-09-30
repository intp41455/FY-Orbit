"""Companion, Reflective Listening, Perspective Framing & Crisis Safety (F5).

Implements productized requirements for personal companion interactions:
- U08: "只倾听"模式 — 纯共情反馈，不强加心理诊断、量表测评或强制任务。
- U09: 潜意识/玄学/精神分析探讨 — 明确标注探索性叙事视角与隐喻，不作为医学诊断或因果宿命，
  且绝不擅自修改用户正式画像事实。
- U10: 高风险心理危机现实支持 — 检测自杀/自残危机信号时提供现实热线支持，严格保护隐私，
  坚决不擅自联系第三人。
- 画像修改提案闭环 — 任何画像修正均通过提案审批流（pending 状态），不自动成为正式事实。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..db.models import Conversation, Message, Proposal
from .actor import Actor
from .audit import AuditService
from .errors import PermissionDenied, ValidationFailed
from .proposal import ProposalService

CRISIS_KEYWORDS = [
    "自杀", "不想活了", "自残", "轻生", "结束生命", "割腕",
    "跳楼", "活着没意思想死", "suicide", "kill myself", "end my life"
]

METAPHYSICAL_KEYWORDS = [
    "做梦", "潜意识", "梦境", "塔罗", "占星", "星盘", "命理",
    "八字", "精神分析", "荣格原型", "弗洛伊德", "前世", "灵魂", "玄学"
]

CRISIS_HOTLINES = [
    {"name": "全国希望24小时热线", "tel": "400-161-9995"},
    {"name": "北京心理危机干预热线", "tel": "010-82951332"},
    {"name": "华中师范大学心理援助热线", "tel": "400-967-8920"},
    {"name": "紧急救援与求助", "tel": "110 / 120"},
]

PERSPECTIVE_DISCLAIMER = (
    "【探索视角标注】本探讨仅作为叙事隐喻与自我反思探讨视角，"
    "不构成医学/精神科诊断，亦非既定人生因果或宿命判定。"
)


class CompanionService:
    def __init__(self, session: Session, audit: AuditService, proposals: ProposalService | None = None):
        self.s = session
        self.audit = audit
        self.proposals = proposals or ProposalService(session, audit)

    def is_crisis(self, text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in CRISIS_KEYWORDS)

    def is_metaphysical(self, text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in METAPHYSICAL_KEYWORDS)

    def is_listen_requested(self, mode: str, text: str) -> bool:
        if mode == "listen":
            return True
        listen_phrases = ["只倾听", "倾听模式", "不要建议", "不用分析", "不用给我下诊断", "听我说就好"]
        lower = text.lower()
        return any(p in lower for p in listen_phrases)

    def respond(self, actor: Actor, conversation: Conversation, user_message: str) -> dict[str, Any]:
        """Generate a compliant assistant response and metadata based on mode and risk signals."""
        actor.require_authenticated()

        # 1. High Risk Crisis Support (U10)
        if self.is_crisis(user_message):
            lines = [
                "我听到了你此刻承受的巨大痛苦与不易。你的生命非常珍贵，请不要独自面对这一切。",
                "在这个艰难的时刻，请尝试联系现实中的专业支持资源或信任的人，他们随时愿意倾听并支持你：",
            ]
            for h in CRISIS_HOTLINES:
                lines.append(f"- {h['name']}: {h['tel']}")
            lines.append("本系统严格守护你的隐私安全，绝不擅自联系未经授权的第三方。请允许现实中的专业力量陪伴你度过危机。")
            content = "\n".join(lines)
            return {
                "role": "assistant",
                "content": content,
                "mode": conversation.mode,
                "is_crisis": True,
                "hotlines_provided": True,
                "unauthorized_third_party_contact": False,
                "is_diagnosis": False,
                "assessment_triggered": False,
                "prescribed_tasks": [],
            }

        # 2. Listen-Only Mode (U08)
        if self.is_listen_requested(conversation.mode, user_message):
            content = (
                f"我完全听到了你的表达。你所经历和感受的这一切是真实而自然的，我在这里静静陪伴着你。"
                f"我不会擅自为你下任何心理诊断、性格定性或安排任务清单，如果你还想说说，我就在这里听着。"
            )
            return {
                "role": "assistant",
                "content": content,
                "mode": "listen",
                "is_crisis": False,
                "hotlines_provided": False,
                "unauthorized_third_party_contact": False,
                "is_diagnosis": False,
                "assessment_triggered": False,
                "prescribed_tasks": [],
            }

        # 3. Subconscious / Metaphysical / Psychoanalytic Reflection (U09)
        if self.is_metaphysical(user_message):
            body = (
                f"从叙事隐喻的角度来看，关于“{user_message[:30]}...”的探讨为你提供了一个探索内在潜意识图像与感受的反射面。\n"
                f"心理学视隐喻为意识与情感联结的桥梁，你可以观察它在生活经验中唤起的情绪共鸣，而不是将其视为不可更改的宿命结论。"
            )
            content = f"{PERSPECTIVE_DISCLAIMER}\n\n{body}"
            return {
                "role": "assistant",
                "content": content,
                "mode": conversation.mode,
                "is_crisis": False,
                "hotlines_provided": False,
                "unauthorized_third_party_contact": False,
                "is_diagnosis": False,
                "perspective": "exploratory_metaphor",
                "profile_facts_mutated": False,
            }

        # 4. Standard Empathetic Exploration
        content = (
            f"关于你谈到的内容，我理解你的思考。探索自我是持续而开放的旅程，"
            f"所有发现都是帮助你更好地理解当下的观察视角。"
        )
        return {
            "role": "assistant",
            "content": content,
            "mode": conversation.mode,
            "is_crisis": False,
            "hotlines_provided": False,
            "unauthorized_third_party_contact": False,
            "is_diagnosis": False,
            "assessment_triggered": False,
            "prescribed_tasks": [],
        }

    def propose_profile_correction(
        self,
        actor: Actor,
        *,
        profile_target_id: str,
        expected_version: int,
        diff_payload: dict,
        reason: str,
        rollback: str = "revert_profile_to_previous_snapshot",
    ) -> Proposal:
        """Create a proposal for user profile adjustments.

        The adjustment remains pending and is NEVER applied to the user's permanent
        facts without explicit owner approval.
        """
        actor.require_owner()
        prop = self.proposals.create(
            actor,
            operation="memory.upsert",
            target_id=profile_target_id,
            expected_version=expected_version,
            payload=diff_payload,
            reason=reason,
            rollback=rollback,
        )
        return prop
