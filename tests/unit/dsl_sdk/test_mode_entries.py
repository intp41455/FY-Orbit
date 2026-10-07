"""A-三重模式-01/04 真落地验收：三入口各自产出**可运行的同一套 IR**。

骨架期的三个 builtin 入口 ``factory=None``，小白模式点进去没有东西。本文件把
「真落地」钉成可执行判据：

* 判据 1：``mode_overview()`` 三个模式齐全（``GET /api/dsl/modes`` 的 ``modes``）；
* 判据 2：小白起手模板能完成「模板起手 → 装配 → 运行」全流程；
* 判据 3：**同源** —— 同一张图经小白入口与技术入口产出的 IR 逐字节相同，
  且 ``validate_ir`` 判定一致（``POST /api/dsl-canvas/validate-ir`` 的后端实现）；
* 判据 4：企业入口的起手模板带 ``approval`` 治理节点，执行时**挂起**（控制流
  信号，不是失败）。
"""

from __future__ import annotations

import pytest

from find_yourself.services.dsl_canvas import DslSuspended
from find_yourself.services.dsl_ir import validate_ir
from find_yourself.services.dsl_sdk import (
    DEFAULT_TEMPLATE_IDS,
    MODE_TEMPLATES,
    AuthoringMode,
    CodeWorkflow,
    DslSdkError,
    build_mode_template,
    default_template_for,
    mode_entries,
    mode_overview,
    mode_template_catalog,
)


def _diags(doc):
    return [d.to_dict() for d in validate_ir(doc)]


class TestModeOverviewLanding:
    def test_three_modes_each_with_runnable_template(self):
        overview = mode_overview()
        assert [m["mode"] for m in overview] == \
            ["beginner", "technical", "enterprise"]
        for mode in overview:
            assert mode["templates"], f"{mode['mode']} 没有起手模板"
            ids = [t["template_id"] for t in mode["templates"]]
            assert mode["default_template"] in ids

    def test_every_builtin_entry_has_a_real_factory(self):
        """入口位不再是空壳：factory 必须存在，且产出可运行的 CodeWorkflow。"""
        for mode in AuthoringMode:
            builtins = [e for e in mode_entries(mode) if e.builtin]
            assert builtins, f"{mode.value} 没有 builtin 入口"
            for entry in builtins:
                assert entry.factory is not None, \
                    f"{entry.entry_id} 仍是骨架入口（factory=None）"
                assert isinstance(entry.factory(), CodeWorkflow)

    def test_api_returns_three_modes_with_templates(self, client, headers):
        r = client.get("/api/dsl/modes", headers=headers)
        assert r.status_code == 200, r.text
        modes = r.json()["modes"]
        assert len(modes) == 3
        for m in modes:
            assert m["templates"] and m["default_template"]

    def test_api_templates_carry_real_dsl(self, client, headers):
        """起手模板必须带**真图**：前端拿到的就是后端那份 IR，不是前端自建的假模板。"""
        r = client.get("/api/dsl/modes", headers=headers)
        seen = 0
        for m in r.json()["modes"]:
            for t in m["templates"]:
                dsl = t["dsl"]
                assert dsl["nodes"] and dsl["edges"], t["template_id"]
                # 前端「模板起手」载入后要能过校验，否则是死模板。
                assert _diags(dsl) == [], f"{t['template_id']} 的 dsl 有诊断"
                # 与注册表里真工厂现算的图逐字节一致（无第二套图定义）。
                assert dsl == MODE_TEMPLATES[t["template_id"]].build().doc
                seen += 1
        assert seen >= 3


class TestTemplateRegistry:
    def test_catalog_filtered_by_mode(self):
        for mode in AuthoringMode:
            items = mode_template_catalog(mode)
            assert items and all(i["mode"] == mode.value for i in items)

    def test_default_template_matches_registry(self):
        for mode in AuthoringMode:
            tid = default_template_for(mode)
            assert MODE_TEMPLATES[tid].mode is mode
            assert DEFAULT_TEMPLATE_IDS[mode] == tid

    def test_unknown_template_is_explicit_error(self):
        with pytest.raises(DslSdkError, match="未注册"):
            build_mode_template("no-such-template")

    def test_every_template_builds_a_valid_ir(self):
        for tid in MODE_TEMPLATES:
            wf = build_mode_template(tid)
            assert _diags(wf.doc) == [], f"{tid} 产出的 IR 有诊断"


class TestSameIrAcrossModes:
    """判据 3：同源 —— 三条入口产出的 IR 结构相同。"""

    def test_beginner_and_technical_produce_identical_ir(self):
        beginner = build_mode_template("beginner-starter")
        technical = build_mode_template("technical-pipeline")
        assert beginner.doc == technical.doc

    def test_validate_ir_verdict_identical(self):
        beginner = build_mode_template("beginner-starter")
        technical = build_mode_template("technical-pipeline")
        assert _diags(beginner.doc) == _diags(technical.doc)

    def test_enterprise_reuses_the_same_subgraph(self):
        """企业模板不是另起一张图：它包含起手图的全部节点与边。"""
        beginner = build_mode_template("beginner-starter")
        enterprise = build_mode_template("enterprise-governed")
        b_nodes = {(n["id"], n["type"]) for n in beginner.doc["nodes"]}
        e_nodes = {(n["id"], n["type"]) for n in enterprise.doc["nodes"]}
        assert b_nodes <= e_nodes
        b_edges = {(e["from"], e["to"]) for e in beginner.doc["edges"]}
        e_edges = {(e["from"], e["to"]) for e in enterprise.doc["edges"]}
        # 起手图的边除“label -> out”被治理节点接管外，全部保留。
        assert (b_edges - e_edges) == {("label", "out")}
        assert ("label", "govern") in e_edges and ("govern", "out") in e_edges


class TestBeginnerFlowIsRunnable:
    """判据 2：小白「模板起手 → 装配 → 运行」全流程可跑通。"""

    def test_beginner_default_runs_to_success(self):
        wf = build_mode_template("beginner-starter")
        result = wf.run()
        assert result.result.status == "succeeded"
        assert result.result.error is None
        # 空行被 filter 掉：两行留下，且都带上了「行：」标签。
        rows = result.result.output
        assert isinstance(rows, list) and len(rows) == 2
        assert all(str(r["line"]).startswith("行：") for r in rows)

    def test_beginner_uses_only_injection_free_nodes(self):
        """起手模板不得依赖注入式解析器（否则新手第一屏就诚实失败）。"""
        wf = build_mode_template("beginner-starter")
        assert {n["type"] for n in wf.doc["nodes"]} == \
            {"input", "transform", "output"}
        assert {n["verb"] for n in wf.doc["nodes"] if n["type"] == "transform"} \
            == {"filter", "map"}


class TestEnterpriseGovernanceEntry:
    """判据 4：企业入口以治理节点起手，执行即挂起等裁决。"""

    def test_enterprise_default_has_approval_node(self):
        wf = build_mode_template("enterprise-governed")
        approval = [n for n in wf.doc["nodes"]
                    if n.get("verb") == "approval"]
        assert len(approval) == 1
        assert approval[0]["params"]["op"] == "memory.upsert"

    def test_run_suspends_rather_than_fails(self):
        wf = build_mode_template("enterprise-governed")
        with pytest.raises(DslSuspended) as exc:
            wf.run()
        assert exc.value.node_id == "govern"
        assert exc.value.options
