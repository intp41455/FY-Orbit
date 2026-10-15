"""B5-full：map / delegation / market 执行器。

* map：items 按成员轮转扇出（真并发），按输入序汇聚；
* delegation：沿 parent_links 自顶向下委派（同层并发），子拿父输出；
* market：全员并行出价 + select 回调裁决，无 select 直接拒绝（不静默内定）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.coordination import CoordinationValidationError, validate_spec
from find_yourself.services.coordination_run import run_spec


def _spec(**over) -> dict:
    base = {
        "name": "t1",
        "authority": {"mode": "hierarchical"},
        "communication": {"mode": "tree"},
        "decomposition": {"mode": "map"},
        "coordination": {"mode": "hierarchical_task"},
        "control": {"mode": "supervisory"},
        "governance": {"mode": "audit_trail"},
        "members": ["boss", "a", "b"],
        "parent_links": {"a": "boss", "b": "boss"},
    }
    base.update(over)
    return validate_spec(base)


def test_map_distributes_items_round_robin_and_gathers_in_order() -> None:
    seen: list[tuple[str, int]] = []

    def handler(member: str, item):
        seen.append((member, item))
        return f"{member}:{item}"

    out = run_spec(_spec(), handler, initial=[1, 2, 3, 4])
    # 轮转：boss→1, a→2, b→3, boss→4；汇聚按输入序。
    assert seen == [("boss", 1), ("a", 2), ("b", 3), ("boss", 4)]
    assert [r["output"] for r in out["results"]] == ["boss:1", "a:2", "b:3", "boss:4"]
    assert out["final"] == ["boss:1", "a:2", "b:3", "boss:4"]


def test_map_requires_list_input() -> None:
    with pytest.raises(CoordinationValidationError, match="items"):
        run_spec(_spec(), lambda m, d: d, initial="not-a-list")


def test_delegation_flows_top_down_with_parent_outputs() -> None:
    def handler(member: str, data):
        return f"{data}>{member}"

    out = run_spec(_spec(decomposition={"mode": "delegation"}), handler, initial="go")
    by_member = {r["member"]: r["output"] for r in out["results"]}
    assert by_member["boss"] == "go>boss"
    assert by_member["a"] == "go>boss>a"
    assert by_member["b"] == "go>boss>b"
    assert out["final"] == by_member


def test_delegation_requires_parent_links() -> None:
    with pytest.raises(CoordinationValidationError, match="parent_links"):
        validate_spec({
            "name": "t1",
            "authority": {"mode": "decentralized"},
            "communication": {"mode": "mesh"},
            "decomposition": {"mode": "delegation"},
            "coordination": {"mode": "orchestrated"},
            "control": {"mode": "supervisory"},
            "governance": {"mode": "audit_trail"},
            "members": ["a", "b"],
        })


def test_market_collects_bids_and_select_picks_winner() -> None:
    def handler(member: str, data):
        return {"price": {"boss": 5, "a": 9, "b": 7}[member], "plan": member}

    def select(bids):
        return max(bids, key=lambda b: b["output"]["price"])

    out = run_spec(_spec(decomposition={"mode": "market"},
                         coordination={"mode": "auction"}),
                   handler, initial="task",
                   select=select)
    assert out["winner"]["member"] == "a"
    assert out["final"] == {"price": 9, "plan": "a"}
    assert len(out["bids"]) == 3


def test_market_without_select_is_rejected() -> None:
    with pytest.raises(ValueError, match="select"):
        run_spec(_spec(decomposition={"mode": "market"},
                       coordination={"mode": "auction"}),
                 lambda m, d: d, initial="x")


def test_market_select_must_pick_a_real_bid() -> None:
    with pytest.raises(ValueError, match="bid"):
        run_spec(_spec(decomposition={"mode": "market"},
                       coordination={"mode": "auction"}),
                 lambda m, d: {"price": 1},
                 initial="x",
                 select=lambda bids: {"member": "ghost", "output": {}})
