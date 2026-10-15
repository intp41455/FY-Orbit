"""B7 冲突裁决（⑤冲突层）：资源抢占按规则裁决，全程留痕。

规则（确定性，无随机）：
1. owner 否决：owner 的 claim 直接胜出（人永远大于 Agent）；
2. 优先级：priority 高者胜；
3. 先到先得：同优先级早 ts 胜；
4. 兜底：仍并列按 claimant id 字典序（可复现，不抛硬币）。
每次裁决返回理由；release 释放后后来者可取。
"""

from __future__ import annotations

from find_yourself.services.bus_conflict import Claim, ConflictArbiter


def _arbiter() -> ConflictArbiter:
    return ConflictArbiter()


def test_owner_veto_wins_over_higher_priority() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="mic", claimant="agent:x", priority=99, ts=1.0))
    d = arb.claim(Claim(resource="mic", claimant="owner",
                        priority=0, ts=2.0, is_owner=True))
    assert d.winner == "owner"
    assert d.reason == "owner_veto"
    assert arb.holder("mic") == "owner"


def test_higher_priority_wins() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="gpu", claimant="agent:low", priority=1, ts=1.0))
    d = arb.claim(Claim(resource="gpu", claimant="agent:high", priority=9, ts=2.0))
    assert d.winner == "agent:high"
    assert d.reason == "priority"


def test_first_writer_wins_on_tie() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="bus", claimant="agent:a", priority=5, ts=1.0))
    d = arb.claim(Claim(resource="bus", claimant="agent:b", priority=5, ts=2.0))
    assert d.winner == "agent:a"
    assert d.reason == "first_writer_wins"


def test_exact_tie_breaks_by_claimant_id_deterministically() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="r", claimant="agent:b", priority=5, ts=1.0))
    d = arb.claim(Claim(resource="r", claimant="agent:a", priority=5, ts=1.0))
    assert d.winner == "agent:a"
    assert d.reason == "claimant_id_order"


def test_release_frees_resource_for_latecomer() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="mic", claimant="agent:a", priority=1, ts=1.0))
    assert arb.release("mic", "agent:a") is True
    assert arb.holder("mic") is None
    d = arb.claim(Claim(resource="mic", claimant="agent:b", priority=1, ts=2.0))
    assert d.winner == "agent:b"


def test_release_by_non_holder_is_rejected() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="mic", claimant="agent:a", priority=1, ts=1.0))
    assert arb.release("mic", "agent:b") is False
    assert arb.holder("mic") == "agent:a"


def test_decision_carries_audit_trail() -> None:
    arb = _arbiter()
    arb.claim(Claim(resource="mic", claimant="agent:a", priority=1, ts=1.0))
    d = arb.claim(Claim(resource="mic", claimant="agent:b", priority=9, ts=2.0))
    assert d.resource == "mic"
    assert list(d.contenders) == ["agent:a", "agent:b"]
    assert d.reason in ("owner_veto", "priority", "first_writer_wins", "claimant_id_order")
