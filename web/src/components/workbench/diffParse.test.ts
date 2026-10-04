import { describe, it, expect } from 'vitest';
import { parseUnifiedDiff } from './diffParse';

const SAMPLE = [
  'diff --git a/notes.txt b/notes.txt',
  'index 1111111..2222222 100644',
  '--- a/notes.txt',
  '+++ b/notes.txt',
  '@@ -1,3 +1,4 @@',
  ' alpha',
  '-bravo',
  '+bravo-changed',
  '+delta',
  ' charlie',
  'diff --git a/docs/guide.md b/docs/guide.md',
  'index 3333333..4444444 100644',
  '--- a/docs/guide.md',
  '+++ b/docs/guide.md',
  '@@ -1 +1,2 @@',
  '-旧标题',
  '+新标题',
  '+第二行',
].join('\n');

describe('parseUnifiedDiff', () => {
  it('按文件分组并统计增删行数', () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    expect(parsed.files).toHaveLength(2);
    expect(parsed.files[0].oldPath).toBe('notes.txt');
    expect(parsed.files[0].newPath).toBe('notes.txt');
    expect(parsed.files[0].additions).toBe(2);
    expect(parsed.files[0].deletions).toBe(1);
    expect(parsed.files[1].newPath).toBe('docs/guide.md');
    expect(parsed.files[1].additions).toBe(2);
    expect(parsed.files[1].deletions).toBe(1);
  });

  it('解析 hunk 头与行号', () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    const h = parsed.files[0].hunks[0];
    expect(h.header).toBe('@@ -1,3 +1,4 @@');
    expect(h.oldStart).toBe(1);
    expect(h.newStart).toBe(1);
    expect(h.lines[0]).toMatchObject({ kind: 'context', text: 'alpha', oldLine: 1, newLine: 1 });
    expect(h.lines[1]).toMatchObject({ kind: 'del', text: 'bravo', oldLine: 2 });
    expect(h.lines[2]).toMatchObject({ kind: 'add', text: 'bravo-changed', newLine: 2 });
    expect(h.lines[4]).toMatchObject({ kind: 'context', text: 'charlie', oldLine: 3, newLine: 4 });
  });

  it('成对的紧邻 del/add 标记为修改（modified）', () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    const lines = parsed.files[0].hunks[0].lines;
    // -bravo 与 +bravo-changed 成对 → modified；+delta 多出的新增不标
    expect(lines[1].modified).toBe(true);
    expect(lines[2].modified).toBe(true);
    expect(lines[3].modified).toBeUndefined();
  });

  it('纯新增文件：oldPath 为空，全部为 add', () => {
    const parsed = parseUnifiedDiff(
      [
        'diff --git a/new.log b/new.log',
        'new file mode 100644',
        'index 0000000..5555555',
        '--- /dev/null',
        '+++ b/new.log',
        '@@ -0,0 +1,2 @@',
        '+第一行',
        '+第二行',
      ].join('\n'),
    );
    expect(parsed.files[0].oldPath).toBe('new.log'); // --- /dev/null → 补齐为同路径
    expect(parsed.files[0].additions).toBe(2);
    expect(parsed.files[0].hunks[0].lines.every((l) => l.kind === 'add')).toBe(true);
  });

  it('二进制文件标记 isBinary 且无 hunk', () => {
    const parsed = parseUnifiedDiff(
      'diff --git a/logo.bin b/logo.bin\nindex 0000000..1111111 100644\nBinary files a/logo.bin and b/logo.bin differ\n',
    );
    expect(parsed.files[0].isBinary).toBe(true);
    expect(parsed.files[0].hunks).toHaveLength(0);
  });

  it('空输入返回空文件列表', () => {
    expect(parseUnifiedDiff('').files).toHaveLength(0);
  });
});
