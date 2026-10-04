"""P1-08 REST+SSE streaming endpoints (text/event-stream)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import Services, get_services, get_settings, csrf_protected
from ...config import Settings
from ...services.actor import Actor
from ...services.chat_orchestration import ChatOrchestrationService
from ...services.prompt import PromptService
from ...services.streaming import StreamingService

router = APIRouter(prefix="/api/streaming", tags=["streaming"])


class StreamChatRequest(BaseModel):
    model: str = "default"
    prompt: str
    task_id: str | None = None
    max_tokens: int = 1024
    # P1-19 Chat 调试预览: optional template binding + tool binding. When
    # either is present the orchestrated (template + tools + SSE) path runs;
    # otherwise the plain P1-08 path is preserved unchanged.
    template_name: str | None = None
    template_version: int | None = None
    variables: dict = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)


@router.post("/chat")
async def stream_chat(body: StreamChatRequest,
                      actor: Actor = Depends(csrf_protected),
                      svc: Services = Depends(get_services),
                      settings: Settings = Depends(get_settings)) -> StreamingResponse:
    # P1-19 orchestrated path: template render + tool manifest + tool-call
    # detection with REAL tool execution and continuation streaming.
    if body.template_name or body.tools:
        template_meta: dict | None = None
        system_prefix = ""
        if body.template_name:
            rendered = PromptService(svc.session, svc.audit).render(
                body.template_name,
                body.variables,
                version=body.template_version,
                task_id=body.task_id,
                scope="chat-debug",
            )
            svc.session.commit()
            system_prefix = rendered.text
            template_meta = {
                "name": rendered.name,
                "version": rendered.version,
                "content_hash": rendered.content_hash,
                "variables_hash": rendered.variables_hash,
            }
        service = ChatOrchestrationService(settings, budget=svc.budget)
        service.resolve_mode()
        tools = service.bind_tools(body.tools)
        gen = service.stream_chat(
            actor,
            prompt=system_prefix,
            user_message=body.prompt,
            model=body.model,
            task_id=body.task_id or "chat-debug",
            tools=tools,
            template_meta=template_meta,
            max_tokens=body.max_tokens,
        )
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    service = StreamingService(settings, budget=svc.budget)
    # Resolve the data mode before committing the 200 so an unconfigured
    # provider surfaces as the unified MODEL_NOT_CONFIGURED envelope (503).
    service.resolve_mode()
    gen = service.stream_chat(
        actor,
        prompt=body.prompt,
        model=body.model,
        task_id=body.task_id or "adhoc",
        max_tokens=body.max_tokens,
    )
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
