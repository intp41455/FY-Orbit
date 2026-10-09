"""提问 ↔ trace 双向索引（需求 #4「每次提问侧边快速定位」）。

本文件锁定三件事：

1. **哈希链向后兼容（硬判据）** —— 不带 ``message_id`` 的事件，其 ``hash``
   必须与旧实现逐位一致。实现把 ``message_id`` 折叠进已被哈希覆盖的
   ``details``，且在未提供时**不写入该键**，因此既有事件的 canonical body
   与旧算法完全相同。
2. **``message_id`` 参与哈希** —— 篡改归属字段会被 ``verify()`` 检测为
   content-hash mismatch。
3. **双向索引 + owner 隔离** —— ``message_id → 帧列表`` 有序且完整、
   ``帧 → message_id`` 正确、未归属帧不报错；越权查询既读不到也不泄露存在性。

锚点用 ``tmp_path`` 下的 ``FileAnchorStore``，避免写入用户主目录、也避免
跨用例污染锚点状态（原因同 ``test_bug10_audit.py``）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from find_yourself.config import Settings
from find_yourself.db.models import AuditEvent
from find_yourself.runtime.sse import inject_message_id, stamp_stream
from find_yourself.services.actor import Actor
from find_yourself.services.anchor_store import FileAnchorStore
from find_yourself.services.audit import MESSAGE_ID_KEY, AuditService, message_id_of
from find_yourself.services.hasher import digest


@pytest.fixture()
def anchor_store(tmp_path):
    return FileAnchorStore(tmp_path / "anchors")


@pytest.fixture()
def a(session, anchor_store):
    return AuditService(session, anchor_store=anchor_store)


def _legacy_body(ev: AuditEvent) -> dict:
    """复刻旧实现的 canonical body（绝不包含 message_id 顶层字段）。"""
    return {
        "seq": ev.seq,
        "actor": ev.actor,
        "action": ev.action,
        "target": ev.target,
        "details": ev.details,
        "previous_hash": ev.previous_hash,
    }


# ---------------------------------------------------------------------------
# 1. 向后兼容：既有事件（无 message_id）哈希与旧实现一致
# ---------------------------------------------------------------------------

def test_legacy_event_hash_matches_old_formula(a, owner):
    """无 message_id 的事件，哈希 == 旧算法直接计算值。"""
    ev = a.append(owner, "one", "t1", {"k": "v"})
    assert MESSAGE_ID_KEY not in ev.details
    assert ev.hash == digest(_legacy_body(ev))


def test_none_message_id_reproduces_legacy_hash(a, owner):
    """显式 message_id=None 与不传完全等价，`None` 不进入哈希。"""
    ev = a.append(owner, "act", "tgt", {"d": 1}, message_id=None)
    assert ev.previous_hash == "0" * 64
    expected = digest({
        "seq": 1, "actor": "owner-1", "action": "act", "target": "tgt",
        "details": {"d": 1}, "previous_hash": "0" * 64,
    })
    assert ev.hash == expected


def test_legacy_chain_still_verifies(a, owner):
    """纯旧式事件组成的链，改造后仍自洽。"""
    a.append(owner, "one", "t1")
    a.append(owner, "two", "t2", {"x": 1})
    a.anchor()
    res = a.verify()
    assert res.ok, res.problems
    assert res.anchored is True


def test_legacy_hash_identical_before_and_after_feature(session, owner, anchor_store):
    """同一输入在带/不带 message_id 支持下的哈希必须一致。

    直接对两条『等价旧式调用』分别计算 canonical digest：只要写法未变，
    结果必须相等 —— 这是“改造未改变既有事件的哈希”的可复现证据。
    """
    a = AuditService(session, anchor_store=anchor_store)
    first = a.append(owner, "legacy", "t")
    second = a.append(owner, "legacy", "t")
    # 两条事件细节相同但 seq/prev 不同；用同一 body 结构证明字段集合未变。
    assert set(first.details.keys()) == set(second.details.keys()) == set()
    assert first.hash == digest(_legacy_body(first))
    assert second.hash == digest(_legacy_body(second))


# ---------------------------------------------------------------------------
# 2. message_id 参与哈希链，篡改可检测
# ---------------------------------------------------------------------------

def test_message_id_recorded_in_details(a, owner):
    ev = a.append(owner, "q", "t", message_id="m1")
    assert ev.details[MESSAGE_ID_KEY] == "m1"
    assert message_id_of(ev) == "m1"


def test_tampering_message_id_is_detected(a, session, owner):
    ev = a.append(owner, "q", "t", message_id="m1")
    stored = session.query(AuditEvent).filter_by(seq=ev.seq).one()
    stored.details = {MESSAGE_ID_KEY: "m1-forged"}
    session.flush()
    res = a.verify()
    assert not res.ok
    assert any("content-hash" in p for p in res.problems)


def test_chain_self_consistent_with_mixed_ids(a, owner):
    """旧式帧与带 id 帧混排，链仍自洽。"""
    a.append(owner, "legacy", "t0")
    a.append(owner, "q", "t1", message_id="m1")
    a.append(owner, "q", "t2", message_id="m1")
    a.append(owner, "legacy", "t3")
    a.append(owner, "q", "t4", message_id="m2")
    res = a.verify()
    assert res.ok, res.problems


def test_conflicting_details_message_id_raises(a, owner):
    with pytest.raises(ValueError):
        a.append(owner, "q", "t", {MESSAGE_ID_KEY: "x"}, message_id="y")
    # 一致时允许，且不重复报错
    ev = a.append(owner, "q", "t", {MESSAGE_ID_KEY: "x"}, message_id="x")
    assert message_id_of(ev) == "x"


def test_append_does_not_mutate_caller_details(a, owner):
    payload = {"k": "v"}
    a.append(owner, "q", "t", payload, message_id="m1")
    assert payload == {"k": "v"}


# ---------------------------------------------------------------------------
# 3. message_id → 帧列表（有序、完整、按 message 过滤）
# ---------------------------------------------------------------------------

def test_frames_for_message_ordered_and_complete(a, owner):
    first = a.append(owner, "q", "a", message_id="m1")
    a.append(owner, "unrelated", "x")  # 未归属
    others = [a.append(owner, "q", f"b{i}", message_id="m1") for i in range(3)]
    frames = a.frames_for_message(owner, "m1")
    assert [f.seq for f in frames] == [first.seq] + [e.seq for e in others]
    assert [f.seq for f in frames] == sorted(f.seq for f in frames)


def test_frames_for_message_excludes_other_messages(a, owner):
    a.append(owner, "q", "a", message_id="m1")
    a.append(owner, "q", "b", message_id="m2")
    a.append(owner, "q", "c", message_id="m1")
    frames = a.frames_for_message(owner, "m1")
    assert len(frames) == 2
    assert all(message_id_of(f) == "m1" for f in frames)


def test_frames_for_unknown_or_empty_message_is_empty(a, owner):
    a.append(owner, "q", "a", message_id="m1")
    assert a.frames_for_message(owner, "does-not-exist") == []
    assert a.frames_for_message(owner, "") == []


def test_message_filter_is_load_bearing(a, owner):
    """变异判据：把 message_id 从查询条件里去掉 → 本用例变红。

    owner 名下共 5 帧，其中仅 2 帧属于 m1。『按 message 过滤』若失效（返回
    全部 5 帧），下面的长度与 seq 断言必然失败。
    """
    for i in range(3):
        a.append(owner, "q", f"other{i}", message_id="other")
    want = [a.append(owner, "q", f"m{i}", message_id="m1").seq for i in range(2)]
    frames = a.frames_for_message(owner, "m1")
    assert [f.seq for f in frames] == want
    assert len(frames) == 2  # 不是 5


# ---------------------------------------------------------------------------
# 4. 帧 → message_id
# ---------------------------------------------------------------------------

def test_message_for_frame_returns_id(a, owner):
    ev = a.append(owner, "q", "a", message_id="m1")
    assert a.message_for_frame(owner, ev.seq) == "m1"


def test_message_for_frame_unassigned_returns_none(a, owner):
    ev = a.append(owner, "legacy", "a")
    assert a.message_for_frame(owner, ev.seq) is None


def test_message_for_frame_unknown_seq_returns_none(a, owner):
    a.append(owner, "q", "a", message_id="m1")
    assert a.message_for_frame(owner, 9999) is None


def test_unassigned_frames_do_not_error(a, owner):
    """无归属帧既不报错，也不污染带归属的查询。"""
    legacy1 = a.append(owner, "legacy", "a")
    tagged = a.append(owner, "q", "b", message_id="m1")
    legacy2 = a.append(owner, "legacy", "c")
    assert [f.seq for f in a.frames_for_message(owner, "m1")] == [tagged.seq]
    assert a.message_for_frame(owner, legacy1.seq) is None
    assert a.message_for_frame(owner, legacy2.seq) is None


# ---------------------------------------------------------------------------
# 5. owner 隔离
# ---------------------------------------------------------------------------

def test_owner_isolation_frames_for_message(a, owner):
    other = Actor.owner("owner-2")
    a.append(other, "q", "a", message_id="shared")
    a.append(owner, "q", "b", message_id="shared")
    frames = a.frames_for_message(owner, "shared")
    assert [f.actor for f in frames] == ["owner-1"]
    assert all(f.actor == owner.owner_id for f in frames)


def test_owner_isolation_message_for_frame(a, owner):
    other = Actor.owner("owner-2")
    ev = a.append(other, "q", "a", message_id="m1")
    # A 查不到 B 的帧，且与「不存在」返回一致 —— 不泄露存在性
    assert a.message_for_frame(owner, ev.seq) is None
    assert a.message_for_frame(other, ev.seq) == "m1"


def test_owner_cannot_enumerate_other_frames(a, owner):
    other = Actor.owner("owner-2")
    for i in range(3):
        a.append(other, "q", f"o{i}", message_id="m1")
    assert a.frames_for_message(owner, "m1") == []


def test_anonymous_identity_reads_nothing(a, owner):
    anonymous = Actor(subject_type="anonymous")
    a.append(owner, "q", "a", message_id="m1")
    assert a.frames_for_message(anonymous, "m1") == []
    assert a.message_for_frame(anonymous, 1) is None


# ---------------------------------------------------------------------------
# 6. SSE 帧携带 message_id
# ---------------------------------------------------------------------------

def test_inject_message_id_into_data_frame():
    frame = 'event: message_start\ndata: {"stream_id": "s"}\n\n'
    out = inject_message_id(frame, "m-42")
    assert out.startswith("event: message_start\n")
    payload = json.loads(out.split("data: ", 1)[1].strip())
    assert payload["stream_id"] == "s"
    assert payload["message_id"] == "m-42"


def test_inject_message_id_leaves_heartbeat_untouched():
    assert inject_message_id(": heartbeat\n\n", "m1") == ": heartbeat\n\n"


def test_inject_message_id_preserves_explicit_value():
    frame = 'data: {"message_id": "explicit"}\n\n'
    out = inject_message_id(frame, "auto")
    payload = json.loads(out.split("data: ", 1)[1].strip())
    assert payload["message_id"] == "explicit"  # 不覆盖显式值


def test_inject_message_id_tolerates_non_json_data():
    frame = "data: not-json\n\n"
    assert inject_message_id(frame, "m1") == "data: not-json\n\n"


def test_stamp_stream_attributes_every_frame():
    """真实 Stub 流的每个 data 帧都带上同一个 message_id。"""
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
    )
    from find_yourself.services.streaming import StreamingService

    svc = StreamingService(settings)
    actor = Actor.service("svc-test", "tester")

    async def collect():
        return [
            f
            async for f in stamp_stream(
                svc.stream_chat(actor, prompt="ping", model="default", task_id="t1"),
                "m-42",
            )
        ]

    frames = asyncio.run(collect())
    data_frames = [f for f in frames if "data:" in f]
    assert data_frames, "expected at least one data frame"
    for f in data_frames:
        payload = json.loads(f.split("data: ", 1)[1].strip())
        assert payload["message_id"] == "m-42"
