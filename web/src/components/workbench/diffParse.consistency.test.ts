// P1-11 硬验收一致性测试：组件解析结果 vs 子进程 `git diff` 的 hunks 集合一致。
//
// 本环境的系统策略禁止 node 进程 spawn 任意子进程（spawnSync 恒 EBUSY，
// 与沙箱无关，实测 node/cmd/whoami/python 全部被拦），因此 `git diff` 原文
// 由 shell 侧真实子进程预先执行并归档到 evidence/p1-11-diff-20261003/fixtures/：
//   two-commit.diff  `git diff --no-color <sha1> <sha2> --` 原文
//   name-only.txt    `git diff --name-only <sha1> <sha2> --`
//   numstat.txt      `git diff --numstat <sha1> <sha2> --`
// 本测试用组件同款 parseUnifiedDiff 解析真实 git 原文，并做四重对照：
//   1. 文件清单 === `git diff --name-only` 输出
//   2. hunk 头集合 === 原文中所有 @@ 行
//   3. 解析出的行（kind 前缀 + 内容）逐行等于原文 hunk 体
//   4. 增删行数 === `git diff --numstat` 输出
// fixtures 同时归档在 evidence/p1-11-diff-20261003/fixtures/（验收证据）。
// 注：Vite fs.deny 拒绝 web 根外文件导入，故测试从 web/e2e/fixtures/ 读取。
import { describe, it, expect } from 'vitest';
import { parseUnifiedDiff, type DiffLine } from './diffParse';
import raw from '../../../e2e/fixtures/p1-11/two-commit.diff?raw';
import nameOnlyRaw from '../../../e2e/fixtures/p1-11/name-only.txt?raw';
import numstatRaw from '../../../e2e/fixtures/p1-11/numstat.txt?raw';

function kindChar(l: DiffLine): string {
  return l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : l.kind === 'context' ? ' ' : '\\';
}

/** 从 git 原文独立提取：hunk 头行 + hunk 体行（kind 前缀 + 内容） */
function extractFromRaw(text: string): { hunkHeaders: string[]; bodyLines: string[] } {
  const hunkHeaders: string[] = [];
  const bodyLines: string[] = [];
  let inHunk = false;
  for (const line of text.split('\n')) {
    if (line.startsWith('@@')) {
      hunkHeaders.push(line);
      inHunk = true;
      continue;
    }
    if (!inHunk) continue;
    const ch = line.charAt(0);
    if (ch === '+' || ch === '-' || ch === ' ' || ch === '\\') {
      bodyLines.push(ch + line.slice(1));
    } else if (line.startsWith('diff --git ')) {
      inHunk = false;
    }
  }
  return { hunkHeaders, bodyLines };
}

describe('P1-11 一致性：parseUnifiedDiff vs 子进程 git diff 原文', () => {
  it('文件清单与 git diff --name-only 输出一致', () => {
    const parsed = parseUnifiedDiff(raw);
    const expected = nameOnlyRaw.split('\n').filter(Boolean);
    expect(parsed.files.map((f) => f.newPath)).toEqual(expected);
    expect(parsed.files.length).toBeGreaterThanOrEqual(3);
  });

  it('hunk 头集合与 git 原文逐条一致', () => {
    const parsed = parseUnifiedDiff(raw);
    const parsedHeaders = parsed.files.flatMap((f) => f.hunks.map((h) => h.header));
    expect(parsedHeaders).toEqual(extractFromRaw(raw).hunkHeaders);
    expect(parsedHeaders.length).toBeGreaterThan(0);
  });

  it('解析行（kind+内容）与 git 原文 hunk 体逐行对应', () => {
    const parsed = parseUnifiedDiff(raw);
    const parsedLines = parsed.files
      .flatMap((f) => f.hunks)
      .flatMap((h) => h.lines)
      .map((l) => kindChar(l) + l.text);
    expect(parsedLines).toEqual(extractFromRaw(raw).bodyLines);
  });

  it('行号与 hunk 头声明的 old/new 区间吻合', () => {
    const parsed = parseUnifiedDiff(raw);
    for (const f of parsed.files) {
      for (const h of f.hunks) {
        const olds = h.lines.filter((l) => l.oldLine !== null).map((l) => l.oldLine as number);
        const news = h.lines.filter((l) => l.newLine !== null).map((l) => l.newLine as number);
        if (h.oldLines > 0) expect(olds[0]).toBe(h.oldStart);
        if (h.newLines > 0) expect(news[0]).toBe(h.newStart);
        expect(olds).toEqual(olds.map((_, i) => h.oldStart + i));
        expect(news).toEqual(news.map((_, i) => h.newStart + i));
      }
    }
  });

  it('增删行数与 git diff --numstat 输出一致', () => {
    const parsed = parseUnifiedDiff(raw);
    const numstat = new Map<string, [number, number]>();
    for (const line of numstatRaw.split('\n')) {
      if (!line.trim()) continue;
      const [a, d, p] = line.split('\t');
      numstat.set(p, [Number(a), Number(d)]);
    }
    for (const f of parsed.files) {
      const [a, d] = numstat.get(f.newPath) ?? [0, 0];
      expect(f.additions).toBe(a);
      expect(f.deletions).toBe(d);
    }
  });
});
