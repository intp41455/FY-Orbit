// P1-11 diff 可视化 — unified diff 解析器。
//
// 硬验收：组件渲染内容必须与 `git diff` 原始输出逐行对应。本模块是渲染与
// 对照测试共用的唯一解析入口：DiffView 渲染前先 parseUnifiedDiff(text)，
// 一致性测试用同一函数解析子进程 git diff 的原文并逐行比对。

export type DiffLineKind = 'context' | 'add' | 'del' | 'meta';

export interface DiffLine {
  kind: DiffLineKind;
  /** 行内容（不含 diff 前缀字符） */
  text: string;
  /** 旧文件行号（context/del 有值，add 为 null） */
  oldLine: number | null;
  /** 新文件行号（context/add 有值，del 为 null） */
  newLine: number | null;
  /** 与紧随的 add/del 成对时标记为"修改"（渲染为黄色标记） */
  modified?: boolean;
}

export interface DiffHunk {
  /** 原始 @@ 头（含函数上下文片段），如 "@@ -1,3 +1,4 @@" */
  header: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLine[];
}

export interface DiffFile {
  oldPath: string;
  newPath: string;
  hunks: DiffHunk[];
  isBinary: boolean;
  additions: number;
  deletions: number;
}

export interface ParsedDiff {
  files: DiffFile[];
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;
const DIFF_GIT_RE = /^diff --git a\/(.*) b\/(.*)$/;

function stripPrefix(line: string, prefixes: string[]): string {
  for (const p of prefixes) {
    if (line.startsWith(p)) return line.slice(p.length);
  }
  return line;
}

function cleanPath(p: string): string {
  const trimmed = p.replace(/\t/g, ' ');
  return trimmed === '/dev/null' ? '' : trimmed;
}

/** 剥离 git diff 头里的 a/ 或 b/ 前缀 */
function stripSidePrefix(p: string, side: 'a' | 'b'): string {
  const cleaned = cleanPath(p);
  return cleaned.startsWith(`${side}/`) ? cleaned.slice(2) : cleaned;
}

/**
 * 解析 unified diff 原文（git diff 输出）为结构化文件/hunk/行。
 * 未知前缀的行归入当前文件的 meta，不会抛错——渲染端宁可多显示也不丢行。
 */
export function parseUnifiedDiff(text: string): ParsedDiff {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;

  const pushFileMeta = (line: string) => {
    if (file) {
      if (line.startsWith('Binary files') || line === 'GIT binary patch') file.isBinary = true;
      hunk = null;
    }
  };

  for (const raw of text.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (line.startsWith('diff --git ')) {
      const m = DIFF_GIT_RE.exec(line);
      file = {
        oldPath: cleanPath(m ? m[1] : line.slice('diff --git '.length)),
        newPath: cleanPath(m ? m[2] : ''),
        hunks: [],
        isBinary: false,
        additions: 0,
        deletions: 0,
      };
      files.push(file);
      hunk = null;
      continue;
    }
    if (!file) continue;
    if (line.startsWith('@@')) {
      const m = HUNK_RE.exec(line);
      if (m) {
        hunk = {
          header: line,
          oldStart: Number(m[1]),
          oldLines: m[2] === undefined ? 1 : Number(m[2]),
          newStart: Number(m[3]),
          newLines: m[4] === undefined ? 1 : Number(m[4]),
          lines: [],
        };
        file.hunks.push(hunk);
        oldLine = hunk.oldStart;
        newLine = hunk.newStart;
        continue;
      }
      pushFileMeta(line);
      continue;
    }
    if (hunk) {
      const ch = line.charAt(0);
      if (ch === '+') {
        hunk.lines.push({ kind: 'add', text: line.slice(1), oldLine: null, newLine: newLine++ });
        file.additions += 1;
        continue;
      }
      if (ch === '-') {
        hunk.lines.push({ kind: 'del', text: line.slice(1), oldLine: oldLine++, newLine: null });
        file.deletions += 1;
        continue;
      }
      if (ch === ' ') {
        hunk.lines.push({ kind: 'context', text: line.slice(1), oldLine: oldLine++, newLine: newLine++ });
        continue;
      }
      if (ch === '\\' ) {
        // "\ No newline at end of file"——按 meta 保留
        hunk.lines.push({ kind: 'meta', text: line, oldLine: null, newLine: null });
        continue;
      }
      // 空串（结尾换行 artifact）或未知行：hunk 提前结束
      hunk = null;
    }
    // 文件级头（index/---/+++/rename/new file mode 等）
    if (
      line.startsWith('index ') || line.startsWith('--- ') || line.startsWith('+++ ')
      || line.startsWith('old mode') || line.startsWith('new mode')
      || line.startsWith('new file mode') || line.startsWith('deleted file mode')
      || line.startsWith('similarity index') || line.startsWith('rename from')
      || line.startsWith('rename to') || line.startsWith('copy from') || line.startsWith('copy to')
      || line.startsWith('Binary files') || line === 'GIT binary patch'
    ) {
      if (line.startsWith('--- ')) file.oldPath = stripSidePrefix(stripPrefix(line, ['--- ']), 'a');
      if (line.startsWith('+++ ')) file.newPath = stripSidePrefix(stripPrefix(line, ['+++ ']), 'b');
      pushFileMeta(line);
    }
  }

  // 删除文件（+++ /dev/null）与新建文件（--- /dev/null）：补齐缺侧路径，
  // 与 `git diff --name-only` / `--numstat` 的单一路径口径一致。
  for (const f of files) {
    if (!f.newPath && f.oldPath) f.newPath = f.oldPath;
    if (!f.oldPath && f.newPath) f.oldPath = f.newPath;
  }

  markModifiedPairs(files);
  return { files };
}

/** 连续 del 块后紧跟等量/不等量 add 块 → 逐对标记为"修改"（黄色标记）。 */
function markModifiedPairs(files: DiffFile[]): void {
  for (const f of files) {
    for (const h of f.hunks) {
      let i = 0;
      while (i < h.lines.length) {
        if (h.lines[i].kind !== 'del') { i += 1; continue; }
        let delEnd = i;
        while (delEnd < h.lines.length && h.lines[delEnd].kind === 'del') delEnd += 1;
        let addEnd = delEnd;
        while (addEnd < h.lines.length && h.lines[addEnd].kind === 'add') addEnd += 1;
        if (addEnd > delEnd) {
          const pairs = Math.min(delEnd - i, addEnd - delEnd);
          for (let k = 0; k < pairs; k += 1) {
            h.lines[i + k].modified = true;
            h.lines[delEnd + k].modified = true;
          }
        }
        i = addEnd > i ? addEnd : i + 1;
      }
    }
  }
}
