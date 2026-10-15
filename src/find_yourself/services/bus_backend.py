"""B8 总线插件化：统一总线接口 + 本地实现（N9 地基）。

* ``BusBackend`` 是跨 Agent 总线（④通信层）的**唯一契约**：
  publish / history / replay + backend_name。调用方只认协议，不认实现。
* 第一版只做本地实现（``LocalBusBackend``）：信封落**文件 JSONL**
  （append-only，零依赖、离线优先；重启可 replay；内存只做读索引）。
  房间名做 URL-quote 清洗后当文件名，跨平台安全。
* 远程 Redis 与热插拔留插槽：``create_bus_backend("redis")`` 诚实抛
  ``BackendUnavailable``（缺 ``FY_BUS_REDIS_URL``），绝不给假实现。
* 并发安全：同 backend 实例的发布串行化（并行分支同时 publish 不丢不乱）。

本模块只管**传输与留存**；身份（②谁在说话）由调用方经
``services.auth.ServiceIdentity`` 落定，内容鉴权仍走各路由既有门禁。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote
from uuid import uuid4


class BackendUnavailable(Exception):
    """请求的后端种类不可用（未配置 / 未实现）：调用方据此诚实降级。"""

    def __init__(self, message: str, *, code: str = "backend_unavailable"):
        super().__init__(message)
        self.code = code


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BusEnvelope:
    """总线统一信封（B6-① 适配翻译的目标形状）。

    ``meta`` 装协议特有 extras（如 A2A contextId），核心字段全协议一致。
    """

    id: str
    room: str
    sender: str
    kind: str
    content: str
    ts: str
    seq: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.room or not self.room.strip():
            raise ValueError("envelope.room 必须非空")
        if not self.content or not self.content.strip():
            raise ValueError("envelope.content 必须非空")


@runtime_checkable
class BusBackend(Protocol):
    """统一总线接口。实现方：本地文件（本模块）、Redis（插槽，后续）。"""

    @property
    def backend_name(self) -> str: ...

    def publish(self, room: str, *, sender: str, kind: str,
                content: str, meta: dict[str, Any] | None = None) -> BusEnvelope: ...
    def history(self, room: str, after_id: str = "",
                limit: int = 100) -> list[BusEnvelope]: ...
    def replay(self, room: str) -> list[BusEnvelope]: ...


class LocalBusBackend:
    """本地文件后端：``<room_dir>/<quoted-room>.jsonl`` append-only 留存。"""

    backend_name = "local"

    def __init__(self, room_dir: str | Path):
        self._dir = Path(room_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}

    def _path(self, room: str) -> Path:
        safe = quote(room, safe="")
        if not safe:
            raise ValueError("room 名为空")
        return self._dir / f"{safe}.jsonl"

    def _next_seq(self, room: str) -> int:
        if room not in self._seq:
            path = self._path(room)
            if not path.exists():
                self._seq[room] = 0
            else:
                count = 0
                with open(path, encoding="utf-8") as fh:
                    for _ in fh:
                        count += 1
                self._seq[room] = count
        self._seq[room] += 1
        return self._seq[room]

    def _read_all(self, room: str) -> list[BusEnvelope]:
        path = self._path(room)
        if not path.exists():
            return []
        out: list[BusEnvelope] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                out.append(BusEnvelope(
                    id=str(raw["id"]), room=str(raw["room"]),
                    sender=str(raw["sender"]), kind=str(raw["kind"]),
                    content=str(raw["content"]), ts=str(raw["ts"]),
                    seq=int(raw.get("seq", 0)),
                    meta=dict(raw.get("meta") or {}),
                ))
        return out

    def publish(self, room: str, *, sender: str, kind: str,
                content: str, meta: dict[str, Any] | None = None) -> BusEnvelope:
        env = BusEnvelope(id=uuid4().hex, room=room, sender=sender, kind=kind,
                          content=content, ts=_utcnow_iso(),
                          meta=dict(meta or {}))
        with self._lock:
            env = BusEnvelope(id=env.id, room=env.room, sender=env.sender,
                              kind=env.kind, content=env.content, ts=env.ts,
                              seq=self._next_seq(room), meta=env.meta)
            path = self._path(room)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(env), ensure_ascii=False) + "\n")
        return env

    def history(self, room: str, after_id: str = "",
                limit: int = 100) -> list[BusEnvelope]:
        with self._lock:
            all_msgs = self._read_all(room)
        if after_id:
            try:
                idx = next(i for i, m in enumerate(all_msgs) if m.id == after_id)
                all_msgs = all_msgs[idx + 1:]
            except StopIteration:
                return []
        return all_msgs[:max(1, int(limit))]

    def replay(self, room: str) -> list[BusEnvelope]:
        with self._lock:
            return self._read_all(room)


def create_bus_backend(kind: str, **kwargs: Any) -> BusBackend:
    """按种类建后端。``local`` 即刻可用；``redis`` 诚实不可用（插槽）。"""
    name = (kind or "").strip().lower()
    if name == "local":
        return LocalBusBackend(**kwargs)
    if name == "redis":
        raise BackendUnavailable(
            "redis backend 未接入：需部署 Redis 并配置 FY_BUS_REDIS_URL "
            "（B8 后续项，当前只有 local 实现）",
            code="redis_not_configured",
        )
    raise BackendUnavailable(f"未知的总线后端种类：{kind!r}", code="unknown_backend")
