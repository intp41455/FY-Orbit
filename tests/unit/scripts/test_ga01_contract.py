"""GA-01 Self-Test：契约门禁真会红，且有判别力（子进程调真实脚本）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ga01_contract.py"

THING_POST = {
    "requestBody": {"content": {"application/json": {"schema": {
        "$ref": "#/components/schemas/ThingIn"}}}},
    "responses": {"200": {"description": "ok"}},
}
BROKEN_POST = {
    "requestBody": {"content": {"application/json": {"schema": {
        "type": "object"}}}},
    "responses": {"200": {"description": "ok"}},
}
SPEC = {
    "paths": {
        "/api/things": {
            "get": {"responses": {"200": {"description": "ok"}}},
            "post": THING_POST,
        },
        "/api/broken": {
            "post": BROKEN_POST,
        },
    },
    "components": {"schemas": {"ThingIn": {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }}},
}

TS_OK = "export const BASE = '/api/things';\nexport const get = () => request(`${BASE}`);\n"
TS_BAD = ("export const get = () => request('/api/things');\n"
          "export const bad = () => request('/api/nope', {method: 'POST', body: {}});\n")


def _run(tmp: Path, spec: dict, ts_files: dict[str, str], extra: list[str]) -> subprocess.CompletedProcess:
    (tmp / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    api = tmp / "web" / "src" / "api"
    api.mkdir(parents=True, exist_ok=True)
    for name, content in ts_files.items():
        (api / name).write_text(content, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp),
         "--spec-file", str(tmp / "spec.json"), *extra],
        capture_output=True, text=True, timeout=120,
    )


def test_gate_red_on_bodiless_schema_and_unknown_path(tmp_path) -> None:
    proc = _run(tmp_path, SPEC, {"a.ts": TS_BAD}, ["--check"])
    assert proc.returncode == 1, proc.stdout
    assert "::error::" in proc.stdout
    assert "/api/broken" in proc.stdout
    assert "/api/nope" in proc.stdout


def test_gate_green_on_clean_tree(tmp_path) -> None:
    spec = {"paths": {k: v for k, v in SPEC["paths"].items() if k != "/api/broken"},
            "components": SPEC["components"]}
    proc = _run(tmp_path, spec, {"a.ts": TS_OK}, ["--write"])
    assert proc.returncode == 0, proc.stdout
    proc = _run(tmp_path, spec, {"a.ts": TS_OK}, ["--check"])
    assert proc.returncode == 0, proc.stdout


def test_gate_passes_on_real_tree() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout
