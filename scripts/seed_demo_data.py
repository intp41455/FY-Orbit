"""写入引导示例数据，让首次使用者不用从空白开始。

只调用真实 HTTP 接口（与前端走完全相同的路径），不直接碰数据库，
因此「能跑通 seed」本身就是「前端主链路可用」的证明。

流程：登录 → 建对象 → 导入资料切片 → 跑画像推演

用法（先 start.ps1 起服务）：
    .venv\\Scripts\\python.exe scripts\\seed_demo_data.py
    .venv\\Scripts\\python.exe scripts\\seed_demo_data.py --base http://127.0.0.1:8000 --reset
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_TOKEN = "dev-token-secret"

# 12 段刻意设计的资料切片，覆盖不同语境与情绪，
# 用于验证「确定性规则能否从真实素材归纳出画像」。
SLICES: list[dict] = [
    {"fn": "01-工作日志.txt", "text": "这周把拖了两周的对账模块终于收尾了。中间有两天特别焦虑，总觉得做得不够好，晚上反复想白天的细节，睡不太着。项目顺利上线后反而没有想象中开心，可能是过程里绷得太紧了。"},
    {"fn": "02-健身记录.txt", "text": "坚持晨跑第37 天，今天 5 公里 28 分。跑步时脑子最清醒，很多想不通的事在跑完第二公里后会突然有答案。决定继续，不加量，维持节奏比冲刺重要。"},
    {"fn": "03-朋友对话.txt", "text": "朋友说我最近状态比之前好。我说是吧，其实也没做什么特别的事。他说可能是终于把注意力从别人身上收回来了。我记住了这句话。"},
    {"fn": "04-深夜随想.txt", "text": "凌晨一点还醒着。想到三年前也是这样睡不着那时候是因为焦虑，现在是因为兴奋。区别在于现在我知道自己在期待什么。这大概是这一年半最大的变化。"},
    {"fn": "05-项目复盘.txt", "text": "复盘这次上线：前期需求改了三轮，是我自己的问题，下次要在开工前把验收标准写清楚。技术上没有意外，都是提前测过的。团队配合比上次好很多。"},
    {"fn": "06-家务清单.txt", "text": "周末把阳台收拾了，扔掉三箱旧东西。物理空间清爽之后脑子也清爽。发现自己一直舍不得扔东西，其实留着占地方的是我。"},
    {"fn": "07-家人电话.txt", "text": "和妈妈通了四十分钟，她说家里都好。挂掉之后有点想哭。上次这么长时间聊天是过年。意识到自己一直在用忙当借口，愧疚感是攒出来的不是突然的。"},
    {"fn": "08-学习笔记.txt", "text": "重新开始学英语，每天 20 分钟。第11 天，断了两次又接回来。关键不是不断，是断了之后能接回来。这个方法应该用在很多事上。"},
    {"fn": "09-职业困惑.txt", "text": "有人问我为什么不换工作。其实我一直在等一个标准答案：什么算做得好。但今天意识到标准是我自己定的。定标准这件事，没人能替我完成。"},
    {"fn": "10-情绪记录.txt", "text": "这周情绪起伏比较大，周二低落、周四平稳、周末轻微焦虑。发现低落都跟睡眠不足有关，焦虑都跟预期不明有关。有规律了就不那么怕。"},
    {"fn": "11-旅行记录.txt", "text": "去了趟青岛。在海边坐了一下午什么也没干。发现自己平时太难停下来，停下来就心虚。这次学着什么都不做，感觉不错。"},
    {"fn": "12-年度总结.txt", "text": "今年最大的转变是从「证明给别人看」转向「按自己的标准活」。代价是慢了一些，也孤独了一些。但我不后悔。明年想把这个标准再写清楚一点。"},
]

SUBJECT = {
    "label": "我（示例对象）",
    "kind": "self",
    "description": "引导示例：一位持续记录自我观察的职场人。12 段资料切片已导入。",
}


class Client:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""
        self.token = token

    def call(self, path: str, payload=None, method=None, need_csrf=False):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"content-type": "application/json", "origin": self.base}
        if need_csrf and self.csrf:
            headers["x-csrf-token"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.op.open(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"_raw": raw[:300]}
        except Exception as e:  # noqa: BLE001
            return -1, {"_error": f"{type(e).__name__}: {e}"}

    def login(self) -> bool:
        st, out = self.call("/auth/local/dev-token", {"token": self.token})
        if st != 200:
            return False
        self.csrf = out.get("csrf_token", "")
        return bool(self.csrf)


def main() -> int:
    ap = argparse.ArgumentParser(description="写入引导示例数据（走真实 HTTP 接口）")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--reset", action="store_true", help="先删除已存在的示例对象再重建")
    args = ap.parse_args()

    c = Client(args.base, args.token)
    print("=" * 62)
    print("引导数据写入 —— Find Yourself")
    print(f"目标: {c.base}")
    print("=" * 62)

    # ------------------------------------------------------------ 0. 可达性
    st, _ = c.call("/health/live")
    if st != 200:
        print(f"\n[1/4] 服务未就绪 (health/live -> {st})")
        print("      请先运行:.\\start.ps1")
        return 1
    print(f"\n[1/4] 服务可达 health/live -> {st}")

    # ---------------------------------------------------------------- 1. 登录
    if not c.login():
        print("      [2/4] 登录失败：本地口令不匹配")
        print(f"            尝试的token: {c.token}")
        print("            启动时用的口令在 .env 的 FY_LOCAL_TOKEN，或 start.ps1 的 -LocalToken")
        return 1
    print("[2/4] 登录成功 csrf 已下发")

    # ------------------------------------------------------------ 2. 建对象
    if args.reset:
        st, lst = c.call("/api/profiles/subjects")
        if st == 200 and isinstance(lst, list):
            for s in lst:
                if s.get("label") == SUBJECT["label"]:
                    d, _ = c.call(f"/api/profiles/subjects/{s['id']}", method="DELETE",
                                  need_csrf=True)
                    print(f"      已删除旧示例对象 {s['id']} -> {d}")
    st, subj = c.call("/api/profiles/subjects", SUBJECT, need_csrf=True)
    if st not in (200, 201):
        print(f"      [3/4] 建对象失败 -> {st} {subj}")
        return 1
    sid = subj.get("id")
    print(f"[3/4] 对象已建 id={sid}  label={subj.get('label')}")

    # ------------------------------------------------------- 3. 导入资料切片
    ok = 0
    fail: list[tuple[str, object]] = []
    for i, s in enumerate(SLICES, 1):
        st, r = c.call("/api/profiles/imports", {
            "content": s["text"],
            "filename": s["fn"],
            "subject_id": sid,
            "privacy_domain": "personal",
        }, need_csrf=True)
        if st in (200, 201):
            ok += 1
            print(f"      [{i:2d}/{len(SLICES)}] {s['fn']:<18} -> {st} import_id={r.get('id')}")
        else:
            fail.append((s["fn"], r))
            print(f"      [{i:2d}/{len(SLICES)}] {s['fn']:<18} -> {st} 失败")
    print(f"      导入成功 {ok}/{len(SLICES)}")

    # --------------------------------------------------------- 4. 跑画像推演
    st, run = c.call(f"/api/profiles/{sid}/runs", {"rule_version": "v1.0"}, need_csrf=True)
    if st in (200, 201):
        print(f"[4/4] 画像推演成功 -> {st}")
        rev = run.get("revision_id") or run.get("id")
        print(f"      revision: {rev}")
        if isinstance(run.get("summary"), str) and run["summary"]:
            print(f"      summary: {run['summary'][:200]}")
    else:
        print(f"[4/4] 画像推演未成功 -> {st}")
        print(f"      响应: {json.dumps(run, ensure_ascii=False)[:300]}")
        print("      可能原因：导入切片尚未确认说话人，需在界面上完成「确认主体」后重跑。")

    # ------------------------------------------------------------- 汇总
    print("\n" + "=" * 62)
    if fail:
        print(f"部分失败：{len(fail)} 段资料未导入")
        for fn, r in fail[:3]:
            print(f"  - {fn}: {str(r)[:160]}")
    print("完成。打开浏览器即可在「画像」页看到该对象与推演结果。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
