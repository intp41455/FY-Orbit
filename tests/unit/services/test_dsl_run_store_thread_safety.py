"""DslRunStore 并发安全（差距清单 G2-i）：纯内存容器加锁。

背景：``DslRunStore`` 的三个可变容器 ``_runs`` / ``_owners`` / ``_order`` 此前
**无任何锁保护**。后续「后台线程执行 run 并写 store + API 线程读 store」的方案
要求这些读写要么原子、要么受同一把锁守护，否则会出现读到半写入状态的中间态。

两个断言各司其职：

* :func:`test_store_has_a_lock` —— **确定性**。实例必须持有一把真实的
  ``threading`` 锁（``RLock``）。旧代码没有 ``_lock`` 属性，此断言必然红。
* :func:`test_concurrent_save_and_read_is_consistent` —— **尽力而为**。并发
  压测下检验「内存列出的 run 必可读回」这一不变量。受 GIL 影响，旧代码也可能
  侥幸通过，故仅作辅助证据（真正打红依赖上面的确定性断言）。
"""

from __future__ import annotations

import threading

from find_yourself.services.dsl_canvas import DslRunStore, RunResult


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


def _result(run_id: str) -> RunResult:
    return RunResult(run_id=run_id, status="succeeded", doc=_doc(run_id),
                     output=run_id, created_at="2026-01-01T00:00:00+00:00")


def test_store_has_a_lock() -> None:
    """确定性：store 必须用一把真实的锁守护可变容器。"""
    store = DslRunStore()
    lock = getattr(store, "_lock", None)
    assert lock is not None, "DslRunStore 未持有锁（_lock 缺失），并发读写无保护"
    assert isinstance(lock, type(threading.RLock())), \
        f"_lock 必须是 threading 的锁，实际为 {type(lock)!r}"


def test_concurrent_save_and_read_is_consistent() -> None:
    """尽力而为：多线程 save/read 交错后，内存不变量不崩。

    不变量：``list_ids()`` 中的每个 run 都能通过 ``get()`` 读回（不出现
    ``_order`` 已登记、``_runs`` 尚未写入的中间态）；容量上限下最终快照长度
    恰好等于 ``max_in_memory``。
    """
    max_in_memory = 500
    n_threads, per_thread = 8, 200  # 总量 1600 > 500，强制触发淘汰路径
    store = DslRunStore(max_in_memory=max_in_memory)
    start = threading.Barrier(n_threads)

    def worker(tid: int) -> None:
        start.wait()
        for i in range(per_thread):
            rid = f"run-{tid}-{i}"
            store.save(_result(rid), owner=f"owner-{tid}")
            # 交错读取：与写入并发，加锁后二者互斥
            for existing in store.list_ids():
                assert store.get(existing) is not None
            assert store.get_owned(rid, f"owner-{tid}") is not None

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = store.list_ids()
    assert len(ids) == max_in_memory
    for rid in ids:
        assert store.get(rid) is not None
