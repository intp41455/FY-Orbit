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
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class TaskEvent:
    seq: int
    event_type: str
    data: dict

    def to_sse(self) -> str:
        import json
        payload = json.dumps(self.data, ensure_ascii=False, sort_keys=True, default=str)
        return f"id: {self.seq}\nevent: {self.event_type}\ndata: {payload}\n\n"


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

    async def subscribe(self, task_id: str, last_event_id: int = 0) -> AsyncIterator[TaskEvent]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subs[task_id].append(q)
        try:
            # Replay buffered events after the client's last seen id.
            for ev in list(self._hist.get(task_id, [])):
                if ev.seq > last_event_id:
                    yield ev
            while True:
                ev = await q.get()
                yield ev
        finally:
            async with self._lock:
                if q in self._subs.get(task_id, []):
                    self._subs[task_id].remove(q)


# Single shared bus for the running API process.
bus = TaskEventBus()
