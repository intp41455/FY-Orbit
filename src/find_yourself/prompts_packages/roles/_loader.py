"""P8 · A-角色模板-01/02 角色模板库加载器。

目录即库：``prompts_packages/roles/*.md``，frontmatter 声明身份与能力绑定：

```yaml
---
name: coder
role: 编码工程师
description: 写代码与修缺陷
tools:            # 能力层绑定（A-角色模板-02）：声明式清单
  - fs.read
  - fs.write
  - terminal.run
variables_schema:
  task: {type: str, required: true}
---
（正文 = 系统提示词，可用 {{var}}）
```

* **发现机制零冲突**：`skills/discovery.py` 只扫 prompts_packages 顶层
  （目录跳过），本子目录由本加载器自治扫描——两条管线互不打扰；
* **能力绑定是声明式清单**（A-角色模板-02）：绑定名是语义名（fs.read /
  terminal.run / browser.open 三核心工具族），**实名映射由装配层**
  （tool_registry 对账）完成——本模块提供 `validate_tools()` 钩子，
  不在加载期假装校验过存在性（诚实边界）；
* 结构不合规的模板显式报错列出问题，绝不静默跳过（坏模板上线比没模板危险）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLES_DIR = Path(__file__).resolve().parent

_REQUIRED_FIELDS = ("name", "role", "description", "tools")
#: 本地三核心工具族（A-角色模板-02）——声明式语义名
CORE_TOOL_FAMILIES = ("fs.read", "fs.write", "terminal.run", "browser.open")


@dataclass(frozen=True)
class RoleTemplate:
    name: str
    role: str
    description: str
    tools: tuple[str, ...]
    variables_schema: dict[str, Any]
    body: str
    source_file: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        raise ValueError("missing frontmatter block (--- ... ---)")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unterminated frontmatter block")
    header, body = parts[1], parts[2].lstrip("\n")
    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in header.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith(("  - ", "- ")) and current_list_key:
            meta[current_list_key].append(line.strip().lstrip("- ").strip())
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            meta[key] = []
            current_list_key = key
        else:
            current_list_key = None
            try:
                meta[key] = int(value)
            except ValueError:
                meta[key] = value
    return meta, body


def _validate(meta: dict[str, Any], body: str, source: str) -> None:
    problems = [f for f in _REQUIRED_FIELDS if not str(meta.get(f, "")).strip()]
    if problems:
        raise ValueError(f"{source}: missing required fields {problems}")
    if not body.strip():
        raise ValueError(f"{source}: empty prompt body")
    tools = meta.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError(f"{source}: tools must be a non-empty list "
                         "(能力绑定清单——A-角色模板-02)")


def load_roles(directory: Path | None = None) -> list[RoleTemplate]:
    """扫描 roles/*.md → 校验 → RoleTemplate 列表（按 name 排序）。"""
    roles_dir = directory or ROLES_DIR
    out: list[RoleTemplate] = []
    for md in sorted(roles_dir.glob("*.md")):
        meta, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
        _validate(meta, body, md.name)
        out.append(RoleTemplate(
            name=str(meta["name"]), role=str(meta["role"]),
            description=str(meta["description"]),
            tools=tuple(str(t) for t in meta.get("tools", [])),
            variables_schema=meta.get("variables_schema") or {},
            body=body, source_file=md.name,
        ))
    return out


def get_role(name: str, directory: Path | None = None) -> RoleTemplate:
    for r in load_roles(directory):
        if r.name == name:
            return r
    raise KeyError(f"role template {name!r} not found; "
                   f"known: {[r.name for r in load_roles(directory)]}")


def validate_tools(known_tool_names: set[str], directory: Path | None = None) -> list[str]:
    """与 tool_registry 实名对账（装配层调用）：返回未解析的绑定清单（空=全对上）。

    支持工具族前缀：绑定名 `fs.read` 对上实名 `fs.read_file` 之类由装配层
    决定；此处按**精确名或族前缀**匹配。
    """
    unresolved: list[str] = []
    for r in load_roles(directory):
        for t in r.tools:
            if t in known_tool_names:
                continue
            if any(n.startswith(t + ".") or n.startswith(t) for n in known_tool_names):
                continue
            unresolved.append(f"{r.name}:{t}")
    return unresolved
