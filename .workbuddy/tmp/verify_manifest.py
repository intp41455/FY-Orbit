"""校验收官报告第七节交付物清单的行数/字节数。
逐行解析 markdown 表格，剥离 ** 粗体后与磁盘实测值比对。
输出一张对照表，任何 MISMATCH 都要人工裁决。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs/handoff-tasks/14-主控综合收官报告-2026-10-04.md"

text = REPORT.read_text(encoding="utf-8")
lines = text.splitlines()

# 定位第七节清单表格的数据行
rows = []
for ln in lines:
    m = re.match(r"^\|\s*\**(\d+)\**\s*\|\s*`([^`]+)`\s*\|\s*\**([\d,]+)\**\s*\|\s*\**([\d,]+)\**\s*\|", ln)
    if m:
        rows.append((int(m.group(1)), m.group(2), int(m.group(3).replace(",", "")), int(m.group(4).replace(",", ""))))

print(f"解析到 {len(rows)} 条清单行")
bad = []
for idx, path, rl, rb in rows:
    p = ROOT / path
    if not p.is_file():
        print(f"[{idx:>2}] MISSING  {path}")
        bad.append(idx)
        continue
    data = p.read_bytes()
    al = data.count(b"\n")
    ab = len(data)
    flag = "OK" if (al == rl and ab == rb) else "MISMATCH"
    if flag != "OK":
        bad.append(idx)
    print(f"[{idx:>2}] {flag:<9}行 报告={rl:<6} 实测={al:<6} | 字节 报告={rb:<8} 实测={ab:<8} | {path}")

print()
print(f"合计 {len(rows)} 项，不一致 {len(bad)} 项：{bad if bad else '无'}")
sys.exit(1 if bad else 0)
