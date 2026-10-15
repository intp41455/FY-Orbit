"""B8 总线插件化：统一总线接口 + 本地实现（文件 JSONL，零依赖离线优先）。

* ``BusBackend`` 是唯一契约（publish/history/replay + backend_name）；
* 第一版只做本地实现；``kind="redis"`` 诚实报不可用（插槽预留，非假实现）；
* 信封落 JSONL 文件（append-only），重启可 replay，内存只做索引。
"""

from __future__ import annotations

import json

import pytest

from find_yourself.services.bus_backend import (
    BackendUnavailable,
    BusBackend,
    BusEnvelope,
    LocalBusBackend,
    create_bus_backend,
)


def test_local_backend_satisfies_protocol(tmp_path) -> None:
    backend = LocalBusBackend(room_dir=str(tmp_path))
    assert isinstance(backend, BusBackend)
    assert backend.backend_name == "local"


def test_publish_history_replay_roundtrip(tmp_path) -> None:
    backend = LocalBusBackend(room_dir=str(tmp_path))
    e1 = backend.publish("room1", sender="agent:a", kind="text", content="hello")
    e2 = backend.publish("room1", sender="agent:b", kind="text", content="hi")
    assert e1.id != e2.id
    hist = backend.history("room1")
    assert [h.content for h in hist] == ["hello", "hi"]
    assert backend.history("room1", after_id=e1.id) == [e2]
    # 文件真实落盘（append-only JSONL）。
    lines = (tmp_path / "room1.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["content"] == "hello"
    # 新实例（模拟重启）能 replay。
    backend2 = LocalBusBackend(room_dir=str(tmp_path))
    assert [h.content for h in backend2.replay("room1")] == ["hello", "hi"]


def test_rooms_are_isolated(tmp_path) -> None:
    backend = LocalBusBackend(room_dir=str(tmp_path))
    backend.publish("r1", sender="a", kind="text", content="x")
    assert backend.history("r2") == []
    assert backend.replay("r2") == []


def test_redis_backend_slot_raises_honest_error(tmp_path) -> None:
    with pytest.raises(BackendUnavailable, match="redis"):
        create_bus_backend("redis", room_dir=str(tmp_path))


def test_unknown_backend_rejected(tmp_path) -> None:
    with pytest.raises(BackendUnavailable, match="未知"):
        create_bus_backend("kafka", room_dir=str(tmp_path))


def test_envelope_validation() -> None:
    with pytest.raises(ValueError, match="room"):
        BusEnvelope(id="1", room="", sender="a", kind="text", content="x", ts="")
    with pytest.raises(ValueError, match="content"):
        BusEnvelope(id="1", room="r", sender="a", kind="text", content="  ", ts="")
