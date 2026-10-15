"""B5 运行时首切片：CoordinationSpec 统一执行。

同一份 spec 驱动执行（拓扑即数据，不是注释）：
* sequential：按 members 顺序串行，前者输出进后者；
* parallel：全员并发，结果按 members 声明序；
* pipeline：按 members 顺序流经（每成员即一站）；
* map/delegation/market 等暂不支持 → 诚实抛 UnsupportedTopology（不静默降级）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.coordination import validate_spec
from find_yourself.services.coordination_run import UnsupportedTopology, run_spec


def _spec(**over) -> dict:
    base = {
        "name": "t1",
        "authority": {"mode": "centralized", "coordinator": "boss"},
        "communication": {"mode": "star"},
        "decomposition": {"mode": "sequential"},
        "coordination": {"mode": "orchestrated"},
        "control": {"mode": "supervisory"},
        "governance": {"mode": "audit_trail"},
        "members": ["boss", "a", "b"],
    }
    base.update(over)
    return validate_spec(base)


def test_sequential_threads_outputs_in_order() -> None:
    calls: list[str] = []

    def handler(member: str, data):
        calls.append(member)
        return f"{data}|{member}"

    out = run_spec(_spec(), handler, initial="go")
    assert calls == ["boss", "a", "b"]
    assert out["final"] == "go|boss|a|b"
    assert [r["member"] for r in out["results"]] == ["boss", "a", "b"]


def test_parallel_collects_in_declaration_order() -> None:
    import threading

    tids: set[int] = set()

    def handler(member: str, data):
        tids.add(threading.get_ident())
        return member.upper()

    out = run_spec(
        _spec(decomposition={"mode": "parallel"},
              authority={"mode": "decentralized"},
              communication={"mode": "mesh"}),
        handler, initial="go",
    )
    assert [r["member"] for r in out["results"]] == ["boss", "a", "b"]
    assert [r["output"] for r in out["results"]] == ["BOSS", "A", "B"]


def test_pipeline_flows_through_members_as_stages() -> None:
    out = run_spec(
        _spec(decomposition={"mode": "pipeline", "stages": ["s1", "s2", "s3"]}),
        lambda member, data: f"{data}>{member}",
        initial="in",
    )
    assert out["final"] == "in>boss>a>b"


def test_unsupported_modes_raise_honestly() -> None:
    with pytest.raises(UnsupportedTopology, match="market"):
        run_spec(_spec(decomposition={"mode": "market"}), lambda m, d: d, initial="x")
    with pytest.raises(UnsupportedTopology, match="map"):
        run_spec(_spec(decomposition={"mode": "map"}), lambda m, d: d, initial="x")
