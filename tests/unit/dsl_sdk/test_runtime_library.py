"""B4 · 导出产物**自包含可运行**：真实子进程验证。

被测不变量（有替代 ≠ 达标：既有「往返一致」测试不等于可运行）：

1. ``write_export_bundle`` 写出的目录，用 **python 子进程**在临时目录里
   ``import find_yourself_dsl`` + ``import flow_restricted`` 并真实跑通
   （历史缺陷：导出脚本 import 不存在的 ``find_yourself_dsl`` →
   ModuleNotFoundError，import 即崩）。
2. 同源一致性：子进程跑导出产物 == 平台 ``run_dsl`` 跑同一张画布。
3. 运行库与平台的**漂移护栏**：参数契约表、动词清单逐项深度相等。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

#: 仓库根（find_yourself_dsl/ 运行库所在；src 布局回推三级）。
REPO_ROOT = Path(__file__).resolve().parents[3]

from find_yourself.services.dsl_canvas import (
    AGGREGATE_OPS,
    CONDITION_OPS,
    INPUT_KINDS,
    MERGE_OPS,
    OUTPUT_FORMATS,
    TRANSFORM_VERBS,
    VERB_REGISTRY,
    NODE_PARAMS_SCHEMAS as PLATFORM_NODE_SCHEMAS,
    canonical_dsl,
    dsl_digest as platform_digest,
    run_dsl,
)
from find_yourself.services.dsl_code_export import (
    DSL_RUNTIME_PACKAGE,
    export_dsl_code,
    parse_dsl_code,
    write_export_bundle,
)

import find_yourself_dsl as fy
from find_yourself_dsl._schema import (    AGGREGATE_OPS as FY_AGGREGATE_OPS,
    CONDITION_OPS as FY_CONDITION_OPS,
    INPUT_KINDS as FY_INPUT_KINDS,
    MERGE_OPS as FY_MERGE_OPS,
    NODE_PARAMS_SCHEMAS as FY_NODE_SCHEMAS,
    OUTPUT_FORMATS as FY_OUTPUT_FORMATS,
    TRANSFORM_PARAMS_SCHEMAS as FY_TRANSFORM_SCHEMAS,
    TRANSFORM_VERBS as FY_TRANSFORM_VERBS,
)

from _dsl_docs import doc_of, lit, out, xf

_RUNNER = """
import json, sys
import flow_restricted, find_yourself_dsl
try:
    r = find_yourself_dsl.run()
except find_yourself_dsl.DslSuspended as s:
    print(json.dumps({"status": "suspended", "node_id": s.node_id,
                      "checkpoint": s.checkpoint}, ensure_ascii=False))
    sys.exit(0)
print(json.dumps({"status": r.status, "output": r.output, "error": r.error},
                 ensure_ascii=False, default=str))
"""


def _run_subprocess(cwd: Path) -> dict:
    proc = subprocess.run([sys.executable, "-c", _RUNNER], cwd=cwd,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"子进程失败: {proc.stderr}"
    return json.loads(proc.stdout)


def _pipeline_doc() -> dict:
    return doc_of(
        [lit([{"name": "张三", "score": 90}, {"name": "李四", "score": 40}]),
         xf("hi", "filter", field="score", op="gt", value=60),
         xf("tag", "map", op="set", field="grade", value="优:{name}"),
         xf("art", "artifact", name="报表", kind="table"),
         out()],
        [{"from": "src", "to": "hi"}, {"from": "hi", "to": "tag"},
         {"from": "tag", "to": "art"}, {"from": "art", "to": "out"}],
    )


# =========================================================================== #
# 1. 子进程真跑（B4 的验收核心）
# =========================================================================== #
class TestExportedBundleRunsInSubprocess:
    def test_bundle_imports_and_runs_in_fresh_subprocess(self, tmp_path):
        """AC：导出产物在临时目录用 python 子进程真实 import 并跑通。"""
        info = write_export_bundle(_pipeline_doc(), tmp_path)
        assert (Path(info["directory"]) / info["entry"]).is_file()
        assert (tmp_path / DSL_RUNTIME_PACKAGE / "__init__.py").is_file()
        payload = _run_subprocess(tmp_path)
        assert payload["status"] == "succeeded", payload["error"]
        assert payload["output"] == {
            "artifact": "报表", "kind": "table",
            "content": [{"name": "张三", "score": 90, "grade": "优:张三"}],
        }

    def test_b4_regression_import_alone_no_longer_crashes(self, tmp_path):
        """历史缺陷回归钉：只 import 导出脚本（不调用任何东西）不再 ModuleNotFoundError。"""
        write_export_bundle(_pipeline_doc(), tmp_path)
        proc = subprocess.run(
            [sys.executable, "-c", "import flow_restricted"], cwd=tmp_path,
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert "ModuleNotFoundError" not in proc.stderr

    def test_subprocess_result_matches_canvas_execution(self, tmp_path):
        """同源一致性：子进程跑导出产物 == 平台执行同一张画布（逐字节同输出）。"""
        doc = _pipeline_doc()
        platform = run_dsl(doc)
        assert platform.status == "succeeded"
        write_export_bundle(doc, tmp_path)
        payload = _run_subprocess(tmp_path)
        assert payload["status"] == platform.status
        assert payload["output"] == platform.output

    def test_subprocess_honors_edge_conditions_and_text_output(self, tmp_path):
        """条件边 + text 输出在独立运行库同样成立（与平台一致）。"""
        doc = doc_of(
            [lit({"mode": "fast"}),
             xf("br", "branch", field="mode", op="eq", value="fast",
                then_label="fast", else_label="slow"),
             xf("fast", "template", template="快速:{value.mode}"),
             xf("slow", "template", template="慢速:{value.mode}"),
             out(fmt="text")],
            [{"from": "src", "to": "br"},
             {"from": "br", "to": "fast",
              "condition": {"field": "branch", "op": "eq", "value": "fast"}},
             {"from": "br", "to": "slow",
              "condition": {"field": "branch", "op": "eq", "value": "slow"}},
             {"from": "fast", "to": "out"}, {"from": "slow", "to": "out"}],
        )
        platform = run_dsl(doc)
        write_export_bundle(doc, tmp_path)
        payload = _run_subprocess(tmp_path)
        assert payload["output"] == platform.output == "快速:fast"

    def test_subprocess_confirm_suspends_not_fails(self, tmp_path):
        """挂起是控制流信号：独立运行库走到 confirm 抛 DslSuspended（不假装失败）。"""
        doc = doc_of(
            [lit({"amount": 100}),
             xf("cf", "confirm", prompt="确认发布？", role="owner"),
             out()],
            [{"from": "src", "to": "cf"}, {"from": "cf", "to": "out"}],
        )
        write_export_bundle(doc, tmp_path)
        payload = _run_subprocess(tmp_path)
        assert payload["status"] == "suspended"
        assert payload["node_id"] == "cf"
        assert payload["checkpoint"].endswith("#cf")

    def test_subprocess_agent_fails_honestly_without_resolver(self, tmp_path):
        doc = doc_of([lit({"q": 1}), xf("ag", "agent", agent="summarizer"), out()],
                     [{"from": "src", "to": "ag"}, {"from": "ag", "to": "out"}])
        write_export_bundle(doc, tmp_path)
        payload = _run_subprocess(tmp_path)
        assert payload["status"] == "failed"
        assert "解析器" in (payload["error"] or "")

    def test_export_payload_ships_runtime_files(self):
        """HTTP 导出载荷同样自包含：runtime_files 随结果交付。"""
        exported = export_dsl_code(_pipeline_doc())
        assert exported["runtime_dirname"] == DSL_RUNTIME_PACKAGE
        files = exported["runtime_files"]
        assert f"{DSL_RUNTIME_PACKAGE}/__init__.py" in files
        assert all(content.strip() for content in files.values())
        # 既有键不受影响（往返一致不回归）
        assert parse_dsl_code(exported["code"]) == canonical_dsl(_pipeline_doc())

    def test_export_from_missing_runtime_dir_fails_loudly(self, tmp_path, monkeypatch):
        """运行库源码找不到时**明确报错**，绝不导出半成品。

        env ``FY_DSL_RUNTIME_DIR`` 是显式覆盖：指向不存在的目录是配置错误，
        必须报错而不是悄悄回落。
        """
        monkeypatch.setenv("FY_DSL_RUNTIME_DIR", str(tmp_path / "不存在"))
        with pytest.raises(Exception, match="find_yourself_dsl"):
            export_dsl_code(_pipeline_doc())


# =========================================================================== #
# 2. 独立运行库语义（进程内直测）
# =========================================================================== #
class TestRuntimeLibrarySemantics:
    def test_runtime_matches_platform_on_kitchen_pipeline(self):
        doc = _pipeline_doc()
        platform = run_dsl(doc)
        result = fy.run(doc)
        assert result.status == platform.status == "succeeded"
        assert result.output == platform.output

    def test_text_lines_input(self):
        doc = doc_of([{"id": "src", "type": "input",
                       "params": {"kind": "text_lines", "value": "a\n\n b"}},
                      out(fmt="text")],
                     [{"from": "src", "to": "out"}])
        assert fy.run(doc).output == run_dsl(doc).output == "a\n b"

    def test_cycle_detection(self):
        doc = doc_of([lit(1), out()], [{"from": "src", "to": "out"},
                                       {"from": "out", "to": "src"}])
        with pytest.raises(fy.DslValidationError, match="环"):
            fy.run(doc)

    def test_unknown_verb_rejected(self):
        doc = doc_of([{"id": "t", "type": "transform", "verb": "exec"}, out()],
                     [{"from": "t", "to": "out"}])
        with pytest.raises(fy.DslValidationError, match="verb"):
            fy.run(doc)

    def test_bad_version_rejected(self):
        with pytest.raises(fy.DslValidationError, match="version"):
            fy.Flow(version="2")

    def test_duplicate_node_id_rejected_in_builder(self):
        fy.Flow(version="1")
        fy.input_node("src", kind="literal", value=1)
        with pytest.raises(fy.DslValidationError, match="重复"):
            fy.input_node("src", kind="literal", value=2)
        fy.reset_current_flow()

    def test_digest_matches_platform(self):
        """独立运行库与平台的规范化摘要同算法（「人所见即所批」可跨载体复用）。"""
        doc = _pipeline_doc()
        assert fy.dsl_digest(doc) == platform_digest(doc)

    def test_merge_and_aggregate_semantics(self):
        doc = doc_of(
            [lit([{"v": 1}, {"v": 2}]),
             xf("left", "template", template="L{v}"),
             xf("m", "merge", mode="concat"),
             xf("agg", "aggregate", op="count"),
             out()],
            [{"from": "src", "to": "left"}, {"from": "left", "to": "m"},
             {"from": "m", "to": "agg"}, {"from": "agg", "to": "out"}])
        assert fy.run(doc).output == run_dsl(doc).output == 2


# =========================================================================== #
# 3. 漂移护栏：运行库契约镜像 == 平台唯一真源（漂移即红）
# =========================================================================== #
class TestRuntimeParityGuard:
    def test_transform_verb_list_identical_in_order(self):
        assert FY_TRANSFORM_VERBS == tuple(VERB_REGISTRY)
        assert FY_TRANSFORM_VERBS == TRANSFORM_VERBS

    def test_transform_params_schemas_deep_equal(self):
        for verb in VERB_REGISTRY:
            assert FY_TRANSFORM_SCHEMAS[verb] \
                == VERB_REGISTRY[verb].params_schema, verb

    def test_node_params_schemas_deep_equal(self):
        for ntype in ("input", "output"):
            assert FY_NODE_SCHEMAS[ntype] == PLATFORM_NODE_SCHEMAS[ntype]

    def test_enum_constants_identical(self):
        assert FY_AGGREGATE_OPS == AGGREGATE_OPS
        assert FY_CONDITION_OPS == CONDITION_OPS
        assert FY_INPUT_KINDS == INPUT_KINDS
        assert FY_OUTPUT_FORMATS == OUTPUT_FORMATS
        assert FY_MERGE_OPS == MERGE_OPS

    def test_runtime_has_no_third_party_imports(self):
        """零第三方依赖：运行库源码里不得出现平台内模块或三方库 import。"""
        runtime_dir = REPO_ROOT / "find_yourself_dsl"
        allowed = {"__future__", "json", "re", "uuid", "hashlib", "datetime",
                   "typing", "dataclasses", "find_yourself_dsl",
                   "_engine", "_errors", "_graph", "_schema"}  # 相对导入
        for path in sorted(runtime_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    module = stripped.split()[1].lstrip(".").split(".")[0] \
                        .rstrip(",")
                    assert module in allowed, \
                        f"{path.name}: 非预期 import {module!r}"
