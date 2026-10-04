"""P4 · 工程脚手架导出测试（需求 13，依 ADR-08）。

三条门禁判据逐条覆盖：
① 同 IR 两次导出**逐字节一致**；
② 导出物不含任何凭据特征串（生成器 fail closed）；
③ 导出工程能被 Python 语法解析（``ast.parse``）。

外加：契约 v1.0 的清单 schema 判据、runner 的真实可运行性（subprocess 跑通）
与诚实边界（不支持动词显式失败）。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.project_scaffold import (
    CREDENTIAL_PATTERNS,
    build_asset_manifest,
    export_project_scaffold,
)

_THREE_NODE_DOC = {
    "version": "1",
    "nodes": [
        {"id": "src", "type": "input",
         "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]}},
        {"id": "tpl", "type": "transform", "verb": "template",
         "params": {"template": "你好，{name}！你今年 {age} 岁。"}},
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "src", "to": "tpl"}, {"from": "tpl", "to": "out"}],
}


# ---- 门禁判据 ①：确定性 -------------------------------------------------------
def test_same_ir_exports_byte_identical():
    """门禁核心：同一份 DSL 导出两次，所有文件逐字节一致。"""
    first = export_project_scaffold(_THREE_NODE_DOC)
    second = export_project_scaffold(_THREE_NODE_DOC)
    assert set(first) == set(second)
    for path in first:
        assert first[path] == second[path], path


def test_export_is_stable_across_key_order_in_input():
    """输入文档的键序不同（语义相同）→ 导出仍一致（canonical 归一）。

    注意：节点/边在**列表中的顺序**是语义（merge/branch 依赖它），不属于
    「键序」；本用例只重排字典键的插入顺序与顶层键序。
    """
    reordered = {
        "version": "1",
        "nodes": [
            {"params": _THREE_NODE_DOC["nodes"][0]["params"], "type": "input", "id": "src"},
            {"verb": "template", "params": {"template": "你好，{name}！你今年 {age} 岁。"},
             "type": "transform", "id": "tpl"},
            {"params": {"format": "text"}, "type": "output", "id": "out"},
        ],
        "edges": [
            {"to": "tpl", "from": "src"},
            {"to": "out", "from": "tpl"},
        ],
    }
    a = export_project_scaffold(_THREE_NODE_DOC)
    b = export_project_scaffold(reordered)
    for path in a:
        assert a[path] == b[path], path


def test_export_contains_no_timestamps_or_random_looking_ids():
    for content in export_project_scaffold(_THREE_NODE_DOC).values():
        assert "2026-" not in content or "2026-01-01"  # 无生成时间戳字段
        assert "generated_at" not in content


# ---- 门禁判据 ②：零凭据 -------------------------------------------------------
def test_export_contains_no_credential_patterns():
    joined = "\n".join(export_project_scaffold(_THREE_NODE_DOC).values()).lower()
    for pattern in CREDENTIAL_PATTERNS:
        assert pattern.lower() not in joined, pattern


def test_export_rejects_credential_like_params():
    """DSL 参数里夹带疑似凭据 → 生成器 fail closed，绝不半成品。"""
    dirty = {
        "version": "1",
        "nodes": [
            {"id": "src", "type": "input",
             "params": {"kind": "literal", "value": {"api_key": "sk-123"}}},
            {"id": "out", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [{"from": "src", "to": "out"}],
    }
    with pytest.raises(ValidationFailed) as err:
        export_project_scaffold(dirty)
    assert err.value.code == "scaffold_credential_detected"


# ---- 门禁判据 ③：语法可解析 ---------------------------------------------------
def test_all_python_files_parse():
    files = export_project_scaffold(_THREE_NODE_DOC)
    py_files = [p for p in files if p.endswith(".py")]
    assert py_files, "脚手架必须包含 Python 源文件"
    for path in py_files:
        ast.parse(files[path], filename=path)  # 抛 SyntaxError 即失败


def test_manifest_is_valid_json_with_frozen_schema():
    files = export_project_scaffold(_THREE_NODE_DOC)
    manifest = json.loads(files["assets.manifest.json"])
    assert set(manifest) == {"manifest_version", "generator", "source", "assets"}
    assert manifest["manifest_version"] == "1"
    assert re.compile(r"^[0-9a-f]{12}$").match(manifest["source"]["dsl_digest"])
    for asset in manifest["assets"]:
        assert set(asset) == {"asset_id", "kind", "path", "pixel_size",
                              "anchor", "frames", "hash"}
        assert asset["hash"] is None  # 平台不伪造产出
        assert asset["kind"] in ("sprite", "tileset", "palette", "audio", "font")
        assert asset["path"].startswith("assets/")
        assert re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$").match(asset["asset_id"])


def test_manifest_deterministic_derivation_sorted_and_unique():
    doc = {
        "version": "1",
        "nodes": [
            {"id": "out-b", "type": "output", "params": {"format": "text"}},
            {"id": "out-a", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [],
    }
    manifest = build_asset_manifest(doc if False else __import__(
        "find_yourself.services.dsl_canvas", fromlist=["canonical_dsl"]
    ).canonical_dsl(doc))
    ids = [a["asset_id"] for a in manifest["assets"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    # 每个 output 一个 sprite + 全局一个 palette（契约 §2 派生规则）
    assert ids == ["palette.project", "preview.out-a", "preview.out-b"]


def test_scaffold_writes_to_disk_and_runs(tmp_path):
    """真实可运行：写盘后以子进程执行 runner，输出与平台执行器一致。"""
    files = export_project_scaffold(_THREE_NODE_DOC)
    for rel, content in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "src" / "run.py")],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines() == ["你好，张三！你今年 34 岁。"]


def test_runner_fails_loudly_on_platform_only_verb(tmp_path):
    """诚实边界：agent/confirm 动词在工程外执行 → 显式报错退出（非零码）。"""
    doc = {
        "version": "1",
        "nodes": [
            {"id": "src", "type": "input", "params": {"kind": "literal", "value": [1]}},
            {"id": "ag", "type": "transform", "verb": "agent", "params": {"agent": "x"}},
            {"id": "out", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [{"from": "src", "to": "ag"}, {"from": "ag", "to": "out"}],
    }
    files = export_project_scaffold(doc)
    for rel, content in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "src" / "run.py")],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert "agent" in (proc.stderr or "")


def test_readme_documents_run_and_contract(tmp_path=None):
    readme = export_project_scaffold(_THREE_NODE_DOC)["README.md"]
    assert "python src/run.py" in readme
    assert "assets.manifest.json" in readme
    assert "ASSET_MANIFEST_CONTRACT" in readme
    assert "null" in readme  # 诚实边界声明


def test_invalid_dsl_rejected_no_half_scaffold():
    from find_yourself.services.dsl_canvas import DslValidationError

    with pytest.raises(DslValidationError):
        export_project_scaffold({"version": "9", "nodes": [], "edges": []})
    cyclic = {
        "version": "1",
        "nodes": [{"id": "a", "type": "output"}, {"id": "b", "type": "output"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    with pytest.raises(DslValidationError):
        export_project_scaffold(cyclic)


def test_empty_project_name_rejected():
    with pytest.raises(ValidationFailed):
        export_project_scaffold(_THREE_NODE_DOC, project_name="  ")


def test_large_document_deterministic_and_manifest_sorted():
    """大文档（30 个 output）导出确定性 + 清单派生规则仍成立。"""
    nodes = [{"id": f"out-{i:02d}", "type": "output", "params": {"format": "text"}}
             for i in range(30)]
    doc = {"version": "1", "nodes": nodes, "edges": []}
    first = export_project_scaffold(doc)
    second = export_project_scaffold(doc)
    assert first == second
    manifest = json.loads(first["assets.manifest.json"])
    ids = [a["asset_id"] for a in manifest["assets"]]
    assert ids == sorted(ids)
    assert sum(1 for a in manifest["assets"] if a["kind"] == "sprite") == 30
    assert sum(1 for a in manifest["assets"] if a["kind"] == "palette") == 1


def test_manifest_reader_rejects_unknown_major_version():
    """契约 §4：读到未来主版本的清单必须显式报错，绝不「尽力解析」。"""
    from find_yourself.services.project_scaffold import validate_manifest_v1

    good = build_asset_manifest(
        __import__("find_yourself.services.dsl_canvas", fromlist=["canonical_dsl"])
        .canonical_dsl(_THREE_NODE_DOC))
    validate_manifest_v1(good)  # v1 清单通过自检
    future = dict(good, manifest_version="2")
    with pytest.raises(ValidationFailed) as err:
        validate_manifest_v1(future)
    assert err.value.code == "manifest_version_unsupported"
