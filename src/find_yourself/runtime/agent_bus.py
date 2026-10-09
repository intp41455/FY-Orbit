"""W7 · Agent 通信总线（进程内实现，接口兼容未来 Redis 后端）。

房间（room）模型
---------------
=================  =========================================================
``global``         广播；单租户语义下 = 本人可见
``<task_id>``      任务房间，沿用既有 task id（``runtime/sse.py`` 的 task 维度
                   事件流保持独立，本总线不改动它）
``<team_id>``      团队房间，沿用 ``team_definitions.id``
``dm:<a>:<b>``     私聊；``a``/``b`` 为身份键（``owner:<id>`` / ``agent:<role>``），
                   按字典序排序，保证「A 找 B」与「B 找 A」落在同一房间
=================  =========================================================

消息结构
--------
``{id, room, from_identity, kind, content, refs, at, mention}``

* ``id``       房间内单调递增序号，SSE 续传用它做 ``after_id`` / ``Last-Event-ID``
* ``kind``     ``text`` | ``file_ref`` | ``handoff`` | ``system``
* ``refs``     共享上下文条目 id（``bus_context`` 表，见 migration 0022）
* ``mention``  被点名的身份（``agent:<role>``），决定 Agent 自动回复与连线徽标
* ``at``       UTC ISO-8601

后端替换说明（重要）
--------------------
本模块只暴露三个原语 —— :meth:`AgentBus.publish` / :meth:`AgentBus.history` /
:meth:`AgentBus.subscribe`。换成 Redis 时三者分别落到 ``XADD`` / ``XRANGE`` /
``XREAD BLOCK``，``services/bus_service.py`` 与 HTTP 层无需改动。当前实现是
**进程内**的：历史为每房间 500 条环形缓冲，**进程重启即丢失**，这是 v1 的明确
范围，不冒充持久化。房间附件（共享上下文）走 ``bus_context`` 表落库。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: 每房间保留的消息条数（超出后最旧的被丢弃）。
HISTORY_LIMIT = 500

#: 身份前缀。``from_identity`` 只能由服务端从 :class:`Actor` 推导。
OWNER_PREFIX = "owner:"
AGENT_PREFIX = "agent:"
SYSTEM_IDENTITY = "system"

#: 消息种类。``system`` 只能由服务端生成（服务身份或内部触发通道）。
MESSAGE_KINDS = ("text", "file_ref", "handoff", "system")

DM_PREFIX = "dm:"
GLOBAL_ROOM = "global"


def dm_room(a: str, b: str) -> str:
    """私聊房间键：两端身份按字典序排序，保证双向同一房间。"""
    lo, hi = sorted([str(a), str(b)])
    return f"{DM_PREFIX}{lo}:{hi}"


def identity_key(identity: str) -> str:
    """剥掉身份前缀，得到可显示/可比对的名字（``agent:coder`` → ``coder``）。"""
    raw = str(identity or "")
    for prefix in (OWNER_PREFIX, AGENT_PREFIX):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BusMessage:
    id: int
    room: str
    from_identity: str
    kind: str
    content: str
    refs: list[str] = field(default_factory=list)
    at: str = ""
    mention: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "room": self.room,
            "from_identity": self.from_identity,
            "kind": self.kind,
            "content": self.content,
            "refs": list(self.refs),
            "at": self.at,
            "mention": self.mention,
        }

    def to_sse(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        return f"id: {self.id}\nevent: message\ndata: {payload}\n\n"


class AgentBus:
    """进程内发布/订阅总线。线程安全写入，异步订阅。

    并发写入用 ``threading.Lock``（照 ``services/memory.py`` 的 ``_cache_lock``
    惯例）；订阅队列在每个订阅者自己的 asyncio 队列上扇出，队列满时丢弃而不是
    阻塞发布者。
    """

    def __init__(self, history_limit: int = HISTORY_LIMIT):
        self.history_limit = int(history_limit)
        self._hist: dict[str, deque[BusMessage]] = defaultdict(lambda: deque(maxlen=self.history_limit))
        self._seq: dict[str, itertools.count] = defaultdict(itertools.count)
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ write
    def publish(
        self,
        room: str,
        *,
        from_identity: str,
        kind: str,
        content: str,
        refs: list[str] | None = None,
        mention: str | None = None,
    ) -> BusMessage:
        """追加一条消息并返回它（``id`` 与 ``at`` 由服务端生成）。"""
        if kind not in MESSAGE_KINDS:
            raise ValueError(f"unknown message kind: {kind}")
        with self._lock:
            msg = BusMessage(
                id=next(self._seq[room]) + 1,
                room=room,
                from_identity=from_identity,
                kind=kind,
                content=content,
                refs=[str(r) for r in (refs or [])],
                at=now_iso(),
                mention=mention,
            )
            self._hist[room].append(msg)
            queues = list(self._subs.get(room, []))
        for q in queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # A slow subscriber must never stall the writer.
                pass
        return msg

    # ------------------------------------------------------------------- read
    def history(self, room: str, after_id: int = 0, limit: int = HISTORY_LIMIT) -> list[BusMessage]:
        with self._lock:
            buf = list(self._hist.get(room, ()))
        out = [m for m in buf if m.id > int(after_id)]
        if limit and len(out) > limit:
            out = out[:limit]
        return out

    def last_id(self, room: str) -> int:
        with self._lock:
            buf = self._hist.get(room)
            return buf[-1].id if buf else 0

    def rooms(self) -> list[str]:
        with self._lock:
            return sorted(self._hist.keys())

    async def subscribe(self, room: str, after_id: int = 0) -> AsyncIterator[BusMessage]:
        """订阅房间；先补发 ``after_id`` 之后的历史，再推送实时消息。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs[room].append(q)
        try:
            for msg in self.history(room, after_id=after_id):
                yield msg
            while True:
                yield await q.get()
        finally:
            with self._lock:
                subs = self._subs.get(room)
                if subs and q in subs:
                    subs.remove(q)


#: 进程内共享总线（与 ``runtime/sse.py`` 的 task 事件总线彼此独立）。
bus = AgentBus()
