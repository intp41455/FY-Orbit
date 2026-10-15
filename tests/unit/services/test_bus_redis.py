"""B8 双模式：Redis 后端（可注入传输）+ auto 自动选择。

* ``RedisBusBackend`` 满足同一 ``BusBackend`` 协议（publish/history/replay）；
* 传输可注入：测试用内存 fake，生产传 ``redis.Redis``（懒加载，缺依赖即
  诚实不可用，不在 pyproject 里硬加依赖）；
* ``kind="auto"``：URL 可达用 redis，否则回落 local（offline-first）；
* 显式 ``kind="redis"`` 但不可达 → ``BackendUnavailable``（不静默降级）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.bus_backend import (
    BackendUnavailable,
    BusBackend,
    create_bus_backend,
)


class FakeRedis:
    """最小内存传输（仅测试用）：rpush / lrange / ping / ping-fail 开关。"""

    def __init__(self, *, fail_ping: bool = False):
        self.store: dict[str, list[str]] = {}
        self._fail_ping = fail_ping

    def ping(self) -> bool:
        if self._fail_ping:
            raise ConnectionError("unreachable")
        return True

    def rpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])

    def lrange(self, key: str, _start: int, _stop: int) -> list[str]:
        return list(self.store.get(key, []))


def _backend(**kw):
    from find_yourself.services.bus_backend import RedisBusBackend

    return RedisBusBackend(client=FakeRedis(), **kw)


def test_redis_backend_satisfies_protocol() -> None:
    backend = _backend()
    assert isinstance(backend, BusBackend)
    assert backend.backend_name == "redis"


def test_redis_publish_history_replay_roundtrip() -> None:
    backend = _backend()
    e1 = backend.publish("room1", sender="a", kind="text", content="hello")
    e2 = backend.publish("room1", sender="b", kind="text", content="hi")
    assert [h.content for h in backend.history("room1")] == ["hello", "hi"]
    assert backend.history("room1", after_id=e1.id) == [e2]
    assert [h.content for h in backend.replay("room1")] == ["hello", "hi"]


def test_redis_rooms_are_isolated() -> None:
    backend = _backend()
    backend.publish("r1", sender="a", kind="text", content="x")
    assert backend.history("r2") == []


def test_auto_selects_redis_when_reachable(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setenv("FY_BUS_REDIS_URL", "redis://qa:6379/0")
    backend = create_bus_backend("auto", room_dir="unused", _client=fake)
    assert backend.backend_name == "redis"


def test_auto_falls_back_to_local_when_unreachable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FY_BUS_REDIS_URL", "redis://qa:6379/0")
    backend = create_bus_backend(
        "auto", room_dir=str(tmp_path), _client=FakeRedis(fail_ping=True))
    assert backend.backend_name == "local"


def test_auto_without_url_uses_local(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("FY_BUS_REDIS_URL", raising=False)
    backend = create_bus_backend("auto", room_dir=str(tmp_path))
    assert backend.backend_name == "local"


def test_explicit_redis_unreachable_is_not_silently_downgraded(
        monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FY_BUS_REDIS_URL", "redis://qa:6379/0")
    with pytest.raises(BackendUnavailable, match="[Rr]edis"):
        create_bus_backend(
            "redis", room_dir=str(tmp_path), _client=FakeRedis(fail_ping=True))


def test_explicit_redis_without_client_and_without_dep_is_honest(
        monkeypatch, tmp_path) -> None:
    import sys

    monkeypatch.delenv("FY_BUS_REDIS_URL", raising=False)
    monkeypatch.setitem(sys.modules, "redis", None)
    with pytest.raises(BackendUnavailable, match="redis"):
        create_bus_backend("redis", room_dir=str(tmp_path))
