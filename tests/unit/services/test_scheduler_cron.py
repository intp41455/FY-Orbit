"""P5 单测 · 定时/周期任务调度（A-竞品借鉴-03）+ Harness 路线图（A-Agent运行时-15）。

覆盖：interval/cron 触发器构造、非法参数拒绝、到点桥接到统一调度中心、
无路由时如实记录 submit_failed（不假装成功）、移除任务、20 阶路线图骨架。
定时测试用 1 秒 interval + eventually 轮询，不用长 sleep。
"""

from __future__ import annotations

import time

import pytest

from find_yourself.services.scheduler.core import (
    CHANNEL_INTERNAL_AGENT,
    DispatchRequest,
    UnifiedScheduler,
)
from find_yourself.services.scheduler.cron import (
    TRIGGER_CRON,
    TRIGGER_INTERVAL,
    CronJobSpec,
    CronScheduler,
    HarnessStage,
    default_harness_roadmap,
)


def eventually(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _req(tag: str = "x") -> DispatchRequest:
    return DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={"v": tag})


def _sch_with_counter() -> tuple[UnifiedScheduler, list]:
    calls: list = []
    sch = UnifiedScheduler(max_concurrent=4)

    def rec(req: DispatchRequest):
        calls.append(req.payload.get("v"))
        return "ok"

    sch.register_simple_worker("rec", CHANNEL_INTERNAL_AGENT, rec, max_parallel=4)
    return sch, calls


# --------------------------------------------------------------------------- #
# 触发器构造与校验
# --------------------------------------------------------------------------- #


def test_interval_spec_requires_positive_seconds():
    with pytest.raises(ValueError):
        CronJobSpec(job_id="j", request=_req(), trigger_kind=TRIGGER_INTERVAL,
                    seconds=0).validate()
    with pytest.raises(ValueError):
        CronJobSpec(job_id="j", request=_req(), trigger_kind=TRIGGER_INTERVAL,
                    seconds=-5).validate()


def test_cron_spec_requires_expression():
    with pytest.raises(ValueError):
        CronJobSpec(job_id="j", request=_req(), trigger_kind=TRIGGER_CRON,
                    cron_expr="").validate()


def test_unknown_trigger_kind_rejected():
    with pytest.raises(ValueError):
        CronJobSpec(job_id="j", request=_req(), trigger_kind="weekly").validate()


def test_empty_job_id_rejected():
    with pytest.raises(ValueError):
        CronJobSpec(job_id="  ", request=_req()).validate()


def test_interval_trigger_builds():
    spec = CronJobSpec(job_id="j", request=_req(), trigger_kind=TRIGGER_INTERVAL,
                       seconds=30)
    trig = spec.build_trigger()
    assert trig is not None


def test_cron_trigger_parses_five_fields():
    spec = CronJobSpec(job_id="j", request=_req(), trigger_kind=TRIGGER_CRON,
                       cron_expr="0 9 * * 1-5")
    trig = spec.build_trigger()
    assert trig is not None


def test_bad_cron_expression_raises_from_apscheduler():
    """cron 语法交给 APScheduler 报错——本模块不自研解析器（附录 C 已拍板）。"""
    spec = CronJobSpec(job_id="j", request=_req(), trigger_kind=TRIGGER_CRON,
                       cron_expr="not a cron")
    with pytest.raises(Exception):
        spec.build_trigger()


# --------------------------------------------------------------------------- #
# 桥接到统一调度中心
# --------------------------------------------------------------------------- #


def test_run_immediately_fires_on_registration():
    sch, calls = _sch_with_counter()
    cron = CronScheduler(sch)
    cron.add_job(CronJobSpec(
        job_id="j1", request=_req("A"), trigger_kind=TRIGGER_INTERVAL,
        seconds=3600, run_immediately=True,
    ))
    assert eventually(lambda: calls == ["A"])
    assert cron.fire_log()[0]["status"] == "submitted"


def test_interval_job_fires_repeatedly():
    sch, calls = _sch_with_counter()
    cron = CronScheduler(sch)
    cron.start()
    try:
        cron.add_job(CronJobSpec(
            job_id="j2", request=_req("B"), trigger_kind=TRIGGER_INTERVAL, seconds=1,
        ))
        assert eventually(lambda: len(calls) >= 2, timeout=8.0)
    finally:
        cron.shutdown()


def test_submit_failure_is_recorded_not_masked():
    """★ 无路由时到点提交会失败——必须如实记 submit_failed，不许静默吞掉。"""
    sch = UnifiedScheduler(max_concurrent=1)  # 无 worker
    cron = CronScheduler(sch)
    cron.add_job(CronJobSpec(
        job_id="j3", request=_req("C"), trigger_kind=TRIGGER_INTERVAL,
        seconds=3600, run_immediately=True,
    ))
    assert eventually(lambda: len(cron.fire_log()) >= 1)
    entry = cron.fire_log()[0]
    assert entry["status"] == "submit_failed"
    assert "NoRouteError" in entry["error"]


def test_remove_job():
    sch, _calls = _sch_with_counter()
    cron = CronScheduler(sch)
    cron.add_job(CronJobSpec(job_id="j4", request=_req(), seconds=3600))
    assert len(cron.jobs()) == 1
    assert cron.remove_job("j4") is True
    assert cron.jobs() == []


def test_remove_missing_job_returns_false():
    sch, _calls = _sch_with_counter()
    cron = CronScheduler(sch)
    assert cron.remove_job("nope") is False


def test_add_job_is_idempotent_by_id():
    sch, _calls = _sch_with_counter()
    cron = CronScheduler(sch)
    cron.add_job(CronJobSpec(job_id="dup", request=_req(), seconds=3600))
    cron.add_job(CronJobSpec(job_id="dup", request=_req(), seconds=60))
    assert len(cron.jobs()) == 1


def test_start_and_shutdown_lifecycle():
    sch, _calls = _sch_with_counter()
    cron = CronScheduler(sch)
    assert cron.running is False
    cron.start()
    assert cron.running is True
    cron.shutdown()
    assert cron.running is False


# --------------------------------------------------------------------------- #
# A-Agent运行时-15 · Harness 路线图
# --------------------------------------------------------------------------- #


def test_harness_roadmap_has_twenty_stages():
    stages = default_harness_roadmap()
    assert len(stages) == 20
    assert stages[0].stage_id == "s01"
    assert stages[-1].stage_id == "s20"


def test_harness_stages_are_sequential_and_unique():
    ids = [s.stage_id for s in default_harness_roadmap()]
    assert ids == [f"s{i:02d}" for i in range(1, 21)]
    assert len(set(ids)) == 20


def test_harness_stage_starts_undone():
    assert all(s.done is False for s in default_harness_roadmap())


def test_harness_stage_acceptance_is_explicitly_marked_tbd():
    """诚实边界：验收门文本标注「待细化」，不冒充「20 阶已定义完毕」。"""
    stages = default_harness_roadmap()
    assert all("待细化" in s.acceptance for s in stages)


def test_harness_stage_dataclass():
    s = HarnessStage(stage_id="s01", name="单步工具调用")
    assert s.done is False
    assert s.trigger == ""
