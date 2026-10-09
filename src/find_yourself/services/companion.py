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

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Conversation, Message, Proposal
from ..runtime.gateway import ModelGateway, ModelNotConfigured
from .actor import Actor
from .audit import AuditService
from .errors import DomainError
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
    def __init__(
        self,
        session: Session,
        audit: AuditService,
        proposals: ProposalService | None = None,
        *,
        settings: Any = None,
        budget: Any = None,
        model_gateway: ModelGateway | None = None,
    ):
        self.s = session
        self.audit = audit
        self.proposals = proposals or ProposalService(session, audit)
        self._settings = settings
        self._budget = budget
        self._model_gateway = model_gateway

    def _gateway(self) -> ModelGateway:
        if self._model_gateway is not None:
            return self._model_gateway
        if self._settings is None:
            raise ModelNotConfigured(
                "No model provider is configured for companion replies. "
                "Set FY_MODEL_API_KEY and FY_MODEL_BASE_URL (OpenAI-compatible endpoint, "
                "e.g. DeepSeek or a local Ollama) to enable real replies."
            )
        self._model_gateway = ModelGateway(self._settings, self._budget)
        return self._model_gateway

    def _model_name(self) -> str:
        return getattr(self._settings, "model_name", None) or "gpt-4o-mini"

    def _conversation_history(self, conversation: Conversation, limit: int = 12) -> list[Message]:
        rows = self.s.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.deleted_at.is_(None))
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).scalars().all()
        return list(reversed(rows))

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

    # Mode-specific constraints sent to the model alongside the conversation.
    U08_LISTEN_CONSTRAINTS = (
        "只倾听模式：仅做纯共情反馈与陪伴；绝不擅自做心理诊断、性格定性，"
        "不触发测评，不安排任务清单，不主动给建议。"
    )
    U09_METAPHYSICAL_CONSTRAINTS = (
        "潜意识/玄学/精神分析探讨：从叙事隐喻与自我反思视角回应，"
        "明确这是探索性视角而非医学诊断或宿命判定；绝不修改用户正式画像事实。"
    )
    STANDARD_CONSTRAINTS = (
        "共情探索模式：理解并回应用户的表达，帮助用户更好地理解当下的观察视角；"
        "不下诊断、不触发测评、不擅自修改画像。"
    )

    def _mode_constraints(self, conversation: Conversation, user_message: str) -> str:
        if self.is_listen_requested(conversation.mode, user_message):
            return self.U08_LISTEN_CONSTRAINTS
        if self.is_metaphysical(user_message):
            return self.U09_METAPHYSICAL_CONSTRAINTS
        return self.STANDARD_CONSTRAINTS

    def _compose_prompt(self, conversation: Conversation, user_message: str) -> str:
        history = self._conversation_history(conversation)
        turns = "\n".join(f"{m.role}: {m.content}" for m in history if m.content)
        return (
            f"[对话约束]\n{self._mode_constraints(conversation, user_message)}\n\n"
            f"[近期对话]\n{turns}\n\n"
            f"[用户最新消息]\nuser: {user_message}"
        )

    def _model_reply(self, actor: Actor, conversation: Conversation, user_message: str) -> tuple[str, str]:
        """Route the non-crisis paths through the governed model gateway.

        Failure is honest: no template fallback exists, so without a configured
        provider the caller gets a 503 telling them how to configure one.
        """
        gateway = self._gateway()
        try:
            result = gateway.complete(
                actor,
                task_id=f"companion:{conversation.id}",
                model=self._model_name(),
                prompt=self._compose_prompt(conversation, user_message),
                target_domain="personal",
                max_tokens=512,
            )
        except ModelNotConfigured:
            raise ModelNotConfigured(
                "No model provider is configured. Set FY_MODEL_API_KEY and FY_MODEL_BASE_URL "
                "(OpenAI-compatible endpoint, e.g. DeepSeek or a local Ollama) to enable real replies."
            ) from None
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError("model_unavailable", f"Model call failed: {type(exc).__name__}", 503) from exc
        return result.text, result.provider_request_id

    def respond(self, actor: Actor, conversation: Conversation, user_message: str) -> dict[str, Any]:
        """Generate a compliant assistant response and metadata based on mode and risk signals.

        Crisis support (U10) stays deterministic — safety copy must never depend
        on a model being reachable. Every other path is model-backed; there is
        deliberately no template fallback (an unconfigured provider raises 503).
        """
        actor.require_authenticated()

        # 1. High Risk Crisis Support (U10) — deterministic, safety-critical.
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
                "generated_by": "safety_template",
            }

        # 2. Model-backed paths (U08 listen-only / U09 metaphysical / standard).
        text, provider_request_id = self._model_reply(actor, conversation, user_message)
        listen_requested = self.is_listen_requested(conversation.mode, user_message)
        metaphysical = self.is_metaphysical(user_message)

        content = f"{PERSPECTIVE_DISCLAIMER}\n\n{text}" if metaphysical else text
        resp: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "mode": "listen" if listen_requested else conversation.mode,
            "is_crisis": False,
            "hotlines_provided": False,
            "unauthorized_third_party_contact": False,
            "is_diagnosis": False,
            "generated_by": "model",
            "model": self._model_name(),
            "provider_request_id": provider_request_id,
        }
        if listen_requested:
            resp["assessment_triggered"] = False
            resp["prescribed_tasks"] = []
        if metaphysical:
            resp["perspective"] = "exploratory_metaphor"
            resp["profile_facts_mutated"] = False
        else:
            resp["assessment_triggered"] = False
            resp["prescribed_tasks"] = []
        return resp

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
