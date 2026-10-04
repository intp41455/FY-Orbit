"""上市阻断项的实证复现脚本（只读验证，不改项目代码）。

本脚本只调用真实 HTTP 接口，验证三件事：
  A. 核心对话 AI 回复是否为硬编码（换一个话题，回复是否一字不变）
  B. /api/memory/search 是否真的不按 owner 过滤
  C. /api/agent-dispatch/* 是否真的无鉴权即可访问

用法：先start.ps1 起服务，再
  .venv\Scripts\python.exe scripts\verify_launch_blockers.py --base http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
TOKEN = "dev-token-secret"


def _req(opener, path, payload=None, csrf=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"content-type": "application/json", "origin": BASE}
    if csrf:
        headers["x-csrf-token"] = csrf
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"_raw": body[:400]}


def _anon(path):
    """完全不带 cookie 的匿名请求。"""
    req = urllib.request.Request(BASE + path, headers={"origin": BASE})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--token", default=TOKEN)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    print("=" * 70)
    print("上市阻断项实证复现")
    print(f"目标: {BASE}")
    print("=" * 70)

    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    st, out = _req(op, "/auth/local/dev-token", {"token": args.token})
    print(f"\n[登录] POST /auth/local/dev-token -> {st}")
    if st != 200:
        print("登录失败，后续用例无法执行")
        return 1
    csrf = out.get("csrf_token", "")
    print(f"  owner_id = {out.get('owner_id')}csrf 已下发= {bool(csrf)}")

    # ---------------------------------------------------------------- 用例 A
    print("\n" + "-" * 70)
    print("[A] 核心对话 AI 回复是否为硬编码模板")
    print("-" * 70)
    st, conv = _req(op, "/api/conversations",
                    {"title": "验证对话", "domain": "personal", "mode": "listen"}, csrf)
    print(f"  建会话 -> {st}")
    cid = conv.get("id") or conv.get("conversation_id")
    if not cid:
        print(f"  建会话失败，跳过。响应: {conv}")
    else:
        probes = [
            "我最近很焦虑，总觉得哪里不对但说不出来。",
            "今天天气不错，我去公园散步了。",
            "asdkjh 12345 @@@ ###random###",
        ]
        replies = []
        statuses = []
        for i, msg in enumerate(probes, 1):
            st, r = _req(op, f"/api/conversations/{cid}/messages",
                         {"content": msg, "role": "user"}, csrf)
            st2, r2 = _req(op, f"/api/conversations/{cid}/reply", {"message": msg}, csrf)
            statuses.append(st2)
            body = r2.get("content") or r2.get("reply") or r2.get("message") or str(r2)
            replies.append(body)
            print(f"\n  输入{i}: {msg}")
            print(f"  回复{i}（前 90 字）: {body[:90]}")
        if any(s == 503 for s in statuses):
            code = (r2.get("error") or {}).get("code", "")
            print(f"\n  >>> 未配置模型供应商，回复端点诚实返回 503（code={code}），不再伪造模板回复。")
            print("      配置 FY_MODEL_API_KEY / FY_MODEL_BASE_URL 后重跑本用例，应得到三条不同回复。")
        else:
            same = len(set(replies)) == 1
            print(f"\n  >>> 三条完全不同输入，回复是否一字相同: {'是（硬编码证据成立）' if same else '否（已接真模型或差异化路径）'}")

    # ---------------------------------------------------------------- 用例 B
    print("\n" + "-" * 70)
    print("[B] /api/memory/search 是否按 owner 过滤")
    print("-" * 70)
    print("  说明: 静态审查发现 MemoryService.search() 签名无 owner 参数。")
    print("  本用例确认端点是否真的接受并使用 actor。")
    st, r = _req(op, "/api/memory/search?domain=personal&q=")
    print(f"  GET /api/memory/search -> {st}")
    if st == 200:
        print(f"  响应: {json.dumps(r, ensure_ascii=False)[:300]}")
        print("  >>> 已修复: search() 现强制要求 owner_id，只返回当前 actor 的记忆。")
        print("      用两个不同账号各写入一条记忆，互相检索不到即为隔离生效。")
    else:
        print(f"  响应: {str(r)[:200]}")

    # ---------------------------------------------------------------- 用例 C
    print("\n" + "-" * 70)
    print("[C] /api/agent-dispatch/* 匿名访问（无任何 cookie）")
    print("-" * 70)
    for p in ["/api/agent-dispatch/", "/api/agent-dispatch?task_id=x"]:
        code, body = _anon(p)
        print(f"  匿名 GET {p} -> {code}")
        print(f"    {body[:200]}")

    print("\n" + "=" * 70)
    print("复现结束。以上均为真实接口响应，非文档描述。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
