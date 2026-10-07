/**
 * P9 前端单测 · ReviewMode 落库接线（A-点哪评哪-05/06/07）。
 *
 * 关键断言：意见走**服务端**（不再只 localStorage）；服务端不可达时
 * 降级为本地草稿并**显式提示未同步**（不假装已保存）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api/review', () => ({
  openReviewSession: vi.fn(),
  fetchReviewSession: vi.fn(),
  addReviewNote: vi.fn(),
  listReviewNotes: vi.fn(),
  patchReviewNote: vi.fn(),
  deleteReviewNote: vi.fn(),
  markReviewRefreshed: vi.fn(),
  exportReviewNotes: vi.fn(),
  decideReviewRoute: vi.fn(),
}));

import {
  openReviewSession,
  listReviewNotes,
  addReviewNote,
  markReviewRefreshed,
} from '../api/review';
import { ReviewMode } from './ReviewMode';

const SESSION = { status: 'ok', session: { id: 's1', page: '/chat', route: 'whitebox' as const, iteration: 1 } };

function note(over: Record<string, unknown> = {}) {
  return {
    id: 'n1', session_id: 's1', page: '/chat', mode: 'dom',
    tag: 'button', element_id: '', element_class: '', text: '',
    selector: 'body > button', dom_path: ['body', 'button'],
    region: {}, strokes: [], audio_ref: '', audio_transcript: '',
    note: '', marker: '<button>', short_code: 'r1',
    dom_digest: 'a', prev_dom_digest: '', changed: false,
    state: 'open', applied_at: null, created_at: '2026-10-07T00:00:00+08:00',
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  document.body.innerHTML = '';
  vi.mocked(openReviewSession).mockResolvedValue(SESSION);
  vi.mocked(listReviewNotes).mockResolvedValue({ status: 'ok', notes: [], count: 0 });
  vi.mocked(markReviewRefreshed).mockResolvedValue({
    status: 'ok',
    session: { id: 's1', iteration: 2, refresh_state: 'applied', refresh_note: '' },
  });
});

describe('ReviewMode · 开关与面板', () => {
  it('初始只有悬浮按钮，点击后出现面板', async () => {
    render(<ReviewMode />);
    expect(screen.getByTestId('review-toggle')).toBeTruthy();
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => expect(screen.getByTestId('review-panel')).toBeTruthy());
  });

  it('打开时创建服务端会话', async () => {
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => expect(openReviewSession).toHaveBeenCalled());
    expect(vi.mocked(openReviewSession).mock.calls[0][0].page).toBe(window.location.pathname);
  });

  it('三种模式切换按钮存在', async () => {
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => screen.getByTestId('review-panel'));
    expect(screen.getByTestId('mode-select')).toBeTruthy();
    expect(screen.getByTestId('mode-region')).toBeTruthy();
    expect(screen.getByTestId('mode-annotate')).toBeTruthy();
  });

  it('切到圈选模式挂载圈选覆盖层', async () => {
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => screen.getByTestId('review-panel'));
    fireEvent.click(screen.getByTestId('mode-region'));
    expect(screen.getByTestId('region-select-overlay')).toBeTruthy();
  });

  it('切到批注模式挂载批注画布', async () => {
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => screen.getByTestId('review-panel'));
    fireEvent.click(screen.getByTestId('mode-annotate'));
    expect(screen.getByTestId('annotation-canvas')).toBeTruthy();
  });

  it('热刷新状态条在面板内可见（A-点哪评哪-05）', async () => {
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => screen.getByTestId('review-panel'));
    expect(screen.getByTestId('refresh-loop-bar')).toBeTruthy();
  });
});

describe('ReviewMode · 圈选落库（A-点哪评哪-06）', () => {
  it('圈选完成后调服务端 addReviewNote（mode=region）', async () => {
    vi.mocked(addReviewNote).mockResolvedValue({
      status: 'ok',
      note: note({ mode: 'region', region: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 } }) as never,
    });
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => screen.getByTestId('review-panel'));
    fireEvent.click(screen.getByTestId('mode-region'));

    const overlay = screen.getByTestId('region-select-overlay');
    fireEvent.mouseDown(overlay, { clientX: 100, clientY: 100, button: 0 });
    fireEvent.mouseMove(overlay, { clientX: 300, clientY: 200 });
    fireEvent.mouseUp(overlay);

    await waitFor(() => expect(addReviewNote).toHaveBeenCalled());
    const [sid, payload] = vi.mocked(addReviewNote).mock.calls[0];
    expect(sid).toBe('s1');
    expect(payload.mode).toBe('region');
    expect(payload.region?.w).toBeGreaterThan(0);
  });
});

describe('ReviewMode · 批注落库（A-点哪评哪-07）', () => {
  it('批注提交调服务端 addReviewNote（mode=freehand）', async () => {
    vi.mocked(addReviewNote).mockResolvedValue({
      status: 'ok',
      note: note({ mode: 'freehand' }) as never,
    });
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => screen.getByTestId('review-panel'));
    fireEvent.click(screen.getByTestId('mode-annotate'));

    const canvas = screen.getByTestId('annotation-canvas');
    fireEvent.mouseDown(canvas, { clientX: 20, clientY: 20, button: 0 });
    fireEvent.mouseMove(canvas, { clientX: 80, clientY: 80 });
    fireEvent.mouseUp(canvas);
    fireEvent.click(screen.getByTestId('annotation-submit'));

    await waitFor(() => expect(addReviewNote).toHaveBeenCalled());
    const [, payload] = vi.mocked(addReviewNote).mock.calls[0];
    expect(payload.mode).toBe('freehand');
    expect(payload.strokes?.length).toBe(1);
  });
});

describe('ReviewMode · 服务端不可达降级', () => {
  it('建会话失败 → 显式提示未同步，仍可本地记草稿', async () => {
    vi.mocked(openReviewSession).mockRejectedValue(new Error('offline'));
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => expect(screen.getByTestId('review-sync-error')).toBeTruthy());
    expect(screen.getByTestId('review-sync-error').textContent).toContain('未同步');
  });
});

describe('ReviewMode · 已有意见渲染', () => {
  it('服务端返回意见时渲染卡片（含短码）', async () => {
    vi.mocked(listReviewNotes).mockResolvedValue({
      status: 'ok',
      notes: [note({ short_code: 'r7', marker: '<a#x>' }) as never],
      count: 1,
    });
    render(<ReviewMode />);
    fireEvent.click(screen.getByTestId('review-toggle'));
    await waitFor(() => expect(screen.getByTestId('note-short-code')).toBeTruthy());
    expect(screen.getByTestId('note-short-code').textContent).toBe('r7');
  });
});
