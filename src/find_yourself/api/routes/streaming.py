"""P1-08 REST+SSE streaming endpoints (text/event-stream)."""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Callable

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...config import Settings
from ...runtime.sse import stamp_stream
from ...services.actor import Actor
from ...services.chat_orchestration import ChatOrchestrationService
from ...services.prompt import PromptService
from ...services.stream_persistence import get_segments, persist_stream
from ...services.streaming import StreamingService
from ..deps import Services, csrf_protected, get_actor, get_services, get_settings

router = APIRouter(prefix="/api/streaming", tags=["streaming"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


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
    #: 提问 ↔ trace 双向索引：同一次提问的所有 SSE 帧与审计帧共用该 id。
    #: 缺省时服务端生成，响应里的 ``message_id`` 即为权威值。
    message_id: str | None = None


def _stamped(gen: AsyncIterator[str], message_id: str,
             persist: Callable[[AsyncIterator[str]], AsyncIterator[str]] | None = None
             ) -> StreamingResponse:
    inner = stamp_stream(gen, message_id)
    if persist is not None:
        # T6-D（补 G3）：逐帧「先落库后吐出」——断流瞬间已吐出的每帧都保证在盘上
        inner = persist(inner)
    return StreamingResponse(
        inner,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat")
async def stream_chat(body: StreamChatRequest,
                      actor: Actor = Depends(csrf_protected),
                      svc: Services = Depends(get_services),
                      settings: Settings = Depends(get_settings)) -> StreamingResponse:
    # One id per question, shared by every SSE frame and the audit frame below.
    # A client-supplied id is honoured so a retry/reconnect keeps the mapping.
    message_id = body.message_id or uuid.uuid4().hex

    def _persist(inner: AsyncIterator[str]) -> AsyncIterator[str]:
        # T6-D：每帧先写 stream_segments（seq 递增）再吐出；异常（断流/断网/
        # 限流）→ 六类分类 → 中断台账 + 自动暂存已产出片段 + 审计挂帧。
        return persist_stream(
            inner, session=svc.session, audit=svc.audit, actor=actor,
            message_id=message_id, task_id=body.task_id or "chat-debug",
        )

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
        _audit_question(svc, actor, body, message_id, mode="orchestrated")
        return _stamped(gen, message_id, persist=_persist)

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
    _audit_question(svc, actor, body, message_id, mode="streaming")
    return _stamped(gen, message_id, persist=_persist)


@router.get("/{message_id}/segments")
def stream_segments(
    message_id: str,
    after_seq: int = 0,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """T6-D 断点取回（Last-Event-ID 语义的 REST 半边）。

    客户端断流/断网恢复后，凭 ``message_id`` 与已收到的最大 ``seq`` 取回
    其后的全部已落盘帧——不重复、不丢失。在线生成不重连到同一进程内
    生成器（诚实边界，见 services/stream_persistence.py 模块注释）。
    """
    rows = get_segments(svc.session, message_id, after_seq=after_seq)
    return {
        "message_id": message_id,
        "count": len(rows),
        "last_seq": rows[-1].seq if rows else after_seq,
        "segments": [{"seq": r.seq, "frame": r.frame} for r in rows],
    }


def _audit_question(svc: Services, actor: Actor, body: StreamChatRequest,
                    message_id: str, *, mode: str) -> None:
    """Record the question on the hash chain, tagged with ``message_id``.

    This is the write half of the bidirectional index: it lets the owner jump
    from a question to its audit frames (``frames_for_message``) and from a
    frame back to the question (``message_for_frame``). Only non-sensitive
    routing metadata is stored — never the prompt text.
    """
    svc.audit.append(
        actor,
        "chat.question",
        message_id,
        {"model": body.model, "mode": mode, "task_id": body.task_id},
        message_id=message_id,
    )
    svc.session.commit()
