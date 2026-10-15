"""GA-01 契约一致性：openapi 请求体锁死 + 前端调用路径存在性。

三条规则（任一违反即 exit 1，::error:: 点名）：
1. 每个 POST/PUT/PATCH 若带 requestBody，其 schema 必须有 properties 或 $ref
  （空 object / 无结构 = 契约未声明，前端无法逐字段比对）。
2. `contracts/openapi-request-bodies.json` 必须与当前 openapi 逐字段一致
   （`--write` 重生成；`--check` 比对，漂移即红 —— 这就是"生成的校验用例"）。
3. `web/src/api/*.ts` 里的 `/api/...`、`/auth/...` 字面量必须能在 openapi
   paths 里找到对应模板（`${x}` 视为单段通配；纯拼接式动态路径不在扫描面，
   见注释）。

用法：python scripts/ga01_contract.py [--write|--check] [--root DIR]
默认 --check。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

API_LITERAL_RE = re.compile(r"""['"`](/(?:api|auth)/[A-Za-z0-9_${}/.+\-,()?]*)['"`]""")
_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.M | re.S)


def _code_only(text: str) -> str:
    """去注释：注释里的示例路径（如文档里的 `/api/kb/documents`）不是调用，
    不得触发门禁。字符串字面量不受影响（注释剥离只删 // 与 /* */ 区段）。"""
    return _COMMENT_RE.sub("", text)


def load_openapi(root: Path) -> dict:
    sys.path.insert(0, str(root / "src"))
    from find_yourself.api.app import create_app
    return create_app().openapi()


def body_fields(spec: dict) -> dict[str, dict]:
    """method + path -> {required: [...], properties: [...]}（无 body 记 {}）。"""
    out: dict[str, dict] = {}
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method.upper() not in ("POST", "PUT", "PATCH"):
                continue
            key = f"{method.upper()} {path}"
            body = (op.get("requestBody") or {}).get("content", {})
            schema = (body.get("application/json") or {}).get("schema", {})
            resolved = _resolve(spec, schema)
            out[key] = {
                "required": sorted(resolved.get("required", [])),
                "properties": sorted((resolved.get("properties") or {}).keys()),
                "has_body": bool(body),
            }
    return out


def _resolve(spec: dict, schema: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and seen < 8:
        if "$ref" in schema:
            ref = schema["$ref"]
            if not ref.startswith("#/components/schemas/"):
                return {}
            schema = spec.get("components", {}).get("schemas", {}).get(
                ref.split("/")[-1], {})
        elif isinstance(schema.get("anyOf"), list) or isinstance(schema.get("oneOf"), list):
            # Optional[X] 渲染为 anyOf：取第一个非 null 分支继续解。
            branches = [b for b in (schema.get("anyOf") or schema.get("oneOf"))
                        if isinstance(b, dict) and b.get("type") != "null"]
            if not branches:
                return {}
            schema = branches[0]
        elif schema.get("type") == "array":
            # 顶层数组体（如批量决策 items: list[RouteBody]）：以内层 $ref 为契约。
            items = schema.get("items", {})
            if isinstance(items, dict) and "$ref" in items:
                schema = items
                seen += 1
                continue
            return {"properties": {"items": items}, "required": []} \
                if items else {}
        else:
            break
        seen += 1
    return schema if isinstance(schema, dict) else {}


def check_rule1(fields: dict[str, dict]) -> list[str]:
    bad = []
    for key, info in sorted(fields.items()):
        if info["has_body"] and not info["properties"]:
            bad.append(f"{key}：requestBody 无 properties/$ref（契约未声明字段）")
    return bad


def check_rule3(root: Path, spec: dict, api_dir: Path | None = None) -> list[str]:
    templates = [p for p in spec.get("paths", {})]
    bad = []
    api_dir = api_dir or root / "web" / "src" / "api"
    if not api_dir.is_dir():
        return ["web/src/api/ 目录缺失，拒绝假绿"]
    for ts in sorted(api_dir.glob("*.ts")):
        if ts.name.endswith(".test.ts"):
            continue
        text = _code_only(ts.read_text(encoding="utf-8"))
        # 先把本文件的 const BASE = '/api/x' 代入 ${BASE}，否则组合调用扫不到、
        # 光秃 BASE 定义反而被误报。
        bases = dict(re.findall(r"""const\s+(\w+)\s*=\s*['"](/api/[^'""]*)['"]""", text))
        for var, val in bases.items():
            text = text.replace("${" + var + "}", val)
        lits = set()
        for lit in API_LITERAL_RE.findall(text):
            # 去掉查询串部分（`${qs}` 常展开为 "?a=1&b=2"，不是路径段）。
            lit = re.sub(r"\?\$\{[^}]*\}$", "", lit)
            lit = lit.split("?")[0]
            lits.add(lit.rstrip("/") or "/")
        # 去掉纯 BASE 定义（被更长的调用字面量包含的前缀，不是调用）。
        calls = {l for l in lits
                 if not any(o != l and o.startswith(l + "/") for o in lits)}
        for lit in sorted(calls):
            # 尾部 ${x} 两义：查询串（去尾即命中）或可选路径段（两种都试）。
            variants = [lit]
            m = re.match(r"^(.*)\$\{[^}]*\}$", lit)
            if m:
                variants.append(m.group(1).rstrip("/") or "/")
            normed = [re.sub(r"\$\{[^}]*\}", "{p}", v) for v in variants]
            if not any(_match(n, templates) is not None for n in normed):
                bad.append(f"{ts.name}：{lit} 在 openapi paths 无对应路由")
    return bad


def _match(lit: str, templates: list[str]) -> str | None:
    for t in templates:
        t_norm = t.rstrip("/")
        lp, tp = lit.strip("/").split("/"), t_norm.strip("/").split("/")
        if len(lp) != len(tp):
            continue
        ok = all(
            seg.startswith("{") or seg == tseg
            for seg, tseg in zip(lp, tp)
        )
        if ok:
            return t
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    # 自测用：用现成的 openapi JSON 代替起真实 app；用指定目录代替 web/src/api。
    parser.add_argument("--spec-file", default=None)
    parser.add_argument("--api-dir", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.spec_file:
        spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    else:
        spec = load_openapi(root)
    fields = body_fields(spec)

    problems = check_rule1(fields)
    problems += check_rule3(
        root, spec,
        Path(args.api_dir) if args.api_dir else None)
    if problems:
        for p in problems:
            print(f"::error::GA-01 {p}")
        return 1

    artifact = root / "contracts" / "openapi-request-bodies.json"
    blob = json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(blob, encoding="utf-8")
        print(f"GA-01 校验用例已写入 {artifact}（{len(fields)} 条）")
        return 0
    if not artifact.is_file():
        print("::error::GA-01 contracts/openapi-request-bodies.json 缺失，先跑 --write")
        return 1
    if artifact.read_text(encoding="utf-8") != blob:
        print("::error::GA-01 契约漂移：openapi 请求体与 contracts/openapi-request-bodies.json 不一致，跑 --write 同步")
        return 1
    print(f"GA-01 通过：{len(fields)} 条变异路由契约一致，前端调用路径全命中。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
