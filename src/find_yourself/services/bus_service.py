"""W7 · Agent 通信总线服务层：房间可见性、身份派生与 Agent 自动参与。

三条硬规则（对应任务书 §1.2 / §3）：

1. **``from_identity`` 不可伪造**。它只由服务端从 :class:`Actor` 推导
   （``owner:<owner_id>`` / ``agent:<service_id>``），HTTP 层根本不接收这个字段。
   Agent 回复由服务端触发通道以 ``agent:<role>`` 身份发布 —— 同样是服务端
   推导（来自团队成员表），不是请求体。
2. **房间可见性由服务端判定**：任务房间限任务 owner，团队房间限团队 owner，
   dm 限双方，``global`` 限 owner 身份。越权统一 ``NotFound``（不泄露房间是否
   存在）或 ``PermissionDenied``。
3. **诚实**：无模型凭据时，Agent 回复位发一条 ``system`` 消息说明
   「模型未配置，无法回应」，绝不编造回答；后台任务失败也在房间内留痕。

后台触发不阻塞发消息请求：``send()`` 只把回复协程交给事件循环（无事件循环时
退化为 ``pending``，供测试 ``await drain()`` 确定性执行）。回复协程自己开一个
**独立 DB session**（请求 session 会在响应后关闭，不能跨请求借用）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from ..db.models import Task
from ..db.team_models import BUS_CONTEXT_KINDS, BusContextEntry, TeamDefinition
from ..db.types import utcnow
from ..runtime.agent_bus import (
    AGENT_PREFIX,
    MESSAGE_KINDS,
    OWNER_PREFIX,
    SYSTEM_IDENTITY,
    AgentBus,
    identity_key,
)
from ..runtime.agent_bus import (
    bus as default_bus,
)
from ..runtime.gateway import ModelNotConfigured
from .actor import Actor
from .agent_teams import AgentTeamService
from .errors import DomainError, NotFound, PermissionDenied, ValidationFailed

#: 单条消息正文上限（超出明确 422，不静默截断）。
MAX_CONTENT_LEN = 8000
#: 单条消息最多引用多少个共享上下文条目。
MAX_REFS = 20
#: 一次登记共享上下文的条目上限。
MAX_CONTEXT_ENTRIES = 20
MAX_CONTEXT_REF_LEN = 2000
MAX_CONTEXT_CONTENT_LEN = 8000

#: 房间种类。
ROOM_GLOBAL = "global"
ROOM_TASK = "task"
ROOM_TEAM = "team"
ROOM_DM = "dm"

#: 上下文条目 id 前缀；消息 refs 里以它开头的引用必须真实存在于本房间。
CONTEXT_ID_PREFIX = "ctx-"

_MENTION_TOKEN = re.compile(r"@([^\s@,，。;；:：]+)")


def identity_of(actor: Actor) -> str:
    """从 Actor 推导总线身份 —— 客户端永远无法指定它。"""
    if actor.subject_type == "owner":
        return f"{OWNER_PREFIX}{actor.owner_id}"
    return f"{AGENT_PREFIX}{actor.service_id or actor.service_kind or 'unknown'}"


@dataclass(frozen=True)
class RoomRef:
    """解析后的房间：种类 + 授权所需的全部服务端事实。"""

    room: str
    kind: str
    owner_id: str = ""
    task_id: str | None = None
    team_id: str | None = None
    parties: tuple[str, ...] = ()


class AgentBusService:
    def __init__(
        self,
        session: Any,
        *,
        teams: AgentTeamService | None = None,
        teams_factory: Callable[[Any], AgentTeamService] | None = None,
        session_maker: Callable[[], Any] | None = None,
        bus: AgentBus | None = None,
        auto_reply: bool = True,
        background: bool = True,
    ):
        self.session = session
        self._teams = teams
        self._teams_factory = teams_factory
        self._session_maker = session_maker
        self.bus = bus if bus is not None else default_bus
        self.auto_reply = auto_reply
        #: True = 交给事件循环后台执行；False = 挂到 pending 供测试确定性执行。
        self.background = background
        self._pending: list[Any] = []

    # ------------------------------------------------------------------ rooms
    def resolve_room(self, actor: Actor, room: str) -> RoomRef:
        """解析房间并校验可见性。越权一律 ``NotFound``/``PermissionDenied``。"""
        actor.require_authenticated()
        room = str(room or "").strip()
        if not room:
            raise ValidationFailed("bus_room_required", "Room is required")

        if room == ROOM_GLOBAL:
            # 广播：单租户语义 = 本人。服务身份不参与广播。
            if actor.subject_type != "owner":
                raise PermissionDenied(
                    "bus_room_forbidden", "Broadcast room 'global' is limited to owner sessions", 403
                )
            return RoomRef(room=room, kind=ROOM_GLOBAL, owner_id=actor.owner_id)

        if room.startswith("dm:"):
            parties = _parse_dm_parties(room[3:])
            me = identity_of(actor)
            if me not in parties:
                raise PermissionDenied(
                    "bus_room_forbidden", "This dm room does not include your identity", 403
                )
            return RoomRef(room=room, kind=ROOM_DM, owner_id=actor.owner_id, parties=parties)

        # 任务房间：沿用既有 task id。
        task = self.session.get(Task, room)
        if task is not None:
            if actor.subject_type == "owner":
                if task.owner_id != actor.owner_id:
                    raise NotFound("bus_room_not_found", f"No accessible bus room: {room}")
            elif actor.bound_task_id != task.id:
                raise PermissionDenied(
                    "bus_room_forbidden",
                    "Service credential is not bound to this task room", 403,
                )
            return RoomRef(room=room, kind=ROOM_TASK, owner_id=task.owner_id, task_id=task.id)

        # 团队房间：team_definitions.id。
        team = self.session.get(TeamDefinition, room)
        if team is not None:
            if actor.subject_type == "owner":
                if team.owner_id != actor.owner_id:
                    raise NotFound("bus_room_not_found", f"No accessible bus room: {room}")
            elif actor.bound_task_id != team.root_task_id:
                raise PermissionDenied(
                    "bus_room_forbidden",
                    "Service credential is not bound to this team's root task", 403,
                )
            return RoomRef(room=room, kind=ROOM_TEAM, owner_id=team.owner_id, team_id=team.id)

        raise NotFound("bus_room_not_found", f"No accessible bus room: {room}")

    # -------------------------------------------------------------- messages
    def send(
        self,
        actor: Actor,
        room: str,
        *,
        content: str,
        kind: str = "text",
        refs: list[str] | None = None,
        mention: str | None = None,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        """发一条消息。``from_identity`` 由 Actor 推导，不接受请求体传入。"""
        ref = self.resolve_room(actor, room)
        if kind not in MESSAGE_KINDS:
            raise ValidationFailed("bus_bad_kind", f"Unsupported message kind: {kind}")
        if kind == "system" and actor.subject_type != "service":
            # 系统消息只能来自服务身份或内部触发通道，owner 不能冒充系统发言。
            raise PermissionDenied(
                "bus_system_requires_service", "Only a service identity may publish system messages", 403
            )
        content = str(content or "")
        if not content.strip():
            raise ValidationFailed("bus_content_required", "Message content must not be empty")
        if len(content) > MAX_CONTENT_LEN:
            raise ValidationFailed(
                "bus_content_too_long",
                f"Message content exceeds {MAX_CONTENT_LEN} characters; refusing to truncate silently",
            )

        refs = self._validate_refs(actor, ref, refs)

        from_identity = identity_of(actor)
        resolved_mention, role = self._resolve_mention(ref, mention, content)

        msg = self.bus.publish(
            ref.room, from_identity=from_identity, kind=kind,
            content=content, refs=refs, mention=resolved_mention,
        )

        triggered: list[str] = []
        scheduled = False
        if role and self.auto_reply and ref.kind == ROOM_TEAM:
            triggered.append(role)
            scheduled = self._schedule_reply(
                room=ref.room, team_id=ref.team_id or "", role=role,
                owner_id=ref.owner_id, prompt=content, mention=resolved_mention,
                from_identity=from_identity, max_tokens=max_tokens,
            )

        return {
            "message": msg.to_dict(),
            "triggered": triggered,
            "scheduled": scheduled,
            "auto_reply_enabled": self.auto_reply,
        }

    def list_messages(
        self, actor: Actor, room: str, *, after_id: int = 0, limit: int = 500
    ) -> dict[str, Any]:
        ref = self.resolve_room(actor, room)
        items = self.bus.history(ref.room, after_id=int(after_id or 0), limit=int(limit or 0))
        return {
            "room": ref.room,
            "kind": ref.kind,
            "items": [m.to_dict() for m in items],
            "count": len(items),
            "next_after_id": items[-1].id if items else int(after_id or 0),
        }

    def handoffs(self, actor: Actor, room: str) -> dict[str, Any]:
        """连线徽标计数：``handoff`` 消息按 发送方 → 被点名方 聚合。"""
        ref = self.resolve_room(actor, room)
        edges: dict[str, int] = {}
        for m in self.bus.history(ref.room):
            if m.kind != "handoff" or not m.mention:
                continue
            key = f"{identity_key(m.from_identity)}>{identity_key(m.mention)}"
            edges[key] = edges.get(key, 0) + 1
        return {"room": ref.room, "kind": ref.kind, "edges": edges, "total": sum(edges.values())}

    # --------------------------------------------------------------- context
    def add_context(self, actor: Actor, room: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        ref = self.resolve_room(actor, room)
        if not isinstance(entries, list) or not entries:
            raise ValidationFailed("bus_context_required", "At least one context entry is required")
        if len(entries) > MAX_CONTEXT_ENTRIES:
            raise ValidationFailed(
                "bus_context_too_many", f"At most {MAX_CONTEXT_ENTRIES} entries per request"
            )
        added: list[dict[str, Any]] = []
        for raw in entries:
            if not isinstance(raw, dict):
                raise ValidationFailed("bus_context_bad_entry", "Each context entry must be an object")
            kind = str(raw.get("kind") or "text")
            if kind not in BUS_CONTEXT_KINDS:
                raise ValidationFailed("bus_context_bad_kind", f"Unsupported context kind: {kind}")
            ref_value = str(raw.get("ref") or "")
            body = str(raw.get("content") or "")
            if len(ref_value) > MAX_CONTEXT_REF_LEN:
                raise ValidationFailed("bus_context_ref_too_long", "Context reference is too long")
            if len(body) > MAX_CONTEXT_CONTENT_LEN:
                raise ValidationFailed(
                    "bus_context_content_too_long",
                    f"Context text exceeds {MAX_CONTEXT_CONTENT_LEN} characters",
                )
            if kind == "file_ref" and not ref_value.strip():
                raise ValidationFailed("bus_context_ref_required", "file_ref entries need a 'ref' target")
            row = BusContextEntry(
                id=f"{CONTEXT_ID_PREFIX}{uuid4().hex[:16]}",
                owner_id=ref.owner_id,
                room=ref.room,
                kind=kind,
                title=str(raw.get("title") or "")[:200],
                ref=ref_value,
                content=body,
                added_by=identity_of(actor),
                created_at=utcnow(),
            )
            self.session.add(row)
            added.append(row)
        self.session.flush()
        return {"room": ref.room, "kind": ref.kind, "items": [self._context_view(r) for r in added],
                "count": len(added)}

    def list_context(self, actor: Actor, room: str) -> dict[str, Any]:
        ref = self.resolve_room(actor, room)
        rows = self.session.execute(
            select(BusContextEntry)
            .where(BusContextEntry.owner_id == ref.owner_id, BusContextEntry.room == ref.room)
            .order_by(BusContextEntry.created_at.asc())
        ).scalars().all()
        return {"room": ref.room, "kind": ref.kind,
                "items": [self._context_view(r) for r in rows], "count": len(rows)}

    @staticmethod
    def _context_view(row: BusContextEntry) -> dict[str, Any]:
        return {
            "id": row.id,
            "room": row.room,
            "kind": row.kind,
            "title": row.title,
            "ref": row.ref,
            "content": row.content,
            "added_by": row.added_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    # -------------------------------------------------------------- internals
    def _validate_refs(self, actor: Actor, ref: RoomRef, refs: list[str] | None) -> list[str]:
        if refs is None:
            return []
        if not isinstance(refs, list):
            raise ValidationFailed("bus_bad_refs", "refs must be a list of context ids")
        if len(refs) > MAX_REFS:
            raise ValidationFailed("bus_too_many_refs", f"At most {MAX_REFS} refs per message")
        out: list[str] = []
        for r in refs:
            token = str(r or "").strip()
            if not token:
                raise ValidationFailed("bus_bad_refs", "refs must not contain empty values")
            if len(token) > 200:
                raise ValidationFailed("bus_bad_refs", "ref is too long")
            if token.startswith(CONTEXT_ID_PREFIX):
                # 引用必须真实存在且属于本房间 —— 不允许悬空引用假装上下文齐备。
                row = self.session.get(BusContextEntry, token)
                if row is None or row.room != ref.room or row.owner_id != ref.owner_id:
                    raise ValidationFailed(
                        "bus_unknown_context_ref", f"Unknown context entry for this room: {token}"
                    )
            out.append(token)
        return out

    def _resolve_mention(
        self, ref: RoomRef, mention: str | None, content: str
    ) -> tuple[str | None, str | None]:
        """返回 ``(规范化 mention, 解析出的团队角色 or None)``。

        团队房间里，显式 ``mention`` 或正文里的 ``@角色/@职务`` 都会解析成成员
        角色；解析不到就明确报错，不静默忽略（否则用户以为 @ 了却没人回）。
        """
        if ref.kind != ROOM_TEAM:
            return (str(mention).strip() or None) if mention else None, None
        team = self.session.get(TeamDefinition, ref.team_id)
        members = list((team.members if team else None) or [])
        if not members:
            return (str(mention).strip() or None) if mention else None, None

        token = str(mention or "").strip()
        explicit = bool(token)
        if not token:
            found = _MENTION_TOKEN.search(content or "")
            token = found.group(1) if found else ""
        if not token:
            return None, None

        role = _match_member(members, token)
        if role is None:
            if explicit:
                raise ValidationFailed(
                    "bus_unknown_mention",
                    f"'{token}' is not a member role or title of this team",
                )
            # 正文里的 @ 未必是点名（可能是邮箱等），不解析就不触发。
            return None, None
        return f"{AGENT_PREFIX}{role}", role

    # ------------------------------------------------------------- agent loop
    def _schedule_reply(
        self, *, room: str, team_id: str, role: str, owner_id: str, prompt: str,
        mention: str | None, from_identity: str, max_tokens: int,
    ) -> bool:
        coro = self._reply_task(
            room=room, team_id=team_id, role=role, owner_id=owner_id,
            prompt=prompt, mention=mention, from_identity=from_identity,
            max_tokens=max_tokens,
        )
        if self.background:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(coro)
                return True
        self._pending.append(coro)
        return False

    async def drain(self) -> list[Any]:
        """确定性执行挂起的回复协程（测试用；生产走事件循环）。"""
        pending, self._pending = self._pending, []
        out = []
        for coro in pending:
            out.append(await coro)
        return out

    async def _reply_task(self, **kw: Any) -> dict[str, Any] | None:
        try:
            # 真模型调用是同步阻塞的；放到线程里，避免卡住事件循环。
            return await asyncio.to_thread(self._run_reply, **kw)
        except Exception as exc:  # pragma: no cover - 兜底，绝不让后台任务静默消失
            self.bus.publish(
                kw["room"], from_identity=SYSTEM_IDENTITY, kind="system",
                content=f"[agent_reply_failed] {type(exc).__name__}: {exc}",
                mention=f"{AGENT_PREFIX}{kw['role']}",
            )
            return None

    def _run_reply(
        self, *, room: str, team_id: str, role: str, owner_id: str, prompt: str,
        mention: str | None, from_identity: str, max_tokens: int,
    ) -> dict[str, Any] | None:
        """经 ``services/agent_teams.py`` 的真实执行通道让成员作答。

        任何失败都在房间内留下 ``system`` 消息，绝不伪造回答。
        """
        def fail(code: str, why: str) -> None:
            self.bus.publish(
                room, from_identity=SYSTEM_IDENTITY, kind="system",
                content=f"[{code}] {why}", mention=f"{AGENT_PREFIX}{role}",
            )

        if self._session_maker is None and self._teams is None:
            fail("bus_no_session_factory", "后台回复未执行：没有可用的独立会话工厂。")
            return None

        session = None
        try:
            if self._teams is not None:
                teams = self._teams
            else:
                session = self._session_maker()
                factory = self._teams_factory
                teams = factory(session) if factory else AgentTeamService(session)
            # 触发者是 owner 本人的会话权威；消息身份仍是服务端推导的 agent:<role>。
            actor = Actor.owner(owner_id)
            try:
                result = teams.execute_member(
                    actor, team_id, role, prompt=prompt, max_tokens=int(max_tokens)
                )
            except ModelNotConfigured as exc:
                fail(
                    exc.code,
                    "模型未配置，无法回应。配置模型凭据后 Agent 才能真实作答；"
                    "此处不生成任何冒充回答的内容。",
                )
                return None
            except DomainError as exc:
                fail(exc.code, str(exc) or exc.code)
                return None
            text = str(result.get("text") or "").strip()
            if not text:
                fail("bus_empty_reply", "执行通道返回空内容；不冒充为有效回答。")
                return None
            msg = self.bus.publish(
                room, from_identity=f"{AGENT_PREFIX}{role}", kind="text",
                content=text[:MAX_CONTENT_LEN], mention=f"{OWNER_PREFIX}{owner_id}",
            )
            return msg.to_dict()
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:  # pragma: no cover
                    pass


def _parse_dm_parties(rest: str) -> tuple[str, str]:
    """``owner:o1`` + ``agent:coder`` → ``dm:agent:coder:owner:o1``。

    身份本身带一个冒号（``owner:`` / ``agent:``），所以一段 dm 房间名切成 4 段；
    两个无前缀身份则切成 2 段。两种都接受，其余一律报 422。
    """
    parts = [p for p in str(rest or "").split(":")]
    if len(parts) == 2 and all(parts):
        return (parts[0], parts[1])
    if len(parts) == 4 and all(parts):
        return (f"{parts[0]}:{parts[1]}", f"{parts[2]}:{parts[3]}")
    raise ValidationFailed(
        "bus_bad_dm_room", "A dm room must be 'dm:<identity_a>:<identity_b>'"
    )


def _match_member(members: list[dict[str, Any]], token: str) -> str | None:
    """按角色名或职务名匹配团队成员（大小写不敏感，忽略空白）。"""
    raw = str(token or "").strip()
    raw = raw.removeprefix(AGENT_PREFIX)
    low = raw.lower()
    if not low:
        return None
    for m in members:
        if str(m.get("role") or "").lower() == low:
            return str(m.get("role"))
    for m in members:
        if str(m.get("title") or "").strip().lower() == low:
            return str(m.get("role"))
    return None
