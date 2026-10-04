"""P4 · 从 DSL IR 确定性生成**可运行工程脚手架**（需求 13，依 ADR-08）。

与 ``services/export.py``（用户数据导出，脱敏+临时令牌）**完全无关**——那是
数据出口，本模块是代码出口；两者不得混用。

三条硬不变量（对应交付门禁）：

1. **确定性（R-04）**：同一份 DSL 规范形导出两次，返回的 ``{path: content}``
   映射**逐字节一致**。实现上：全排序、无时间戳、无随机数、JSON ``sort_keys``
   + 固定缩进；``dsl_digest`` 取 canonical 文档的 sha256（与
   ``avatar_gen`` 的「sha256 派生确定性」同一思路——**不需要** PRNG，因为
   本导出根本不引入任何随机源）。
2. **零凭据**：导出物里不允许出现任何凭据/连接串特征串。生成器在出口处
   主动扫描，命中即抛 ``ValidationFailed``（fail closed，绝不半成品）。
3. **语法可解析**：所有 ``.py`` 产物必须能被 ``ast.parse`` 解析（门禁判据③）。

资产清单（``assets.manifest.json``）是与**美术侧的唯一契约面**，schema 见
``docs/ASSET_MANIFEST_CONTRACT.md``（v1.0 冻结）；派生规则按契约 §2：
每个 ``output`` 节点一个 sprite + 全局一个 palette，按 ``asset_id`` 字典序。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .dsl_canvas import canonical_dsl, compile_dsl, validate_dsl
from .errors import ValidationFailed

MANIFEST_VERSION = "1"
GENERATOR_NAME = "find-yourself-project-scaffold"
GENERATOR_VERSION = "1.0.0"

#: 凭据/连接串特征串（大小写不敏感）。命中任何一个都拒绝导出。
CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "password", "passwd", "secret", "api_key", "apikey", "access_token",
    "refresh_token", "private_key", "BEGIN RSA", "BEGIN OPENSSH",
    "connectionstring", "postgres://", "mysql://", "mongodb://",
    "redis://", "amqp://", "bearer ",
)

#: 清单里允许的资产种类（契约 §2 封闭枚举）。
ASSET_KINDS = ("sprite", "tileset", "palette", "audio", "font")


def _assert_no_credentials(text: str, where: str) -> None:
    lowered = text.lower()
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.lower() in lowered:
            raise ValidationFailed(
                "scaffold_credential_detected",
                f"导出中止：{where} 含疑似凭据/连接串特征串 {pattern!r}；"
                "工程脚手架绝不携带任何凭据",
            )


def _dsl_digest(canonical: dict[str, Any]) -> str:
    basis = json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(basis).hexdigest()[:12]


def build_asset_manifest(canonical: dict[str, Any]) -> dict[str, Any]:
    """按契约 v1.0 派生资产清单（确定性；hash 恒 null——平台不伪造产出）。"""
    assets: list[dict[str, Any]] = [
        {
            "asset_id": "palette.project",
            "kind": "palette",
            "path": "assets/palette/project.png",
            "pixel_size": [16, 16],
            "anchor": [0, 0],
            "frames": 1,
            "hash": None,
        }
    ]
    for node in canonical["nodes"]:
        if node["type"] == "output":
            assets.append({
                "asset_id": f"preview.{node['id']}",
                "kind": "sprite",
                "path": f"assets/sprites/preview.{node['id']}.png",
                "pixel_size": [32, 32],
                "anchor": [0, 0],
                "frames": 1,
                "hash": None,
            })
    assets.sort(key=lambda a: a["asset_id"])
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "source": {"dsl_digest": _dsl_digest(canonical)},
        "assets": assets,
    }
    # 生成侧自检：asset_id 唯一、kind 封闭、hash 全空（契约 §5 判据 2/3）。
    ids = [a["asset_id"] for a in assets]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("scaffold_manifest_invalid", "asset_id 重复")
    for asset in assets:
        if asset["kind"] not in ASSET_KINDS:
            raise ValidationFailed("scaffold_manifest_invalid",
                                   f"未知资产种类 {asset['kind']!r}")
        if asset["hash"] is not None:
            raise ValidationFailed("scaffold_manifest_invalid",
                                   "生成态 hash 必须为 null（不得伪造产出）")
    return manifest


_RUNNER_TEMPLATE = '''# -*- coding: utf-8 -*-
"""由 Find Yourself 脚手架生成（确定性导出，无时间戳/无随机源）。

本 runner 只实现受限动词集的纯 Python 解释：``agent`` / ``confirm`` 两个
动词在平台外**不受支持**，执行到时显式报错退出——绝不假装执行成功。
"""

import json
import sys
from pathlib import Path

_UNSUPPORTED_VERBS = ("agent", "confirm")


def _fail(message: str) -> "NoReturn":
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(2)


def _get(value, field, default=None):
    if not isinstance(value, dict):
        return default
    return value.get(field, default)


def _template(text, row):
    out = str(text)
    if isinstance(row, dict):
        for key, val in row.items():
            out = out.replace("{" + key + "}", str(val))
    return out


def _compare(left, op, right):
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "gt":
        return left > right
    if op == "lt":
        return left < right
    if op == "contains":
        return right in left
    _fail(f"未知比较算子 {op!r}")


def _transform(value, verb, params):
    if verb == "template":
        template = _get(params, "template", "")
        if isinstance(value, list):
            return [_template(template, row) for row in value]
        return _template(template, value)
    if verb == "map":
        op = _get(params, "op", "set")
        field = _get(params, "field", "")
        rows = value if isinstance(value, list) else [value]
        out = []
        for row in rows:
            if op == "set":
                row = dict(row) if isinstance(row, dict) else {}
                row[field] = _template(_get(params, "value", ""), row)
            elif op == "upper":
                row = {k: v.upper() if isinstance(v, str) else v for k, v in row.items()} \\
                    if isinstance(row, dict) else str(row).upper()
            elif op == "lower":
                row = {k: v.lower() if isinstance(v, str) else v for k, v in row.items()} \\
                    if isinstance(row, dict) else str(row).lower()
            else:
                _fail(f"未知 map 算子 {op!r}")
            out.append(row)
        return out
    if verb == "filter":
        field, op, target = _get(params, "field", ""), _get(params, "op", "eq"), _get(params, "value")
        rows = value if isinstance(value, list) else [value]
        return [row for row in rows if _compare(_get(row, field), op, target)]
    if verb == "aggregate":
        op = _get(params, "op", "count")
        field = _get(params, "field", "")
        rows = value if isinstance(value, list) else [value]
        if op == "count":
            return len(rows)
        values = [_get(row, field) for row in rows]
        values = [v for v in values if v is not None]
        if op == "sum":
            return sum(values)
        if op in ("min", "max"):
            return (min if op == "min" else max)(values) if values else None
        if op == "avg":
            return (sum(values) / len(values)) if values else None
        if op == "first":
            return values[0] if values else None
        if op == "last":
            return values[-1] if values else None
        if op == "join":
            return _get(params, "sep", ",").join(str(v) for v in values)
        if op == "unique":
            return sorted(set(values))
        _fail(f"未知聚合算子 {op!r}")
    if verb == "branch":
        field, op, target = _get(params, "field", ""), _get(params, "op", "eq"), _get(params, "value")
        return _compare(_get(value, field), op, target)
    if verb in ("merge",):
        return value
    if verb in _UNSUPPORTED_VERBS:
        _fail(f"动词 {verb!r} 需要平台运行时（HITL / Agent 总线），工程外不受支持")
    _fail(f"未知动词 {verb!r}")


def _order(nodes, edges):
    """Kahn 拓扑排序；成环显式报错（与平台 compile_dsl 同语义）。"""
    indeg = {n["id"]: 0 for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    for e in edges:
        adj[e["from"]].append(e["to"])
        indeg[e["to"]] += 1
    ready = [nid for nid, d in indeg.items() if d == 0]
    out = []
    while ready:
        nid = ready.pop(0)
        out.append(nid)
        for nxt in adj[nid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    if len(out) != len(nodes):
        _fail("DSL 图中存在环，无法拓扑排序")
    return out


def main() -> int:
    doc = json.loads(Path(__file__).with_name("flow.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in doc["nodes"]}
    edges = doc["edges"]
    values = {}
    for nid in _order(list(nodes.values()), edges):
        node = nodes[nid]
        if node["type"] == "input":
            params = node.get("params") or {}
            if params.get("kind") == "text_lines":
                values[nid] = str(params.get("value", "")).splitlines()
            else:
                values[nid] = params.get("value")
            continue
        incoming = [e for e in edges if e["to"] == nid]
        if node["type"] == "transform":
            if not incoming:
                _fail(f"transform 节点 {nid} 没有输入边")
            src = values.get(incoming[0]["from"])
            cond = incoming[0].get("condition")
            if cond and not _compare(_get(src, cond.get("field")), cond.get("op"), cond.get("value")):
                values[nid] = None
                continue
            values[nid] = _transform(src, node["verb"], node.get("params") or {})
            continue
        if node["type"] == "output":
            fmt = _get(node.get("params") or {}, "format", "text")
            merged = [values.get(e["from"]) for e in incoming]
            merged = [v for v in merged if v is not None]
            payload = merged[0] if len(merged) == 1 else merged
            if fmt == "json":
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            else:
                if isinstance(payload, list):
                    for row in payload:
                        print(row if isinstance(row, str) else json.dumps(row, ensure_ascii=False))
                else:
                    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def export_project_scaffold(doc: dict[str, Any], *,
                            project_name: str = "fy-dsl-project") -> dict[str, str]:
    """把一份 DSL 文档导出为确定性工程脚手架（``{相对路径: 文本内容}``）。

    非法 DSL 一律抛 :class:`ValidationFailed`——**绝不生成半成品工程**。
    返回值是纯映射（不写盘、无时间戳、无随机源），两次调用逐字节一致。
    """
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValidationFailed("scaffold_project_name", "project_name 必须是非空字符串")
    validate_dsl(doc)
    compile_dsl(doc)  # 环检测：导出的工程必须是能跑的那张图
    canonical = canonical_dsl(doc)

    flow_json = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                           indent=2) + "\n"
    manifest_json = json.dumps(build_asset_manifest(canonical), ensure_ascii=False,
                               sort_keys=True, indent=2) + "\n"
    runner = _RUNNER_TEMPLATE
    readme = _render_readme(canonical, project_name)

    files = {
        "src/flow.json": flow_json,
        "src/run.py": runner,
        "assets.manifest.json": manifest_json,
        "README.md": readme,
    }
    # 出口统一扫描：任何文件命中凭据特征串都拒绝导出（铁律：零凭据）。
    for rel, content in files.items():
        _assert_no_credentials(content, rel)
    return files


def _render_readme(canonical: dict[str, Any], project_name: str) -> str:
    verbs = sorted({n.get("verb") for n in canonical["nodes"] if n.get("verb")})
    unsupported = [v for v in verbs if v in ("agent", "confirm")]
    lines = [
        f"# {project_name}",
        "",
        "由 Find Yourself 平台确定性导出的可运行工程脚手架。",
        "",
        "## 运行",
        "",
        "```",
        "python src/run.py",
        "```",
        "",
        "## 结构",
        "",
        "| 路径 | 说明 |",
        "|---|---|",
        "| `src/flow.json` | 规范形 DSL 文档（runner 的唯一输入） |",
        "| `src/run.py` | 受限动词集的纯 Python 解释器（无平台依赖） |",
        "| `assets.manifest.json` | 资产清单（与美术侧的唯一契约面，schema 见 ASSET_MANIFEST_CONTRACT v1.0） |",
        "",
        f"本工程用到受限动词集：{', '.join(verbs) if verbs else '（无 transform 节点）'}。",
    ]
    if unsupported:
        lines.append("")
        lines.append(
            f"⚠ 动词 {', '.join(unsupported)} 需要平台运行时（HITL / Agent 总线），"
            "在本工程外执行到该节点会**显式报错退出**——不会假装执行成功。"
        )
    lines.append("")
    lines.append("诚实边界：`assets.manifest.json` 里所有 `hash` 均为 `null`——"
                 "资产由美术侧产出后回填；平台绝不以占位图冒充已产出。")
    return "\n".join(lines) + "\n"


__all__ = [
    "ASSET_KINDS",
    "CREDENTIAL_PATTERNS",
    "GENERATOR_NAME",
    "GENERATOR_VERSION",
    "MANIFEST_VERSION",
    "build_asset_manifest",
    "export_project_scaffold",
]


def validate_manifest_v1(manifest: dict[str, Any]) -> None:
    """清单**消费侧**守卫（契约 §4：读到不认识的主版本必须显式报错）。

    平台生成清单后也会用它做一次自检。未来 v2 的清单被 v1 读取方拿到时，
    在这里显式失败——绝不「尽力解析」造成半兼容的假象。
    """
    if not isinstance(manifest, dict):
        raise ValidationFailed("manifest_invalid", "清单必须是 JSON 对象")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ValidationFailed(
            "manifest_version_unsupported",
            f"清单版本 {manifest.get('manifest_version')!r} 不受支持；"
            f"本读取方只认 {MANIFEST_VERSION!r}（契约 §4：未知主版本显式报错）",
        )
    for key in ("manifest_version", "generator", "source", "assets"):
        if key not in manifest:
            raise ValidationFailed("manifest_invalid", f"清单缺少必需字段 {key!r}")
    if not isinstance(manifest.get("assets"), list):
        raise ValidationFailed("manifest_invalid", "assets 必须是数组")
