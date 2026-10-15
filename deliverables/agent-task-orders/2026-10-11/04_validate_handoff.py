"""校验外部 Agent 回传包的结构/引用/覆盖；不证明内容正确或运行功能通过。"""
from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
from pathlib import Path

REQUIRED = ["baseline.json", "inventory.csv", "coverage.csv", "sources.jsonl",
            "requirements.jsonl", "versions.jsonl", "conflicts.jsonl", "capability-map.jsonl",
            "competitors.jsonl", "enterprise-scenarios.jsonl", "HANDOFF.md", "QA.md"]
REQUIRED += ["self-iteration-audit.jsonl", "自迭代基座审查.md"]
PRODUCTS = {"Claude Code", "LangGraph", "Dify", "Coze", "FastGPT"}


def validate(root: Path) -> dict:
    errors = []
    counts = {}
    def fail(msg):
        errors.append(msg)
    for name in REQUIRED:
        if not (root / name).is_file():
            fail(f"missing file: {name}")
    def read_json(name):
        try:
            return json.loads((root / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            fail(f"{name}: {type(exc).__name__}")
            return {}
    def read_csv(name):
        try:
            with (root / name).open(encoding="utf-8-sig", newline="") as f:
                return list(csv.DictReader(f))
        except OSError:
            return []
    def read_lines(name, key):
        out = []
        try:
            for n, line in enumerate((root / name).read_text(encoding="utf-8-sig").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError()
                    out.append(value)
                except ValueError:
                    fail(f"{name}:{n}: invalid object")
        except OSError:
            return []
        ids = [r.get(key) for r in out]
        if any(not x for x in ids) or len(set(ids)) != len(ids):
            fail(f"{name}: missing/duplicate {key}")
        counts[name] = len(out)
        return out
    baseline = read_json("baseline.json")
    for key in ("captured_at", "scope_roots", "candidate_commit", "public_main_commit", "excluded_roots"):
        if key not in baseline:
            fail(f"baseline missing {key}")
    inventory = read_csv("inventory.csv")
    coverage = read_csv("coverage.csv")
    inv_ids = [r.get("inventory_id") for r in inventory]
    cov_ids = [r.get("inventory_id") for r in coverage]
    if not inventory or not coverage:
        fail("inventory/coverage must not be empty")
    if len(set(inv_ids)) != len(inv_ids) or len(set(cov_ids)) != len(cov_ids):
        fail("inventory/coverage duplicate IDs")
    if None in inv_ids or "" in inv_ids or set(inv_ids) != set(cov_ids):
        fail("inventory/coverage ID mismatch")
    for row in coverage:
        if row.get("status") not in {"scanned", "excluded", "blocked"}:
            fail(f"invalid coverage status: {row.get('inventory_id')}")
        if row.get("status") in {"excluded", "blocked"} and not row.get("reason"):
            fail(f"coverage reason missing: {row.get('inventory_id')}")
    sources = read_lines("sources.jsonl", "source_id")
    source_ids = {r.get("source_id") for r in sources}
    for row in sources:
        if not row.get("path") or not row.get("locator"):
            fail(f"source path/locator missing: {row.get('source_id')}")
        if row.get("inventory_id") not in set(inv_ids):
            fail(f"source inventory not found: {row.get('source_id')}")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("sha256", ""))):
            fail(f"source SHA256 invalid: {row.get('source_id')}")
    rows_by_file = {}
    for name, key in (("requirements.jsonl", "requirement_id"), ("versions.jsonl", "version_id"),
                      ("conflicts.jsonl", "conflict_id"), ("capability-map.jsonl", "map_id"),
                      ("competitors.jsonl", "competitor_id"), ("enterprise-scenarios.jsonl", "scenario_id"),
                      ("self-iteration-audit.jsonl", "iteration_id")):
        rows = read_lines(name, key)
        rows_by_file[name] = rows
        for row in rows:
            refs = row.get("source_ids", [])
            if not isinstance(refs, list) or any(ref not in source_ids for ref in refs):
                fail(f"{name}/{row.get(key)}: unknown source")
            if not refs and row.get("facts_status") != "hypothesis":
                fail(f"{name}/{row.get(key)}: no source evidence")
    req_ids = {r.get("requirement_id") for r in rows_by_file["requirements.jsonl"]}
    if not req_ids:
        fail("requirements must not be empty")
    version_ids = {r.get("version_id") for r in rows_by_file["versions.jsonl"]}
    for row in rows_by_file["requirements.jsonl"]:
        if any(v not in version_ids for v in row.get("version_ids", [])):
            fail(f"unknown version in {row.get('requirement_id')}")
        if row.get("duplicate_of") and row["duplicate_of"] not in req_ids:
            fail("duplicate_of target missing")
        if row.get("acceptance_origin") not in {"source_explicit", "missing"}:
            fail("acceptance origin invalid")
        if row.get("acceptance_origin") == "source_explicit" and not row.get("acceptance_text"):
            fail("explicit acceptance missing text")
    for row in rows_by_file["capability-map.jsonl"]:
        if row.get("requirement_id") not in req_ids:
            fail("map requirement missing")
        if row.get("status") == "verified":
            evidence = row.get("runtime_evidence") or {}
            for key in ("command", "exit_code", "environment", "captured_at", "output_path"):
                if key not in evidence:
                    fail(f"verified map missing {key}")
            if evidence.get("exit_code") != 0:
                fail("verified map nonzero exit code")
            if evidence.get("evidence_type") in {"mock", "stub", "simulation"}:
                fail("mock evidence cannot claim real verified status")
    products = {r.get("product") for r in rows_by_file["competitors.jsonl"]}
    if products != PRODUCTS:
        fail(f"product coverage mismatch: {sorted(PRODUCTS - products)}")
    for row in rows_by_file["competitors.jsonl"]:
        if not row.get("version") or not row.get("edition") or not row.get("official_url"):
            fail("competitor version/edition/url missing")
        if row.get("status") == "runtime_verified" and not row.get("runtime_evidence"):
            fail("competitor runtime receipt missing")
    return {"status": "passed" if not errors else "failed", "errors": errors, "counts": counts,
            "boundary": "structure-only; content/official-source validity and E2E require independent review"}


def self_test():
    # 不含用户数据；有效最小包须绿，删除来源后须红。
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "baseline.json").write_text(json.dumps({
            "captured_at": "test", "scope_roots": ["test"], "candidate_commit": "test",
            "public_main_commit": "test", "excluded_roots": []}), encoding="utf-8")
        (root / "inventory.csv").write_text("inventory_id,path\nINV-1,test\n", encoding="utf-8")
        (root / "coverage.csv").write_text("inventory_id,status,reason\nINV-1,scanned,\n", encoding="utf-8")
        data = {
            "sources.jsonl": [{"source_id": "SRC-1", "inventory_id": "INV-1", "path": "test",
                              "locator": "L1", "sha256": "a"*64}],
            "requirements.jsonl": [{"requirement_id": "R1", "source_ids": ["SRC-1"],
                                  "acceptance_origin": "missing", "version_ids": ["V1"]}],
            "versions.jsonl": [{"version_id": "V1", "source_ids": ["SRC-1"]}],
            "conflicts.jsonl": [], "capability-map.jsonl": [],
            "self-iteration-audit.jsonl": [{"iteration_id": "ITER-1", "source_ids": ["SRC-1"]}],
            "enterprise-scenarios.jsonl": [{"scenario_id": "E1", "facts_status": "hypothesis", "source_ids": []}],
            "competitors.jsonl": [{"competitor_id": p, "product": p, "source_ids": ["SRC-1"],
                                  "version": "unknown", "edition": "unknown", "official_url": "https://example.invalid"}
                                 for p in PRODUCTS]}
        for name, rows in data.items():
            (root / name).write_text("".join(json.dumps(r)+"\n" for r in rows), encoding="utf-8")
        for name in ("HANDOFF.md", "QA.md", "自迭代基座审查.md"):
            (root / name).write_text("synthetic test", encoding="utf-8")
        assert validate(root)["status"] == "passed"
        (root / "sources.jsonl").write_text("", encoding="utf-8")
        assert validate(root)["status"] == "failed"
    return {"self_test": "passed", "valid_fixture": "passed", "broken_source_fixture": "rejected"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_test:
        result = self_test()
    elif args.root:
        result = validate(Path(args.root))
    else:
        parser.error("provide final directory or --self-test")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(1 if result.get("status") == "failed" else 0)
