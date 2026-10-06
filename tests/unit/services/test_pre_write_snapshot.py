"""T6-F 高危写前置快照与回滚测试（补 G6）——验收剧本 V4 的服务级实现。

V4 剧本：模拟 agent 误删/误覆盖 → 从前置快照恢复；回滚前有 diff 预览，
回滚后有 sha256 校验；快照损坏则整体拒绝（不做半吊子回滚）。
"""

from __future__ import annotations

import hashlib

import pytest

import find_yourself.db.staging_models  # noqa: F401  (WorkStash 表)
from find_yourself.db.staging_models import WorkStash
from find_yourself.services.errors import DomainError
from find_yourself.services.snapshot import (
    pre_write_snapshot,
    restore_apply,
    restore_preview,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def snap_root(tmp_path, monkeypatch) -> str:
    root = str(tmp_path / "snaps")
    monkeypatch.setenv("FY_SNAPSHOTS_PATH", root)
    return root


def test_snapshot_preview_apply_roundtrip(tmp_path, session, audit, owner, snap_root):
    """V4 主线：快照 → agent 误覆盖 → 预览显示变更 → 回滚 → sha256 校验通过。"""
    target = tmp_path / "doc.txt"
    target.write_text("v1-original", encoding="utf-8")

    manifest = pre_write_snapshot(
        session, audit, owner, files=[str(target)],
        reason="unit-test: before agent write", task_id="task-snap-1",
    )
    session.commit()
    assert manifest["files"][0]["sha256"] == _sha("v1-original")
    assert manifest["files"][0]["existed"] is True

    # manifest 已自动暂存进 WorkStash（主库，重启后仍在）+ 审计挂帧
    stash_rows = session.query(WorkStash).all()
    assert any(
        (r.stash_metadata or {}).get("kind") == "pre_write_snapshot"
        for r in stash_rows
    )

    # 「agent 误覆盖」
    target.write_text("v2-overwritten-by-agent", encoding="utf-8")

    preview = restore_preview(session, owner, manifest["snapshot_id"])
    assert preview["files"][0]["changed"] is True
    assert preview["files"][0]["sha256_now"] == _sha("v2-overwritten-by-agent")

    out = restore_apply(session, audit, owner, manifest["snapshot_id"])
    assert out["ok"] is True
    assert out["files"][0]["restored"] is True
    assert target.read_text(encoding="utf-8") == "v1-original"  # 误覆盖被挽回


def test_tampered_snapshot_refused_entirely(tmp_path, session, audit, owner, snap_root):
    """快照副本被篡改 → 整体拒绝回滚，绝不从损坏快照恢复（红线 5）。"""
    target = tmp_path / "doc.txt"
    target.write_text("v1", encoding="utf-8")
    manifest = pre_write_snapshot(session, audit, owner, files=[str(target)],
                                  reason="tamper-test")
    session.commit()
    target.write_text("v2-current", encoding="utf-8")

    import pathlib

    copy_path = (
        pathlib.Path(snap_root) / manifest["snapshot_id"] / "files"
        / manifest["files"][0]["snapshot_copy"]
    )
    copy_path.write_bytes(b"tampered!!!")

    with pytest.raises(DomainError) as excinfo:
        restore_apply(session, audit, owner, manifest["snapshot_id"])
    assert excinfo.value.code == "snapshot_integrity"
    assert target.read_text(encoding="utf-8") == "v2-current"  # 原文件未被半吊子改动


def test_absent_at_snapshot_is_not_auto_deleted(tmp_path, session, audit, owner, snap_root):
    """快照时点不存在的文件：回滚**不自动删除**当前文件（诚实边界）。"""
    absent = tmp_path / "created-later.txt"
    manifest = pre_write_snapshot(session, audit, owner, files=[str(absent)],
                                  reason="absent-test")
    session.commit()
    assert manifest["files"][0]["existed"] is False

    absent.write_text("born after snapshot", encoding="utf-8")
    out = restore_apply(session, audit, owner, manifest["snapshot_id"])
    entry = out["files"][0]
    assert entry["action"] == "absent_at_snapshot"
    assert entry["restored"] is False
    assert absent.is_file()  # 不自动删，决定权留给用户


def test_unknown_snapshot_id_404(session, owner, snap_root):
    with pytest.raises(DomainError):
        restore_preview(session, owner, "nonexistent-snap")


def test_empty_file_list_rejected(session, audit, owner, snap_root):
    with pytest.raises(DomainError):
        pre_write_snapshot(session, audit, owner, files=[], reason="empty")
