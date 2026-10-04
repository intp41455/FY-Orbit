import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { OutlinePanel, parseMarkdownOutline, searchInContent } from './OutlinePanel';
import type { EditorJumpApi } from './CodeEditor';

/**
 * P1-13 侧边定位 — 单元测试：
 *  - Markdown 标题解析（层级/行号/代码围栏跳过）；
 *  - 当前文件内搜索命中；
 *  - 点击大纲项 / 命中项会调用注入的 editorApi.jumpToLine。
 */

vi.mock('../../api/workbench', () => ({
  workbenchApi: {
    readFile: vi.fn(),
  },
}));

import { workbenchApi } from '../../api/workbench';

const SAMPLE = [
  '# 项目总览',
  '',
  '一段简介文本。',
  '```md',
  '## 这不是标题（代码围栏内）',
  '```',
  '## 快速开始',
  '',
  '执行 npm install。',
  '### 环境要求',
  'Node.js 20+。',
  '## 部署说明',
].join('\n');

describe('parseMarkdownOutline', () => {
  it('解析 #/##/### 层级与 1-based 行号', () => {
    const outline = parseMarkdownOutline(SAMPLE);
    expect(outline.map((h) => [h.level, h.text, h.line])).toEqual([
      [1, '项目总览', 1],
      [2, '快速开始', 7],
      [3, '环境要求', 10],
      [2, '部署说明', 12],
    ]);
  });

  it('跳过代码围栏内的 # 行', () => {
    expect(parseMarkdownOutline(SAMPLE).some((h) => h.text.includes('代码围栏'))).toBe(false);
  });

  it('空内容返回空数组', () => {
    expect(parseMarkdownOutline('')).toEqual([]);
  });
});

describe('searchInContent', () => {
  it('大小写不敏感地列出所有命中行与首列', () => {
    const hits = searchInContent('Alpha\nbeta ALPHA\nGamma', 'alpha');
    expect(hits).toEqual([
      { line: 1, col: 0, text: 'Alpha' },
      { line: 2, col: 5, text: 'beta ALPHA' },
    ]);
  });

  it('空关键词返回空数组', () => {
    expect(searchInContent(SAMPLE, '  ')).toEqual([]);
  });
});

describe('OutlinePanel 组件', () => {
  const WS = 'ws-p113';

  it('选择文件后渲染大纲与搜索命中，点击调用 jumpToLine', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue({
      workspace_id: WS,
      path: 'notes/guide.md',
      content: SAMPLE,
      encoding: 'utf-8',
      binary: false,
      editable: true,
      size_bytes: SAMPLE.length,
      sha256: null,
      revision: 1,
      read_only: false,
    });
    const ref = { current: null as EditorJumpApi | null };
    let jumped: { line: number; text?: string } | null = null;
    ref.current = {
      jumpToLine: (line, text) => {
        jumped = { line, text };
      },
    };

    const user = userEvent.setup();
    render(<OutlinePanel workspaceId={WS} path="notes/guide.md" editorApiRef={ref} />);

    // 大纲项出现（真实解析结果）
    const items = await screen.findAllByTestId('outline-item');
    expect(items).toHaveLength(4);

    // 点击 “### 环境要求”（第 10 行）→ jumpToLine(10, '环境要求')
    await user.click(screen.getByText('环境要求'));
    await waitFor(() => expect(jumped).toEqual({ line: 10, text: '环境要求' }));

    // 搜索 “npm” → 1 个命中行（第 9 行），点击后跳转并携带关键词
    await user.type(screen.getByTestId('outline-search'), 'npm');
    const hit = await screen.findByTestId('outline-hit');
    expect(hit).toHaveAttribute('data-line', '9');
    await user.click(hit);
    await waitFor(() => expect(jumped).toEqual({ line: 9, text: 'npm' }));
  });

  it('二进制文件显示不可解析提示且不渲染大纲', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue({
      workspace_id: WS,
      path: 'logo.bin',
      content: '',
      encoding: 'binary',
      binary: true,
      editable: false,
      size_bytes: 9,
      sha256: null,
      revision: 1,
      read_only: true,
    });
    const ref = { current: null as EditorJumpApi | null };
    render(<OutlinePanel workspaceId={WS} path="logo.bin" editorApiRef={ref} />);
    await screen.findByText('二进制文件无法生成大纲。');
    expect(screen.queryByTestId('outline-list')).toBeNull();
  });

  it('未选择文件时展示占位文案', () => {
    const ref = { current: null as EditorJumpApi | null };
    render(<OutlinePanel workspaceId={WS} path={null} editorApiRef={ref} />);
    expect(screen.getByText('选择文件后可在此查看标题大纲并搜索跳转。')).toBeInTheDocument();
  });
});
