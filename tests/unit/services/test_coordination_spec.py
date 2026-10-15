"""B5/N8 首切片：组织/协调拓扑即数据（REQ-A09）。

六维正交原语（权威/通信/分解/协调/控制/治理）声明式配置，代替逐个硬编码
拓扑形态（REQ-D04 的顺序/并行/流水线只是其中三种实例）。
本切片 = 声明层：schema + 交叉一致性校验 + describe；通用协调运行时在后。
"""

from __future__ import annotations

import pytest

from find_yourself.services.coordination import (
    CoordinationSpec,
    CoordinationValidationError,
    describe_spec,
    validate_spec,
)


def _base(**over) -> dict:
    spec = {
        "name": "t1",
        "authority": {"mode": "centralized", "coordinator": "boss"},
        "communication": {"mode": "star"},
        "decomposition": {"mode": "sequential"},
        "coordination": {"mode": "orchestrated"},
        "control": {"mode": "supervisory"},
        "governance": {"mode": "audit_trail"},
        "members": ["boss", "a", "b"],
    }
    spec.update(over)
    return spec


def test_valid_centralized_star_spec_passes() -> None:
    spec = validate_spec(_base())
    assert isinstance(spec, CoordinationSpec)
    assert spec.authority.mode == "centralized"


def test_pipeline_decentralized_mesh_spec_passes() -> None:
    spec = validate_spec(_base(
        authority={"mode": "decentralized"},
        communication={"mode": "mesh"},
        decomposition={"mode": "pipeline", "stages": ["s1", "s2"]},
        coordination={"mode": "choreographed"},
        control={"mode": "cooperative"},
        governance={"mode": "none"},
    ))
    assert spec.communication.mode == "mesh"


def test_hierarchy_requires_acyclic_parent_links() -> None:
    ok = validate_spec(_base(
        authority={"mode": "hierarchical"},
        communication={"mode": "tree"},
        members=["root", "m1", "m2"],
        parent_links={"m1": "root", "m2": "m1"},
    ))
    assert ok.authority.mode == "hierarchical"
    with pytest.raises(CoordinationValidationError, match="cycle"):
        validate_spec(_base(
            authority={"mode": "hierarchical"},
            communication={"mode": "tree"},
            members=["root", "m1"],
            parent_links={"m1": "root", "root": "m1"},
        ))


def test_centralized_star_requires_coordinator() -> None:
    with pytest.raises(CoordinationValidationError, match="coordinator"):
        validate_spec(_base(authority={"mode": "centralized"}))
    with pytest.raises(CoordinationValidationError, match="coordinator"):
        validate_spec(_base(
            authority={"mode": "centralized", "coordinator": "boss"},
            communication={"mode": "star"},
            members=["a", "b"],
        ))


def test_pipeline_requires_ordered_stages() -> None:
    with pytest.raises(CoordinationValidationError, match="stages"):
        validate_spec(_base(decomposition={"mode": "pipeline"}))


def test_unknown_mode_rejected() -> None:
    with pytest.raises(CoordinationValidationError, match="authority.mode"):
        validate_spec(_base(authority={"mode": "telepathy"}))


def test_describe_is_stable_and_honest() -> None:
    d = describe_spec(validate_spec(_base()))
    assert d["topology_kind"] == "centralized-star"
    assert d["coordinator"] == "boss"
    assert d["member_count"] == 3
    assert "sequential" in d["execution"]
