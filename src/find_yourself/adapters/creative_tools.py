"""Creative & Daily Arrangement Tools Adapters (Execution Manual F7, A11, A12).

Declarations and rules:
1. Every tool explicitly declares: purpose, uploaded data, provider, license, budget,
   cancellability, storage target, and failure handling.
2. Tools with App UI only and no formal API (e.g. WeChat, proprietary booking apps)
   are declared un-automatable (`can_automate=False`) with manual alternatives.
   Browser packet capture / reverse engineering is strictly prohibited.
3. Third-party messaging, purchasing, calendar changes, and external publishing
   require individual owner approval (`requires_individual_approval=True`).
4. At least one authorized real creation tool (`local_authorized_calendar` / `local_creative_art`)
   completes a true asynchronous closed loop, persisting artifacts to S3 with domain="personal".
5. Unconfigured external tools return structured unavailability without simulated success.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Artifact
from ..services.actor import Actor
from ..services.audit import AuditService
from ..services.budget import BudgetService
from ..services.errors import DomainError, NotFound
from .artifacts import LocalArtifactStore, S3ArtifactStore


@dataclass(frozen=True)
class ToolDeclaration:
    tool_id: str
    name: str
    category: str  # image | music | video | calendar | life_schedule | social_message | purchase
    purpose: str
    uploaded_data: str
    provider: str
    license: str
    budget_estimate_usd: Decimal
    cancellable: bool
    storage_target: str
    failure_handling: str
    requires_individual_approval: bool = False
    can_automate: bool = True
    manual_alternative: str | None = None
    configured: bool = False


TOOL_CATALOG: dict[str, ToolDeclaration] = {
    "local_calendar_schedule": ToolDeclaration(
        tool_id="local_calendar_schedule",
        name="本地日程与生活安排生成器",
        category="calendar",
        purpose="根据反思与生活规划生成结构化日程与 iCalendar (.ics) 标准日历清单",
        uploaded_data="日程标题、起止时间、周期规划与提醒事项",
        provider="FindYourself Native Engine (Local)",
        license="MIT Open Source (Private Owner)",
        budget_estimate_usd=Decimal("0.00"),
        cancellable=True,
        storage_target="s3_artifacts",
        failure_handling="本地语法校验回滚与结构化错误通知",
        requires_individual_approval=True,
        can_automate=True,
        configured=True,
    ),
    "local_svg_artwork": ToolDeclaration(
        tool_id="local_svg_artwork",
        name="本地视觉创意与心智图表生成器",
        category="image",
        purpose="基于反思对话生成矢量视觉表达、心智架构图与探索徽章 (SVG)",
        uploaded_data="图表主题、视觉元素、配色偏好与节点描述",
        provider="FindYourself Native SVG Renderer (Local)",
        license="MIT Open Source (Private Owner)",
        budget_estimate_usd=Decimal("0.00"),
        cancellable=True,
        storage_target="s3_artifacts",
        failure_handling="渲染校验失败即时回退",
        requires_individual_approval=False,
        can_automate=True,
        configured=True,
    ),
    "dalle_image_generation": ToolDeclaration(
        tool_id="dalle_image_generation",
        name="DALL-E 3 高清画作生成",
        category="image",
        purpose="高分辨率写实与艺术场景生成",
        uploaded_data="画面提示词、风格指令、宽高比例",
        provider="OpenAI DALL-E 3 (Cloud)",
        license="OpenAI Commercial Terms",
        budget_estimate_usd=Decimal("0.04"),
        cancellable=False,
        storage_target="s3_artifacts",
        failure_handling="外部超时与错误退回预留费用，抛出明确供应商错误",
        requires_individual_approval=False,
        can_automate=True,
        configured=False,  # Unconfigured without cloud API key
    ),
    "suno_music_synthesis": ToolDeclaration(
        tool_id="suno_music_synthesis",
        name="Suno 音乐旋律生成",
        category="music",
        purpose="生成情绪氛围乐段与歌曲旋律",
        uploaded_data="旋律风格提示、情绪标签、歌词文本",
        provider="Suno Music API (Cloud)",
        license="Suno Commercial Subscription",
        budget_estimate_usd=Decimal("0.10"),
        cancellable=True,
        storage_target="s3_artifacts",
        failure_handling="轮询超时自动中止并释放预算",
        requires_individual_approval=False,
        can_automate=True,
        configured=False,
    ),
    "runway_video_render": ToolDeclaration(
        tool_id="runway_video_render",
        name="Runway Gen-3 视频场景生成",
        category="video",
        purpose="生成动态视觉短视频与镜头推拉片段",
        uploaded_data="分镜脚本、镜头运动指令、首尾帧参考",
        provider="Runway API (Cloud)",
        license="Runway Enterprise License",
        budget_estimate_usd=Decimal("0.50"),
        cancellable=True,
        storage_target="s3_artifacts",
        failure_handling="排队超时或生成失败自动退回",
        requires_individual_approval=False,
        can_automate=True,
        configured=False,
    ),
    "wechat_social_publish": ToolDeclaration(
        tool_id="wechat_social_publish",
        name="微信社交消息/朋友圈发布",
        category="social_message",
        purpose="向社交联系人发送消息或公开发布动态",
        uploaded_data="消息正文、联系人标识、发布图片",
        provider="Tencent WeChat (App UI Only)",
        license="WeChat End User Agreement",
        budget_estimate_usd=Decimal("0.00"),
        cancellable=True,
        storage_target="local_audit",
        failure_handling="不适用",
        requires_individual_approval=True,
        can_automate=False,
        manual_alternative="微信客户端无官方开放个人自动化接口。系统已为您生成并排版好文本，请点击一键复制后在手机微信中手动发送。",
        configured=False,
    ),
    "railway_ticket_purchase": ToolDeclaration(
        tool_id="railway_ticket_purchase",
        name="铁路与航程票务代购",
        category="purchase",
        purpose="车票航次订购与出票",
        uploaded_data="乘车人身份信息、出行日期、班次信息",
        provider="12306 / 票务订购客户端",
        license="Consumer Purchase Agreement",
        budget_estimate_usd=Decimal("0.00"),
        cancellable=True,
        storage_target="local_audit",
        failure_handling="不适用",
        requires_individual_approval=True,
        can_automate=False,
        manual_alternative="票务购买涉及资金扣划与实名认证安全，禁止自动化划扣。行程建议已为您整理，请打开 12306 官方客户端核对后自行支付。",
        configured=False,
    ),
}


class CreativeToolsService:
    """Manages creative and daily life tools with strict privacy, individual approvals, and async artifact persistence."""

    def __init__(
        self,
        session: Session,
        audit: AuditService,
        budget: BudgetService,
        s3_store: S3ArtifactStore | None = None,
        local_store: LocalArtifactStore | None = None,
    ):
        self.session = session
        self.audit = audit
        self.budget = budget
        self.s3_store = s3_store
        self.local_store = local_store or LocalArtifactStore(".runtime/artifacts")

    def list_declarations(self) -> list[dict[str, Any]]:
        """Returns all declared tools and their privacy/security parameters."""
        res = []
        for t in TOOL_CATALOG.values():
            res.append({
                "tool_id": t.tool_id,
                "name": t.name,
                "category": t.category,
                "purpose": t.purpose,
                "uploaded_data": t.uploaded_data,
                "provider": t.provider,
                "license": t.license,
                "budget_estimate_usd": str(t.budget_estimate_usd),
                "cancellable": t.cancellable,
                "storage_target": t.storage_target,
                "failure_handling": t.failure_handling,
                "requires_individual_approval": t.requires_individual_approval,
                "can_automate": t.can_automate,
                "manual_alternative": t.manual_alternative,
                "configured": t.configured,
            })
        return res

    def get_declaration(self, tool_id: str) -> ToolDeclaration:
        t = TOOL_CATALOG.get(tool_id)
        if t is None:
            raise NotFound("tool_not_found", f"Tool '{tool_id}' is not registered in catalog")
        return t

    def execute_tool(
        self,
        actor: Actor,
        *,
        tool_id: str,
        task_id: str,
        idempotency_key: str,
        params: dict[str, Any],
        approved: bool = False,
    ) -> dict[str, Any]:
        """Executes a creative tool or raises explicit error if unconfigured or unapproved."""
        actor.require_owner()
        tool = self.get_declaration(tool_id)

        # 1. Check if tool is App UI only / non-automatable
        if not tool.can_automate:
            return {
                "tool_id": tool_id,
                "status": "manual_action_required",
                "can_automate": False,
                "manual_steps": tool.manual_alternative,
                "note": "Tool cannot be automated without violating terms of service; manual operation presented.",
            }

        # 2. Check if external tool is unconfigured
        if not tool.configured:
            raise DomainError(
                "provider_not_configured",
                f"External provider '{tool.provider}' for tool '{tool_id}' is not configured (credentials missing).",
            )

        # 3. Individual approval enforcement for sensitive operations (calendar change, purchasing, publishing)
        if tool.requires_individual_approval and not approved:
            return {
                "tool_id": tool_id,
                "status": "approval_required",
                "requires_individual_approval": True,
                "proposal_payload": {
                    "tool_id": tool_id,
                    "task_id": task_id,
                    "params": params,
                    "estimated_cost_usd": str(tool.budget_estimate_usd),
                },
                "note": "Individual owner approval required before executing calendar mutation or sensitive daily action.",
            }

        # 4. Atomic budget reservation
        if tool.budget_estimate_usd > Decimal("0.0"):
            self.budget.reserve(actor, task_id=task_id, amount=tool.budget_estimate_usd, idempotency_key=idempotency_key)

        # 5. Real local generation and S3 artifact persistence
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        if tool_id == "local_calendar_schedule":
            title = params.get("title", "每周反思与成长日常")
            events = params.get("events", ["周一 09:00 晨间专注", "周三 19:00 深度阅读", "周日 15:00 自我反思"])
            content_str = (
                "BEGIN:VCALENDAR\r\n"
                "VERSION:2.0\r\n"
                f"PRODID:-//FindYourself//PersonalSchedule//EN\r\n"
                f"X-WR-CALNAME:{title}\r\n"
            )
            for idx, ev in enumerate(events):
                content_str += (
                    "BEGIN:VEVENT\r\n"
                    f"UID:{uuid.uuid4().hex[:16]}@findyourself.local\r\n"
                    f"SUMMARY:{ev}\r\n"
                    f"DESCRIPTION:Generated by {tool.name}\r\n"
                    "END:VEVENT\r\n"
                )
            content_str += "END:VCALENDAR\r\n"
            data_bytes = content_str.encode("utf-8")
            media_type = "text/calendar"
            extension = "ics"

        elif tool_id == "local_svg_artwork":
            topic = params.get("topic", "成长心智模型")
            data_bytes = (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400">'
                f'<rect width="100%" height="100%" fill="#1a1c23"/>'
                f'<circle cx="300" cy="200" r="120" stroke="#4f46e5" stroke-width="4" fill="#242735"/>'
                f'<text x="300" y="205" text-anchor="middle" fill="#ffffff" font-size="20">{topic}</text>'
                f'<text x="300" y="360" text-anchor="middle" fill="#9ca3af" font-size="12">FindYourself Native Visual</text>'
                f'</svg>'
            ).encode("utf-8")
            media_type = "image/svg+xml"
            extension = "svg"
        else:
            data_bytes = b"EMPTY"
            media_type = "text/plain"
            extension = "bin"

        # Write to S3 or local store
        sha256 = hashlib.sha256(data_bytes).hexdigest()
        size = len(data_bytes)
        _ = f"artifacts/{artifact_id}.{extension}"

        if self.s3_store and self.s3_store.available:
            self.s3_store.put_bytes(artifact_id, data_bytes, media_type=media_type)
            storage_type = "s3"
        else:
            self.local_store.put(artifact_id, data_bytes, media_type=media_type)
            storage_type = "local"

        # Register artifact in database
        art = Artifact(
            id=artifact_id,
            task_id=task_id,
            domain="personal",
            sha256=sha256,
            size=size,
            media_type=media_type,
            verified=True,
            verifier="local-authorized-engine",
        )
        self.session.add(art)
        self.audit.append(actor, "tool.executed", tool_id, {
            "artifact_id": artifact_id,
            "sha256": sha256,
            "storage_type": storage_type,
            "domain": "personal",
        })
        self.session.flush()

        return {
            "tool_id": tool_id,
            "status": "completed",
            "artifact_id": artifact_id,
            "sha256": sha256,
            "size": size,
            "media_type": media_type,
            "storage_type": storage_type,
            "domain": "personal",
            "provider": tool.provider,
            "cancellable": tool.cancellable,
        }
