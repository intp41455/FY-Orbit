"""只读复核第二轮机器数据；不执行外部Agent脚本、不覆盖其证据。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BASE = Path("C:/Users/intpj/agent-projects/find-yourself/outputs/baseline-reconciliation-20261011")
RUNS = {
    "T1": ("coordinator-t1-rev2-20261012-040453", "HANDOFF-r2.md"),
    "T2": ("coordinator-t2-r2-20261012-0405", "HANDOFF.md"),
    "T3": ("coordinator-t3-rev2-20261012-0400", "HANDOFF.md"),
    "T5": ("coordinator-t5-r2-20261012-0402", "HANDOFF.md"),
    "T6": ("coordinator-t6-r2-20261012-040142", "HANDOFF.md"),
}


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def main():
    report = {"scope": "R2 receipt structure and bounded source checks only",
              "product_e2e_verified": False, "T4_round2": "not_located", "runs": {}}
    for team, (run, handoff) in RUNS.items():
        root = BASE / run
        final = root / "final"
        result = {"run": run, "handoff_exists": (final / handoff).is_file(),
                  "files": [], "parse_errors": []}
        for path in sorted(final.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            item = {"path": str(path.relative_to(root)), "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}
            try:
                if path.suffix == ".jsonl":
                    item["rows"] = len(rows(path))
                elif path.suffix == ".json":
                    json.loads(data.decode("utf-8-sig"))
            except (ValueError, UnicodeError) as exc:
                result["parse_errors"].append({"path": item["path"], "error": str(exc)})
            result["files"].append(item)
        report["runs"][team] = result

    # 实测样例内容归一化；只覆盖清单提供的sample_paths，不冒充全部副本验证。
    checked = []
    for record in rows(BASE / RUNS["T1"][0] / "final/spec-family-diff.jsonl"):
        for raw_path in record["sample_paths"]:
            path = Path(raw_path)
            if not path.is_file():
                checked.append({"file": record["file"], "path": raw_path, "status": "missing"})
                continue
            content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            checked.append({"file": record["file"], "path": raw_path,
                            "status": "match" if digest.startswith(record["normalized_sha"]) else "mismatch"})
    report["T1_normalization_sample"] = checked

    sources = rows(BASE / RUNS["T2"][0] / "final/sources-r2.jsonl")
    refs = []
    for source in sources:
        raw_path = source.get("snapshot_path") or source.get("path")
        if not raw_path:
            refs.append({"source_id": source["source_id"], "status": "missing_path"})
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = BASE / RUNS["T2"][0] / path
        expected = source.get("sha256")
        status = "missing"
        if path.is_file():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            status = "match" if expected == actual else "mismatch"
        refs.append({"source_id": source["source_id"], "status": status})
    report["T2_source_sha"] = refs
    output = Path(sys.argv[1])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"runs": {k: {"handoff": v["handoff_exists"], "files": len(v["files"]),
                                 "parse_errors": len(v["parse_errors"])} for k, v in report["runs"].items()},
                      "normalization": {s: sum(x["status"] == s for x in checked)
                                        for s in {x["status"] for x in checked}},
                      "T2_source_sha": {s: sum(x["status"] == s for x in refs)
                                        for s in {x["status"] for x in refs}},
                      "T4": "not_located"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
