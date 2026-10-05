"""In-process task event bus for SSE (FROZEN_CONTRACT §5.2).

``GET /api/tasks/{id}/events`` streams stage/state/reference/redacted-artifact
events to connected owner clients. The bus carries only references and
non-sensitive state — never prompts, hidden reasoning, keys or raw content.
Reconnect support is provided via ``Last-Event-ID`` resuming from a small
per-task ring buffer. Durable history lives in Temporal (Workflow shard); this
is the live fan-out.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class TaskEvent:
    seq: int
    event_type: str
    data: dict

    def to_sse(self) -> str:
        if self.event_type == ": heartbeat" or (
            self.event_type == "heartbeat" and (not self.data or self.data.get("heartbeat"))
        ):
            return ": heartbeat\n\n"
        import json
        payload = json.dumps(self.data, ensure_ascii=False, sort_keys=True, default=str)
        return f"id: {self.seq}\nevent: {self.event_type}\ndata: {payload}\n\n"


def inject_message_id(frame: str, message_id: str) -> str:
    """Return ``frame`` with ``message_id`` merged into its JSON ``data`` field.

    Used to attribute every SSE frame of one question to the same
    ``message_id`` so the client can group a received stream and jump from a
    question to its trace (and back). Comment/heartbeat frames (``: ...``) and
    frames without a ``data:`` line are passed through untouched.
    """
    import json

    if not frame or frame.startswith(":"):
        return frame
    out_lines: list[str] = []
    for line in frame.split("\n"):
        if not line.startswith("data:"):
            out_lines.append(line)
            continue
        raw = line[len("data:"):].strip()
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            out_lines.append(line)
            continue
        if isinstance(obj, dict):
            obj.setdefault("message_id", message_id)
        out_lines.append("data: " + json.dumps(obj, ensure_ascii=False))
    return "\n".join(out_lines)


async def stamp_stream(frames: AsyncIterator[str], message_id: str) -> AsyncIterator[str]:
    """Yield ``frames`` with ``message_id`` stamped onto every data frame."""
    async for frame in frames:
        yield inject_message_id(frame, message_id)


class TaskEventBus:
    def __init__(self, history: int = 100):
        self._hist: dict[str, deque[TaskEvent]] = defaultdict(lambda: deque(maxlen=history))
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._counters: dict[str, itertools.count] = defaultdict(itertools.count)
        self._lock = asyncio.Lock()

    def publish(self, task_id: str, event_type: str, data: dict) -> TaskEvent:
        seq = next(self._counters[task_id]) + 1
        ev = TaskEvent(seq=seq, event_type=event_type, data=data)
        self._hist[task_id].append(ev)
        for q in list(self._subs.get(task_id, [])):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return ev

    async def subscribe(
        self,
        task_id: str,
        last_event_id: int = 0,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[TaskEvent]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subs[task_id].append(q)
        try:
            hist = list(self._hist.get(task_id, []))
            # GAP 帧：若客户端提供了 last_event_id，且缓冲非空，
            # 当 last_event_id < min_seq - 1 时，说明中间有事件已溢出丢弃，
            # 必须先发一帧 event: gap 提示客户端全量重拉。
            if last_event_id > 0 and hist:
                min_seq = hist[0].seq
                if last_event_id < min_seq - 1:
                    yield TaskEvent(
                        seq=min_seq,
                        event_type="gap",
                        data={"gap": True, "min_seq": min_seq, "last_seen": last_event_id},
                    )

            # Replay buffered events after the client's last seen id.
            for ev in hist:
                if ev.seq > last_event_id:
                    yield ev
            while True:
                try:
                    if heartbeat_interval and heartbeat_interval > 0:
                        ev = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
                    else:
                        ev = await q.get()
                    yield ev
                except asyncio.TimeoutError:
                    yield TaskEvent(seq=0, event_type=": heartbeat", data={"heartbeat": True})
        finally:
            async with self._lock:
                if q in self._subs.get(task_id, []):
                    self._subs[task_id].remove(q)


# Single shared bus for the running API process.
bus = TaskEventBus()
