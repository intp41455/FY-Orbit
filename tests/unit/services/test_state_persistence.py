"""state_persistence 的 DSL run 存档导出/导入（``export_dsl_store`` / ``import_dsl_store``）。

此前该模块**零测试覆盖**（``export_dsl_store`` / ``import_dsl_store`` /
``session-state`` 在 tests 下 zero hit）。G2-e 给每条 run 加了归属标识 ``owner``，
并让快照**携带**与**回填** ``owner``——本文件把这处「只有代码审查保证」的改动
变成可验证的往返：

* 整库往返：run 数量与关键字段（run_id / status / dsl / output / error /
  created_at / logs）一致；
* 归属保留：import 后 ``owner_of`` 与导出前一致，``get_owned`` 正确 owner 取得到、
  错误 owner 取不到（返回 None）；
* 边界/兼容：空快照、缺 ``runs`` 键、以及**缺 ``owner`` 的旧格式快照**都不崩溃。
"""

from __future__ import annotations

from find_yourself.services.dsl_canvas import DslRunStore, NodeLog, RunResult
from find_yourself.services.state_persistence import (
    export_dsl_store,
    import_dsl_store,
)

_OWNER_A = "owner-a"
_OWNER_B = "owner-b"


def _doc(label: str) -> dict:
    return {
        "version": "1",
        "nodes": [
            {"id": "src", "type": "input",
             "params": {"kind": "literal", "value": label}},
            {"id": "out", "type": "output", "params": {"format": "text"}},
        ],
        "edges": [{"from": "src", "to": "out"}],
    }


def _seed_store() -> DslRunStore:
    """三条 run、分属两个 owner：覆盖 succeeded/failed/suspended 与有无 output。"""
    store = DslRunStore()
    store.save(
        RunResult(
            run_id="run-a", status="succeeded", doc=_doc("A"),
            logs=[NodeLog(node_id="src", node_type="input", verb=None,
                          status="succeeded", output="A",
                          started_at="2026-01-01T00:00:00+00:00",
                          finished_at="2026-01-01T00:00:01+00:00")],
            output="A-out", created_at="2026-01-01T00:00:00+00:00"),
        owner=_OWNER_A,
    )
    store.save(
        RunResult(run_id="run-b", status="failed", doc=_doc("B"),
                  output=None, error="boom",
                  created_at="2026-01-02T00:00:00+00:00"),
        owner=_OWNER_B,
    )
    store.save(
        RunResult(run_id="run-c", status="suspended", doc=_doc("C"),
                  output={"partial": True},
                  created_at="2026-01-03T00:00:00+00:00"),
        owner=_OWNER_A,
    )
    return store


def _round_trip(src: DslRunStore) -> tuple[dict, DslRunStore]:
    """export 到快照 → 导入一个全新的空 store。"""
    snapshot = export_dsl_store(src)
    dst = DslRunStore()
    import_dsl_store(snapshot, dst)
    return snapshot, dst


# --------------------------------------------------------------------------- #
# 往返无损
# --------------------------------------------------------------------------- #

def test_round_trip_preserves_runs_and_key_fields() -> None:
    """整库往返后 run 数量、run_id、status、doc/output、created_at、logs 全一致。"""
    src = _seed_store()
    snapshot, dst = _round_trip(src)

    assert len(snapshot["runs"]) == 3
    assert dst.list_ids() == src.list_ids()

    for run_id in src.list_ids():
        before, after = src.get(run_id), dst.get(run_id)
        assert after is not None
        assert after["run_id"] == before["run_id"]
        assert after["status"] == before["status"]
        assert after["dsl"] == before["dsl"]
        assert after["output"] == before["output"]
        assert after["error"] == before["error"]
        assert after["created_at"] == before["created_at"]
        assert after["logs"] == before["logs"]

    # 钉死几个具体值，避免「自比自」的假通过
    assert dst.get("run-a")["output"] == "A-out"
    assert dst.get("run-a")["logs"][0]["status"] == "succeeded"
    assert dst.get("run-b")["status"] == "failed"
    assert dst.get("run-b")["error"] == "boom"
    assert dst.get("run-c")["output"] == {"partial": True}


def test_round_trip_preserves_execution_id() -> None:
    """``execution_id`` 必须随快照走。

    缺陷（2026-10-13 由补测试挖出）：``_run_result_from_dict`` 重建``RunResult``
    时未读回 ``execution_id``，restore 后一律变``None``。这违反模块 docstring
    「a faithful restore must reproduce state exactly」（P1-21 验收判据），并
    直接影响 ``dsl_debug.get_state`` 返回的 ``execution_id``。
    """
    src = DslRunStore()
    src.save(
        RunResult(run_id="run-x", status="succeeded", doc=_doc("X"),
                  output="X-out", execution_id="exec-42",
                  created_at="2026-01-04T00:00:00+00:00"),
        owner=_OWNER_A,
    )
    _, dst = _round_trip(src)

    assert dst.get("run-x")["execution_id"] == "exec-42"
    assert dst.get("run-x")["execution_id"] == src.get("run-x")["execution_id"]


def test_round_trip_without_execution_id_stays_none() -> None:
    """没有 execution_id 的 run 往返后仍是 ``None``（不凭空造值）。"""
    _, dst = _round_trip(_seed_store())
    assert dst.get("run-a")["execution_id"] is None


def test_round_trip_import_returns_count() -> None:
    """``import_dsl_store`` 返回值 = 实际回放的 run 条数。"""
    snapshot = export_dsl_store(_seed_store())
    dst = DslRunStore()
    assert import_dsl_store(snapshot, dst) == 3


# --------------------------------------------------------------------------- #
# 归属保留
# --------------------------------------------------------------------------- #

def test_round_trip_keeps_owner() -> None:
    """import 之后 ``owner_of`` 与导出前逐条一致。"""
    src = _seed_store()
    _snapshot, dst = _round_trip(src)

    for run_id in src.list_ids():
        assert dst.owner_of(run_id) == src.owner_of(run_id)
    assert dst.owner_of("run-a") == _OWNER_A
    assert dst.owner_of("run-b") == _OWNER_B
    assert dst.owner_of("run-c") == _OWNER_A


def test_get_owned_after_import() -> None:
    """正确 owner 取得到、错误 owner 取不到（返回 None）——归属校验在 restore 后仍生效。"""
    _snapshot, dst = _round_trip(_seed_store())

    assert dst.get_owned("run-a", _OWNER_A) is not None
    assert dst.get_owned("run-b", _OWNER_B) is not None
    assert dst.get_owned("run-c", _OWNER_A) is not None

    assert dst.get_owned("run-a", _OWNER_B) is None
    assert dst.get_owned("run-b", _OWNER_A) is None
    # 不存在的 run 同样取不到，不泄漏「存在/不存在」
    assert dst.get_owned("run-z", _OWNER_A) is None


def test_export_carries_owner_not_present_in_store_payload() -> None:
    """store 内存 payload 本身无 ``owner`` 键；owner 只在导出快照里补上。

    钉死 ``export_dsl_store`` 的写法：``{**payload, "owner": store.owner_of(run_id)}``。
    """
    src = _seed_store()
    for run_id in src.list_ids():
        assert "owner" not in src.get(run_id), \
            "store.get() 的 payload 不应自带 owner 键（owner 存于 _owners 旁路）"

    snapshot = export_dsl_store(src)
    owners = {r["run_id"]: r["owner"] for r in snapshot["runs"]}
    assert owners == {"run-a": _OWNER_A, "run-b": _OWNER_B, "run-c": _OWNER_A}


# --------------------------------------------------------------------------- #
# 边界 / 兼容
# --------------------------------------------------------------------------- #

def test_empty_snapshot_imports_zero_and_is_noop() -> None:
    """空快照 ``{"runs": []}`` → 返回 0，且不抛异常、store 保持空。"""
    dst = DslRunStore()
    assert import_dsl_store({"runs": []}, dst) == 0
    assert dst.list_ids() == []


def test_missing_runs_key_imports_zero() -> None:
    """连 ``runs`` 键都没有时按空处理，不抛 KeyError。"""
    dst = DslRunStore()
    assert import_dsl_store({}, dst) == 0
    assert dst.list_ids() == []


def test_legacy_snapshot_without_owner_is_backward_compatible() -> None:
    """旧格式快照（无 ``owner`` 字段）导入不崩溃；owner 回退为空串 ``""``。"""
    legacy = {"runs": [{
        "run_id": "run-legacy", "status": "succeeded", "dsl": _doc("L"),
        "output": "L-out", "error": None,
        "created_at": "2025-12-31T00:00:00+00:00", "logs": [],
    }]}
    dst = DslRunStore()
    assert import_dsl_store(legacy, dst) == 1
    assert dst.get("run-legacy") is not None
    assert dst.owner_of("run-legacy") == ""
    # 归属被回退为空串——空 owner 者可读回（向后兼容语义）
    assert dst.get_owned("run-legacy", "") is not None
    assert dst.get_owned("run-legacy", _OWNER_A) is None
