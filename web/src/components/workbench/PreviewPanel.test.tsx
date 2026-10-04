import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PreviewPanel, AUTO_REFRESH_INTERVAL_MS } from './PreviewPanel';

/**
 * P1-A 实时预览窗 — 预览面板（统一预览源协议前端）行为断言：
 *   1. iframe 必须带 sandbox="allow-scripts"（且仅此能力）；
 *   2. 源下拉切换会重新加载对应源内容；
 *   3. 自动刷新开关：开启时轮询 /version、版本变化才重新取内容；关闭即停；
 *   4. 登记入口：.html/.md 可一键登记；其他类型只显示提示；
 *   5. 诚实原则：内容加载失败必须显示明确错误，绝不假装渲染成功。
 */

vi.mock('../../api/workbench', () => ({
  workbenchApi: {
    listPreviewSources: vi.fn(),
    registerStaticPreviewSource: vi.fn(),
    previewSourceVersion: vi.fn(),
    unregisterPreviewSource: vi.fn(),
    fetchPreviewSourceContent: vi.fn(),
  },
}));

import { workbenchApi, type PreviewSource } from '../../api/workbench';

const WS = 'ws-p1a';

function staticSource(over: Partial<PreviewSource> = {}): PreviewSource {
  return {
    id: 'psrc-a',
    workspace_id: WS,
    kind: 'static',
    path: 'index.html',
    media_type: 'text/html',
    version: 'ver-1',
    state: 'active',
    content_url: '/api/workbench/preview-sources/psrc-a/content',
    created_at: '2026-10-04T00:00:00Z',
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom 无 createObjectURL/revokeObjectURL —— 桩化并校验调用。
  let n = 0;
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(
    () => `blob:mock-${++n}`,
  );
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
  vi.mocked(workbenchApi.fetchPreviewSourceContent).mockResolvedValue({
    blob: new Blob(['<html><body>P1A</body></html>'], { type: 'text/html' }),
    version: 'ver-1',
  });
  vi.mocked(workbenchApi.previewSourceVersion).mockResolvedValue({
    id: 'psrc-a',
    kind: 'static',
    version: 'ver-1',
    state: 'active',
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('P1-A PreviewPanel：iframe 沙箱与源选择', () => {
  it('static 源渲染 iframe：sandbox 仅授予 allow-scripts，src 为 blob URL', async () => {
    vi.mocked(workbenchApi.listPreviewSources).mockResolvedValue({
      items: [staticSource()],
      count: 1,
    });

    render(<PreviewPanel workspaceId={WS} path={null} />);

    const frame = await screen.findByTestId('wb-psrc-frame');
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
    expect(frame.getAttribute('src')).toMatch(/^blob:mock-/);
    expect(workbenchApi.fetchPreviewSourceContent).toHaveBeenCalledWith('psrc-a');
  });

  it('切换源下拉会重新加载对应源（md 源显示未渲染标注）', async () => {
    const user = userEvent.setup();
    vi.mocked(workbenchApi.listPreviewSources).mockResolvedValue({
      items: [
        staticSource(),
        staticSource({
          id: 'psrc-b',
          path: 'notes.md',
          media_type: 'text/plain',
          content_url: '/api/workbench/preview-sources/psrc-b/content',
        }),
      ],
      count: 2,
    });

    render(<PreviewPanel workspaceId={WS} path={null} />);
    await screen.findByTestId('wb-psrc-frame');

    await user.selectOptions(screen.getByTestId('wb-psrc-select'), 'psrc-b');
    await waitFor(() => {
      expect(workbenchApi.fetchPreviewSourceContent).toHaveBeenCalledWith('psrc-b');
    });
    expect(screen.getByTestId('wb-psrc-md-note')).toHaveTextContent(/Markdown 暂未渲染/);
    // iframe 换新 blob URL（重载）
    const frame = screen.getByTestId('wb-psrc-frame');
    expect(frame.getAttribute('src')).toMatch(/^blob:mock-/);
  });
});

describe('P1-A PreviewPanel：自动刷新', () => {
  it('开启时轮询 version，版本变化才重新取内容；关闭后停止轮询', async () => {
    vi.useFakeTimers();
    vi.mocked(workbenchApi.listPreviewSources).mockResolvedValue({
      items: [staticSource()],
      count: 1,
    });

    render(<PreviewPanel workspaceId={WS} path={null} />);
    // fake timers 下 flush 初始 effect 与 promise 链（findBy* 依赖真实定时器，不可用）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('wb-psrc-frame')).toBeInTheDocument();
    expect(workbenchApi.fetchPreviewSourceContent).toHaveBeenCalledTimes(1);

    // 版本未变 → 不重取
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS + 100);
    });
    expect(workbenchApi.previewSourceVersion).toHaveBeenCalled();
    expect(workbenchApi.fetchPreviewSourceContent).toHaveBeenCalledTimes(1);

    // 版本变化 → 重取内容（iframe 换新 blob URL）
    vi.mocked(workbenchApi.previewSourceVersion).mockResolvedValue({
      id: 'psrc-a',
      kind: 'static',
      version: 'ver-2',
      state: 'active',
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS + 100);
    });
    expect(workbenchApi.fetchPreviewSourceContent).toHaveBeenCalledTimes(2);

    // 关闭自动刷新 → 不再轮询
    fireEvent.click(screen.getByTestId('wb-psrc-auto'));
    vi.mocked(workbenchApi.previewSourceVersion).mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_REFRESH_INTERVAL_MS * 3);
    });
    expect(workbenchApi.previewSourceVersion).not.toHaveBeenCalled();
  });
});

describe('P1-A PreviewPanel：登记预览源入口', () => {
  it('当前文件为 .html/.md 时显示一键登记按钮，登记后选中新源', async () => {
    const user = userEvent.setup();
    // 首次（挂载）为空列表；登记后的重拉返回新源。
    vi.mocked(workbenchApi.listPreviewSources)
      .mockResolvedValueOnce({ items: [], count: 0 })
      .mockResolvedValue({ items: [staticSource({ id: 'psrc-new', path: 'page.html' })], count: 1 });
    vi.mocked(workbenchApi.registerStaticPreviewSource).mockResolvedValue(
      staticSource({ id: 'psrc-new', path: 'page.html' }),
    );

    render(<PreviewPanel workspaceId={WS} path="page.html" />);

    const btn = await screen.findByTestId('wb-psrc-register');
    expect(btn).toBeEnabled();
    await user.click(btn);

    expect(workbenchApi.registerStaticPreviewSource).toHaveBeenCalledWith(WS, 'page.html');
    await waitFor(() => {
      expect(workbenchApi.fetchPreviewSourceContent).toHaveBeenCalledWith('psrc-new');
    });
    // 下拉中选中的是新登记的源
    const select = screen.getByTestId('wb-psrc-select') as HTMLSelectElement;
    expect(select.value).toBe('psrc-new');
  });

  it('非 html/md 文件不显示登记按钮，仅显示提示', async () => {
    vi.mocked(workbenchApi.listPreviewSources).mockResolvedValue({ items: [], count: 0 });
    render(<PreviewPanel workspaceId={WS} path="notes/a.txt" />);
    expect(await screen.findByTestId('wb-psrc-register-hint')).toBeInTheDocument();
    expect(screen.queryByTestId('wb-psrc-register')).not.toBeInTheDocument();
  });
});

describe('P1-A PreviewPanel：诚实错误状态', () => {
  it('内容加载失败显示明确错误，不渲染 iframe', async () => {
    vi.mocked(workbenchApi.listPreviewSources).mockResolvedValue({
      items: [staticSource()],
      count: 1,
    });
    vi.mocked(workbenchApi.fetchPreviewSourceContent).mockRejectedValue(
      new Error('预览内容加载失败（HTTP 404）'),
    );

    render(<PreviewPanel workspaceId={WS} path={null} />);

    const err = await screen.findByTestId('wb-psrc-error');
    expect(err).toHaveTextContent(/预览不可用/);
    expect(err).toHaveTextContent(/404/);
    expect(screen.queryByTestId('wb-psrc-frame')).not.toBeInTheDocument();
  });
});
