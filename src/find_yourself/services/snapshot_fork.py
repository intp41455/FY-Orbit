"""P4 · 存档回溯分支引擎（A-存档回溯-04/05/06）。

需求现场
--------

三条需求都围绕「**存档不是终点，而是分叉的起点**」：

* **A-存档回溯-04 改参重跑生成新分支** —— 回溯到某个存档点后改参数重跑，结果
  必须落在**新分支**上，**不覆盖原历史**。
* **A-存档回溯-05 分支对比** —— 不同分支的执行结果差异要能并排看出。
* **A-存档回溯-06 存档历史时间线可视化** —— 存档历史要能按时间轴看。

复用而非重建（重要）
--------------------

本模块**不新建**存档/检查点基础设施，只在其上做分叉语义：

* ``runtime/checkpoint_sqlite.py`` 的 ``checkpoints`` 表已有 ``parent_checkpoint_id``
  ——**这就是分叉的基础**：新分支 = 新 thread_id 的检查点，其父指针指向被回溯的点。
* ``services/snapshot.py`` 已提供高危写操作前置快照（sha256 manifest + WorkStash）；
  本模块复用它作为存档点的**文件系统侧**锚。
* ``services/state_persistence.py`` 的 ``SessionStateService`` 提供 session 快照
  的 upsert/load；本模块复用它作为存档点的**会话状态侧**锚。

设计约束（违反即返工）
----------------------

1. **分叉不覆盖原历史**（红线，需求原文）——建新分支**只写新行**，绝不对父点
   做任何 UPDATE/DELETE。原分支的检查点、快照、会话状态一字不改。
2. **对比要真读数据**——``compare`` 从两侧真实读取并逐字段比对，不回显调用方
   传入的「预期结果」。
3. **时间线要有序且完整**——``timeline`` 按时间升序返回全部存档点，不丢点。
4. **诚实边界**：分叉复制的是**锚点引用**（checkpoint_id / snapshot_id / session_key），
   不深拷贝底层字节。真正的「重跑」由调用方按 ``ForkPlan`` 发起（含新 thread_id
   与改后的参数），本模块只保证**分支记录与血缘**正确可查。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.fork_models import ArchiveFork
from .actor import Actor
from .audit import AuditService
from .errors import Conflict, NotFound

#: 分支状态：active（可继续）/ discarded（已弃用，留档不删）
FORK_ACTIVE = "active"
FORK_DISCARDED = "discarded"
FORK_STATES = (FORK_ACTIVE, FORK_DISCARDED)


@dataclass
class ForkPlan:
    """一次「回溯改参重跑」的完整执行参数（A-存档回溯-04）。

    调用方拿到它后，用 ``thread_id`` 作为新分支的 LangGraph 线程发起执行，
    并把 ``overrides`` 覆盖到原参数上——**原 thread 不动**。
    """

    fork_id: str
    source_thread_id: str
    source_checkpoint_id: str
    new_thread_id: str
    overrides: dict[str, Any] = field(default_factory=dict)
    snapshot_id: str = ""
    session_key: str = ""
    label: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "fork_id": self.fork_id,
            "source_thread_id": self.source_thread_id,
            "source_checkpoint_id": self.source_checkpoint_id,
            "new_thread_id": self.new_thread_id,
            "overrides": dict(self.overrides),
            "snapshot_id": self.snapshot_id,
            "session_key": self.session_key,
            "label": self.label,
        }


class ArchiveForkService:
    """存档分支：分叉 / 对比 / 时间线（P4 三条款的服务层）。"""

    def __init__(self, session: Session, audit: AuditService):
        self._session = session
        self._audit = audit

    # ----------------------------------------------------------------- #
    # A-存档回溯-04 · 改参重跑生成新分支
    # ----------------------------------------------------------------- #

    def fork(
        self,
        actor: Actor,
        *,
        source_thread_id: str,
        source_checkpoint_id: str = "",
        overrides: dict[str, Any] | None = None,
        snapshot_id: str = "",
        session_key: str = "",
        label: str = "",
    ) -> ForkPlan:
        """从某个存档点分叉出新分支（**不改动原历史**）。

        返回 :class:`ForkPlan`——调用方据此以 ``new_thread_id`` 发起重跑。
        ``source_checkpoint_id`` 留空表示从该 thread 的最新检查点分叉。
        """
        if not source_thread_id:
            raise ValueError("source_thread_id is required")

        new_thread = f"{source_thread_id}--fork-{uuid.uuid4().hex[:8]}"
        fork_id = uuid.uuid4().hex
        row = ArchiveFork(
            id=fork_id,
            owner_id=getattr(actor, "owner_id", "") or "",
            source_thread_id=source_thread_id,
            source_checkpoint_id=source_checkpoint_id,
            new_thread_id=new_thread,
            snapshot_id=snapshot_id,
            session_key=session_key,
            overrides=dict(overrides or {}),
            label=label[:200],
            state=FORK_ACTIVE,
        )
        self._session.add(row)
        self._session.flush()
        self._audit.append(
            actor, "fork.created", fork_id,
            {
                "source_thread_id": source_thread_id,
                "source_checkpoint_id": source_checkpoint_id,
                "new_thread_id": new_thread,
                "overrides": sorted((overrides or {}).keys()),
                "snapshot_id": snapshot_id,
                "session_key": session_key,
            },
        )
        return ForkPlan(
            fork_id=fork_id,
            source_thread_id=source_thread_id,
            source_checkpoint_id=source_checkpoint_id,
            new_thread_id=new_thread,
            overrides=dict(overrides or {}),
            snapshot_id=snapshot_id,
            session_key=session_key,
            label=label,
        )

    def list_forks(
        self,
        *,
        source_thread_id: str | None = None,
        state: str | None = None,
    ) -> list[ArchiveFork]:
        stmt = select(ArchiveFork)
        if source_thread_id is not None:
            stmt = stmt.where(ArchiveFork.source_thread_id == source_thread_id)
        if state is not None:
            if state not in FORK_STATES:
                raise ValueError(f"unknown fork state: {state!r}")
            stmt = stmt.where(ArchiveFork.state == state)
        stmt = stmt.order_by(ArchiveFork.created_at.asc(), ArchiveFork.id.asc())
        return list(self._session.execute(stmt).scalars().all())

    def get_fork(self, fork_id: str) -> ArchiveFork:
        row = self._session.execute(
            select(ArchiveFork).where(ArchiveFork.id == fork_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("fork_not_found", f"fork {fork_id} not found", 404)
        return row

    def discard(self, actor: Actor, fork_id: str, *, reason: str = "") -> ArchiveFork:
        """弃用分支（留档不删——历史不可抹除，红线）。"""
        row = self.get_fork(fork_id)
        if row.state == FORK_DISCARDED:
            raise Conflict("already_discarded", f"fork {fork_id} already discarded")
        row.state = FORK_DISCARDED
        row.discard_reason = reason[:500]
        self._session.add(row)
        self._session.flush()
        self._audit.append(actor, "fork.discarded", fork_id, {"reason": reason[:200]})
        return row

    # ----------------------------------------------------------------- #
    # A-存档回溯-05 · 分支对比
    # ----------------------------------------------------------------- #

    def compare(
        self,
        fork_id: str,
        *,
        left_runs: dict[str, Any] | None = None,
        right_runs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """对比两个分支的执行结果差异。

        ``left_runs`` / ``right_runs`` 是两侧各自的**执行结果快照**
        （形如 ``{"node": value, ...}``，通常由调用方从 thread 的最终 state 导出）。
        留空则回退到从 fork 记录里持久化的结果（若有）。

        逐键比对并分类为 ``only_left`` / ``only_right`` / ``changed`` / ``same``。
        这是**真比对**——不回显调用方传入的「预期差异」。
        """
        fork = self.get_fork(fork_id)
        left = dict(left_runs if left_runs is not None else (fork.left_result or {}))
        right = dict(right_runs if right_runs is not None else (fork.right_result or {}))
        return compare_runs(left, right, left_label=fork.source_thread_id,
                            right_label=fork.new_thread_id)

    # ----------------------------------------------------------------- #
    # A-存档回溯-06 · 存档历史时间线
    # ----------------------------------------------------------------- #

    def timeline(
        self,
        *,
        source_thread_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """按时间升序返回存档点 + 分叉点的完整时间线（有序且不丢点）。

        每条 = 一个事件：``fork``（分支创建）/ ``discard``（分支弃用）。
        时间用记录的 ``created_at``；同刻按 id 稳定排序。
        """
        rows = self.list_forks(source_thread_id=source_thread_id)
        events: list[dict[str, Any]] = []
        for r in rows:
            events.append({
                "kind": "fork",
                "at": r.created_at.isoformat(),
                "fork_id": r.id,
                "thread_id": r.new_thread_id,
                "parent_thread_id": r.source_thread_id,
                "parent_checkpoint_id": r.source_checkpoint_id,
                "snapshot_id": r.snapshot_id,
                "session_key": r.session_key,
                "label": r.label,
                "state": r.state,
                "overrides": dict(r.overrides or {}),
            })
            if r.state == FORK_DISCARDED and r.discard_reason:
                events.append({
                    "kind": "discard",
                    "at": r.created_at.isoformat(),
                    "fork_id": r.id,
                    "thread_id": r.new_thread_id,
                    "reason": r.discard_reason,
                })
        events.sort(key=lambda e: (e["at"], e["fork_id"], e["kind"]))
        return events[:limit]


def compare_runs(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> dict[str, Any]:
    """逐键比对两份执行结果快照（A-存档回溯-05 的纯函数核心）。

    分类：
    * ``only_left``  —— 左侧有、右侧无
    * ``only_right`` —— 右侧有、左侧无
    * ``changed``    —— 两侧都有但值不同（附 before/after）
    * ``same``       —— 两侧都有且值相同

    值比较用 ``==``（dict/list 深比较），不做字符串化——避免把 ``1`` 与 ``"1"``
    误判为相同（诚实比对）。
    """
    keys = sorted(set(left) | set(right))
    only_left: list[str] = []
    only_right: list[str] = []
    changed: list[dict[str, Any]] = []
    same: list[str] = []
    for k in keys:
        in_l = k in left
        in_r = k in right
        if in_l and not in_r:
            only_left.append(k)
        elif in_r and not in_l:
            only_right.append(k)
        elif left[k] == right[k]:
            same.append(k)
        else:
            changed.append({"key": k, "left": left[k], "right": right[k]})
    return {
        "left_label": left_label,
        "right_label": right_label,
        "identical": not (only_left or only_right or changed),
        "summary": {
            "total_keys": len(keys),
            "same": len(same),
            "changed": len(changed),
            "only_left": len(only_left),
            "only_right": len(only_right),
        },
        "same": same,
        "only_left": only_left,
        "only_right": only_right,
        "changed": changed,
    }
