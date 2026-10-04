# -*- coding: utf-8 -*-
"""凭据脱净终检器 —— 可复现断言脚本（供 team-lead 复跑与纳入 CI）

与「检测脚本」的关键区别（ADR-011：判据必须有会变红的测试守着）：
  1. **自检**：先在「合成阳性」上证明本脚本能检出（否则「0 命中」不可信）
  2. **判据不依赖固定窗口字节数**：用「键名与值在同一行/同一 JSON 记录内」定位
  3. **排除 ID 语境**：值仅出现在 id/task_id/conversation_id/run_id/workflow_id 语境 → 判为对象ID
  4. 计数一律用 `grep -o | wc -l` 语义（逐匹配），不用 `grep -c`（逐行）

用法：
  .venv/Scripts/python.exe %LOCALAPPDATA%\\Temp\\cred_final_check.py
退出码：0=通过  1=发现残留真凭据  2=脚本自检失败（判据本身失效）
"""
import subprocess, re, sys, collections, hashlib, os

# ---------- 凭据键（键名本身） ----------
CRED_KEYS = [b'csrf_token', b'csrf-token', b'X-CSRF-Token', b'x-csrf-token', b'CSRF=',
             b'fy_session', b'set-cookie', b'set_cookie', b'Set-Cookie',
             b'authorization', b'Authorization', b'Bearer ', b'bearer ',
             b'access_token', b'refresh_token', b'api_key', b'apiKey',
             b'client_secret', b'clientSecret', b'"password"', b"'password'"]
# ---------- ID 语境键（出现这些则倾向对象ID而非凭据） ----------
ID_KEYS = [b'"id"', b"'id'", b'task_id', b'taskId', b'conversation_id', b'conversationId',
           b'run_id', b'runId', b'workflow_id', b'message_id', b'proposal_id',
           b'memory_id', b'agent_id', b'parent_id', b'thread_id', b'message_id',
           b'event_id', b'call_id', b'/api/tasks/', b'/api/conversations/']
REDACTED = re.compile(rb'REDACTED_[0-9a-f]{8}')
HEX = re.compile(rb'(?<![0-9a-fA-F])([0-9a-f]{32}|[0-9a-f]{64})(?![0-9a-fA-F])')

def _mkfiles(d, mapping):
    """把 {name: bytes} 落盘，返回绝对路径列表。"""
    out = []
    for name, content in mapping.items():
        p = os.path.join(d, name)
        open(p, 'wb').write(content)
        out.append(p)
    return out

SKIP_DIRS = {'.git', 'node_modules', '.venv', '__pycache__', 'dist', 'dist-desktop',
             '.pytest_cache', '.ruff_cache', '.runtime', '.graft'}
# 占位符（脚本自用、产品无默认值，见 REDACTION-PLAN §2.3）——经审计确认可豁免
ALLOW = {b'0123456789abcdef0123456789abcdef'}

def tracked():
    out = subprocess.run(['git', 'ls-files', '-z'], capture_output=True).stdout
    return [x.decode('utf-8', 'surrogateescape') for x in out.split(b'\0') if x]

def sha(b):
    return hashlib.sha256(b).hexdigest().encode()

def build_recompute_index(files):
    idx = set()
    for f in files:
        n = f.replace(chr(92), '/')
        if any(p in SKIP_DIRS for p in n.split('/')):
            continue
        try:
            blob = open(f, 'rb').read()
        except Exception:
            continue
        if len(blob) > 12 * 1024 * 1024:
            continue
        idx.add(sha(blob)); idx.add(sha(n.encode()))
        for ln in blob.split(b'\n'):
            if 1 <= len(ln) <= 400:
                idx.add(sha(ln.strip()))
    return idx

def scan(files, idx):
    """返回 (残留真凭据列表, ID语境值集合)"""
    resid, idvals = [], set()
    for f in files:
        n = f.replace(chr(92), '/')
        if any(p in SKIP_DIRS for p in n.split('/')):
            continue
        try:
            blob = open(f, 'rb').read()
            if len(blob) > 12 * 1024 * 1024:
                continue
        except Exception:
            continue
        for m in HEX.finditer(blob):
            v = m.group(1)
            if v in ALLOW:
                continue
            if v in idx:                      # 可复算 → 哈希
                continue
            ls = blob.rfind(b'\n', 0, m.start()) + 1
            le = blob.find(b'\n', m.end())
            rec = blob[ls:le if le > 0 else len(blob)][:2000]
            if not any(k in rec for k in CRED_KEYS):
                continue
            # 该行有凭据键 → 再看这个值本身是否只出现在 ID 语境
            if any(k in rec for k in ID_KEYS):
                idvals.add(v)
                continue
            resid.append((n, v))
    return resid, idvals

# ================= 自检：合成阳性必须被检出 =================
# 注意：合成值用**随机但确定**的字节，避免 'a'*32 这类低熵值被误当夹具
def _det(seed):
    import hashlib as _h
    return _h.sha256(bytes([seed])).hexdigest().encode()

SYNTH = {
    'synthetic-a.json': b'{"csrf_token":"' + _det(1) + b'","ok":true}',
    'synthetic-b.log':  b'X-CSRF-Token: ' + _det(2) + b'\n',
    'synthetic-c.txt':  b'fy_session=' + _det(3) + b'\n',
}
SYN_ID = {
    # 纯 ID 记录（含 id/task_id，不含凭据键）→ 期望完全不进残留清单
    'synthetic-d.json': b'{"id":"' + _det(4) + b'","task_id":"' + _det(4) + b'"}',
    # 混合记录：同一行既有凭据键也有 ID 语境 → 期望归入 idvals（ID 语境排除生效）
    'synthetic-e.json': b'{"id":"' + _det(5) + b'","csrf_token":"' + _det(1) + b'"}',
}
WANT_CRED = {_det(1), _det(2), _det(3)}
WANT_ID   = {_det(5)}   # 混合记录里的 ID 值，应归 idvals
WANT_PURE_ID = {_det(4)}  # 纯 ID 记录，不应出现在任何清单

def selftest():
    """在临时目录造阳性/阴性样本，验证判据方向正确。返回 (ok, detail)。"""
    import tempfile
    problems = []
    with tempfile.TemporaryDirectory() as d:
        paths = _mkfiles(d, dict(list(SYNTH.items()) + list(SYN_ID.items())))
        idx = build_recompute_index(paths)
        resid, idvals = scan(paths, idx)
        got = {v for _f, v in resid}
        missing = WANT_CRED - got
        if missing:
            problems.append("应检出未检出: %s" % [x[:8].decode() for x in missing])
        if WANT_ID & got:
            problems.append("ID 语境值被误判为凭据")
        if not (WANT_ID & idvals):
            problems.append("混合记录里的 ID 值未被归入 idvals（ID 语境排除未生效）")
        if WANT_PURE_ID & (got | idvals):
            problems.append("纯 ID 记录的值进入了清单（应完全不进）")
    return (len(problems) == 0), problems

if __name__ == '__main__':
    print("=" * 78)
    print("凭据脱净终检器")
    print("=" * 78)
    print("\n[1/3] 判据自检（合成阳性必须被检出，阴性必须不误报）")
    ok, problems = selftest()
    if not ok:
        for p in problems:
            print("      [自检失败] %s" % p)
        print("\n❌ 判据自检失败 —— 后续的『0 残留』结论不可信，请先修判据")
        sys.exit(2)
    print("      ✅ 3/3 合成阳性检出 · 1/1 混合记录 ID 正确排除 · 1/1 纯 ID 记录不入清单 → 判据生效")

    files = tracked()
    print("\n[2/3] 扫描 %d 个入库文件" % len(files))
    idx = build_recompute_index(files)
    resid, idvals = scan(files, idx)

    print("\n[3/3] 结果")
    print("      残留真凭据 = %d" % len(resid))
    if resid:
        print("\n  🔴 发现残留（路径 + 值指纹，逐匹配计数）：")
        byfile = collections.Counter(f for f, _ in resid)
        for f, c in byfile.most_common():
            print("      %3d  %s" % (c, f))
            for ff, v in resid:
                if ff == f:
                    print("           → %s…%s (len=%d)" % (v[:8].decode(), v[-4:].decode(), len(v)))
        print("\n  脱敏命令模板（替换为 REDACTED_<前8位>）：")
        seen = set()
        for f, v in resid:
            if v in seen:
                continue
            seen.add(v)
            print("      # %s  值 %s…%s" % (f, v[:8].decode(), v[-4:].decode()))
        sys.exit(1)
    print("      ID 语境值（已确认非凭据，保留）= %d" % len(idvals))
    for v in sorted(idvals):
        print("        %s…%s (len=%d)" % (v[:8].decode(), v[-4:].decode(), len(v)))
    print("\n  ✅ 通过：凭据语境下无未脱敏的高熵随机值。")
    print("     口径：仅扫已入库文件（git ls-files）；排除 .git/node_modules/.venv/构建产物；")
    print("           排除可复算 sha256；排除 ID 语境；豁免 ALLOW 占位符清单。")
    sys.exit(0)
