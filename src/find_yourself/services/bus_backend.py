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
    """按种类建后端。

    * ``"local"``：文件 JSONL（离线优先，永远可用）；
    * ``"redis"``：必须可达，否则 ``BackendUnavailable``（不静默降级）；
    * ``"auto"``（缺省推荐）：``FY_BUS_REDIS_URL`` 有值且 ping 通用 redis，
      否则回落 local——单机开箱零依赖，有基建自动升级。
    * ``_client``：测试注入传输；生产勿传（走 ``build_redis_client``）。
    """
    import os

    name = (kind or "").strip().lower()
    if name == "local":
        return LocalBusBackend(**{k: v for k, v in kwargs.items()
                                   if not k.startswith("_")})
    if name == "redis":
        return _redis_or_raise(url=kwargs.get("url"), client=kwargs.get("_client"),
                               timeout_seconds=float(kwargs.get("timeout_seconds", 2.0)))
    if name == "auto":
        url = kwargs.get("url") or os.environ.get("FY_BUS_REDIS_URL", "")
        if url:
            try:
                return _redis_or_raise(url=url, client=kwargs.get("_client"),
                                       timeout_seconds=float(
                                           kwargs.get("timeout_seconds", 2.0)))
            except BackendUnavailable:
                pass  # auto 档回落 local（offline-first），不抛错
        return LocalBusBackend(**{k: v for k, v in kwargs.items()
                                   if not k.startswith("_")})
    raise BackendUnavailable(f"未知的总线后端种类：{kind!r}", code="unknown_backend")


def _redis_or_raise(*, url: Any, client: Any, timeout_seconds: float) -> BusBackend:
    if client is not None:
        try:
            client.ping()
        except Exception as exc:
            raise BackendUnavailable(
                f"Redis 传输 ping 不通：{type(exc).__name__}",
                code="redis_unreachable",
            ) from exc
        return RedisBusBackend(client=client)
    if not url:
        raise BackendUnavailable(
            "redis 后端需要 URL（参数 url 或 FY_BUS_REDIS_URL）",
            code="redis_not_configured",
        )
    return RedisBusBackend(client=build_redis_client(str(url), timeout_seconds))


class RedisBusBackend:
    """Redis List 后端（与 LocalBusBackend 同一协议；B8 双模式之远程档）。

    每房间一个 List（``bus:room:<quoted>``），publish = RPUSH，
    history/replay = LRANGE。``client`` 须提供 ``rpush / lrange / ping``：
    测试注内存 fake，生产传 ``redis.Redis``（``build_redis_client`` 懒加载
    ``redis`` 包——pyproject 不硬依赖，无 server 时不装包也能跑 local）。
    与 LocalBusBackend 同模块，避免两模块互指成环（N7 门禁）。
    """

    backend_name = "redis"

    def __init__(self, client: Any, *, key_prefix: str = "bus:room:"):
        self._client = client
        self._prefix = key_prefix

    def _key(self, room: str) -> str:
        safe = quote(room, safe="")
        if not safe:
            raise ValueError("room 名为空")
        return f"{self._prefix}{safe}"

    def _decode(self, raw: Any) -> BusEnvelope:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        data = json.loads(str(raw))
        return BusEnvelope(
            id=str(data["id"]), room=str(data["room"]),
            sender=str(data["sender"]), kind=str(data["kind"]),
            content=str(data["content"]), ts=str(data["ts"]),
            seq=int(data.get("seq", 0)),
            meta=dict(data.get("meta") or {}),
        )

    def _read_all(self, room: str) -> list[BusEnvelope]:
        raws = self._client.lrange(self._key(room), 0, -1) or []
        return [self._decode(r) for r in raws]

    def publish(self, room: str, *, sender: str, kind: str,
                content: str, meta: dict[str, Any] | None = None) -> BusEnvelope:
        env = BusEnvelope(id=uuid4().hex, room=room, sender=sender, kind=kind,
                          content=content, ts=_utcnow_iso(),
                          meta=dict(meta or {}))
        current = self._read_all(room)
        env = BusEnvelope(id=env.id, room=env.room, sender=env.sender,
                          kind=env.kind, content=env.content, ts=env.ts,
                          seq=len(current) + 1, meta=env.meta)
        self._client.rpush(self._key(room), json.dumps({
            "id": env.id, "room": env.room, "sender": env.sender,
            "kind": env.kind, "content": env.content, "ts": env.ts,
            "seq": env.seq, "meta": env.meta,
        }, ensure_ascii=False))
        return env

    def history(self, room: str, after_id: str = "",
                limit: int = 100) -> list[BusEnvelope]:
        all_msgs = self._read_all(room)
        if after_id:
            try:
                idx = next(i for i, m in enumerate(all_msgs) if m.id == after_id)
                all_msgs = all_msgs[idx + 1:]
            except StopIteration:
                return []
        return all_msgs[:max(1, int(limit))]

    def replay(self, room: str) -> list[BusEnvelope]:
        return self._read_all(room)


def build_redis_client(url: str, timeout_seconds: float = 2.0) -> Any:
    """按 URL 建生产客户端；包缺失/连不上都抛 ``BackendUnavailable``（诚实）。"""
    try:
        import redis
    except ImportError as exc:
        raise BackendUnavailable(
            "redis 包未安装：远程总线需 `pip install redis` 并部署 Redis "
            "(FY_BUS_REDIS_URL)，当前只有 local 实现可用",
            code="redis_not_installed",
        ) from exc
    try:
        client = redis.Redis.from_url(url, socket_timeout=timeout_seconds,
                                      socket_connect_timeout=timeout_seconds)
        client.ping()
        return client
    except Exception as exc:
        raise BackendUnavailable(
            f"Redis 不可达（{url}）：{type(exc).__name__}",
            code="redis_unreachable",
        ) from exc
