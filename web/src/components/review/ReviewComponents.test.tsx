/**
 * P9 前端单测 · 圈选 / 批注 / 热刷新 / 意见卡片（A-点哪评哪-05/06/07）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { RegionSelectOverlay } from './RegionSelectOverlay';
import { AnnotationCanvas } from './AnnotationCanvas';
import { RefreshLoopBar } from './RefreshLoop';
import { ReviewNoteCard } from './ReviewNoteList';
import type { ReviewNoteView } from '../../api/review';

beforeEach(() => {
  vi.restoreAllMocks();
});

// --------------------------------------------------------------------------- #
// A-点哪评哪-06 圈选
// --------------------------------------------------------------------------- #

describe('RegionSelectOverlay · A-点哪评哪-06', () => {
  it('拖拽后回调归一化区域', async () => {
    const onSelect = vi.fn();
    render(<RegionSelectOverlay onSelect={onSelect} onCancel={vi.fn()} />);
    const overlay = screen.getByTestId('region-select-overlay');

    fireEvent.mouseDown(overlay, { clientX: 100, clientY: 50, button: 0 });
    fireEvent.mouseMove(overlay, { clientX: 300, clientY: 150 });
    fireEvent.mouseUp(overlay);

    expect(onSelect).toHaveBeenCalledTimes(1);
    const region = onSelect.mock.calls[0][0];
    expect(region.w).toBeGreaterThan(0);
    expect(region.h).toBeGreaterThan(0);
    // 归一化：所有值落在 0~1
    for (const v of Object.values(region)) {
      expect(v as number).toBeGreaterThanOrEqual(0);
      expect(v as number).toBeLessThanOrEqual(1);
    }
  });

  it('过小拖拽视为误触，不提交而取消', () => {
    const onSelect = vi.fn();
    const onCancel = vi.fn();
    render(<RegionSelectOverlay onSelect={onSelect} onCancel={onCancel} />);
    const overlay = screen.getByTestId('region-select-overlay');

    fireEvent.mouseDown(overlay, { clientX: 100, clientY: 50, button: 0 });
    fireEvent.mouseMove(overlay, { clientX: 103, clientY: 52 });
    fireEvent.mouseUp(overlay);

    expect(onSelect).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });

  it('Esc 取消圈选', async () => {
    const onCancel = vi.fn();
    render(<RegionSelectOverlay onSelect={vi.fn()} onCancel={onCancel} />);
    await userEvent.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalled();
  });

  it('拖拽中显示选区框', () => {
    render(<RegionSelectOverlay onSelect={vi.fn()} onCancel={vi.fn()} />);
    const overlay = screen.getByTestId('region-select-overlay');
    expect(screen.queryByTestId('region-select-box')).toBeNull();
    fireEvent.mouseDown(overlay, { clientX: 100, clientY: 50, button: 0 });
    fireEvent.mouseMove(overlay, { clientX: 300, clientY: 150 });
    expect(screen.getByTestId('region-select-box')).toBeTruthy();
  });
});

// --------------------------------------------------------------------------- #
// A-点哪评哪-07 画笔 + 语音
// --------------------------------------------------------------------------- #

describe('AnnotationCanvas · A-点哪评哪-07', () => {
  it('无内容时提交按钮禁用', () => {
    render(<AnnotationCanvas onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByTestId('annotation-submit')).toBeDisabled();
  });

  it('画线后提交归一化笔迹', () => {
    const onSubmit = vi.fn();
    render(<AnnotationCanvas onSubmit={onSubmit} onCancel={vi.fn()} />);
    const canvas = screen.getByTestId('annotation-canvas');

    fireEvent.mouseDown(canvas, { clientX: 100, clientY: 50, button: 0 });
    fireEvent.mouseMove(canvas, { clientX: 200, clientY: 100 });
    fireEvent.mouseMove(canvas, { clientX: 300, clientY: 150 });
    fireEvent.mouseUp(canvas);

    fireEvent.click(screen.getByTestId('annotation-submit'));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.strokes.length).toBe(1);
    // 归一化点集
    expect(payload.strokes[0].points[0].x).toBeLessThanOrEqual(1);
    expect(payload.strokes[0].points[0].x).toBeGreaterThanOrEqual(0);
  });

  it('撤销移除最后一笔', () => {
    render(<AnnotationCanvas onSubmit={vi.fn()} onCancel={vi.fn()} />);
    const canvas = screen.getByTestId('annotation-canvas');
    fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10, button: 0 });
    fireEvent.mouseMove(canvas, { clientX: 60, clientY: 60 });
    fireEvent.mouseUp(canvas);
    expect(screen.getByTestId('annotation-submit')).not.toBeDisabled();
    fireEvent.click(screen.getByTestId('annotation-undo'));
    expect(screen.getByTestId('annotation-submit')).toBeDisabled();
  });

  it('麦克风不可用时如实提示（不假装录上）', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    render(<AnnotationCanvas onSubmit={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByTestId('annotation-record'));
    await waitFor(() => expect(screen.getByTestId('annotation-notice')).toBeTruthy());
    expect(screen.getByTestId('annotation-notice').textContent).toContain('麦克风不可用');
  });

  it('手填转写文本会随提交带上', () => {
    const onSubmit = vi.fn();
    render(<AnnotationCanvas onSubmit={onSubmit} onCancel={vi.fn()} />);
    const canvas = screen.getByTestId('annotation-canvas');
    fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10, button: 0 });
    fireEvent.mouseMove(canvas, { clientX: 60, clientY: 60 });
    fireEvent.mouseUp(canvas);
    fireEvent.change(screen.getByTestId('annotation-transcript'), {
      target: { value: '这里往左挪' },
    });
    fireEvent.click(screen.getByTestId('annotation-submit'));
    expect(onSubmit.mock.calls[0][0].audio_transcript).toBe('这里往左挪');
  });
});

// --------------------------------------------------------------------------- #
// A-点哪评哪-05 热刷新状态条
// --------------------------------------------------------------------------- #

describe('RefreshLoopBar · A-点哪评哪-05', () => {
  const base = { refreshNote: '', iteration: 1, onManualRefresh: vi.fn() };

  it('显示各状态文案', () => {
    const { rerender } = render(<RefreshLoopBar {...base} refreshState="idle" />);
    expect(screen.getByTestId('refresh-state-label').textContent).toContain('待改动');
    rerender(<RefreshLoopBar {...base} refreshState="refreshing" />);
    expect(screen.getByTestId('refresh-state-label').textContent).toContain('刷新中');
    rerender(<RefreshLoopBar {...base} refreshState="applied" />);
    expect(screen.getByTestId('refresh-state-label').textContent).toContain('已应用');
    rerender(<RefreshLoopBar {...base} refreshState="failed" />);
    expect(screen.getByTestId('refresh-state-label').textContent).toContain('刷新失败');
  });

  it('显示轮次并可手动触发', () => {
    const onManualRefresh = vi.fn();
    render(<RefreshLoopBar {...base} refreshState="idle" iteration={3} onManualRefresh={onManualRefresh} />);
    expect(screen.getByTestId('refresh-iteration').textContent).toContain('3');
    fireEvent.click(screen.getByTestId('refresh-now'));
    expect(onManualRefresh).toHaveBeenCalled();
  });

  it('data 属性带状态（便于断言）', () => {
    render(<RefreshLoopBar {...base} refreshState="failed" />);
    expect(screen.getByTestId('refresh-loop-bar').getAttribute('data-refresh-state')).toBe('failed');
  });
});

// --------------------------------------------------------------------------- #
// 意见卡片（承载三类定位 + TOKEN 三项）
// --------------------------------------------------------------------------- #

function note(over: Partial<ReviewNoteView> = {}): ReviewNoteView {
  return {
    id: 'n1', session_id: 's1', page: '/chat', mode: 'dom',
    tag: 'button', element_id: 'save', element_class: 'primary',
    text: '保存', selector: 'body > button#save', dom_path: ['body', 'button'],
    region: {}, strokes: [], audio_ref: '', audio_transcript: '',
    note: '按钮太大', marker: '<button#save.primary>', short_code: 'r1',
    dom_digest: 'abc', prev_dom_digest: '', changed: false,
    state: 'open', applied_at: null, created_at: '2026-10-07T00:00:00+08:00',
    ...over,
  };
}

describe('ReviewNoteCard', () => {
  it('展示短码与真写标记（TOKEN 优化三项可见）', () => {
    render(<ReviewNoteCard note={note()} />);
    expect(screen.getByTestId('note-short-code').textContent).toBe('r1');
    expect(screen.getByTestId('note-marker').textContent).toBe('<button#save.primary>');
  });

  it('结构变更时显示告警', () => {
    render(<ReviewNoteCard note={note({ changed: true, prev_dom_digest: 'old', dom_digest: 'new' })} />);
    expect(screen.getByTestId('note-changed')).toBeTruthy();
  });

  it('圈选模式展示归一化坐标', () => {
    render(<ReviewNoteCard note={note({ mode: 'region', region: { x: 0.1, y: 0.2, w: 0.3, h: 0.4 } })} />);
    expect(screen.getByTestId('note-region').textContent).toContain('0.100');
  });

  it('批注模式展示笔数与语音', () => {
    render(<ReviewNoteCard note={note({
      mode: 'freehand',
      strokes: [{ points: [{ x: 0.1, y: 0.1 }], color: '#f00', width: 2 }],
      audio_ref: 'review-audio/x.webm',
      audio_transcript: '往左挪',
    })} />);
    const el = screen.getByTestId('note-freehand').textContent || '';
    expect(el).toContain('1 笔');
    expect(el).toContain('含语音');
  });

  it('状态流转回调', () => {
    const onStateChange = vi.fn();
    render(<ReviewNoteCard note={note()} onStateChange={onStateChange} />);
    fireEvent.click(screen.getByTestId('note-resolve-r1'));
    expect(onStateChange).toHaveBeenCalledWith('n1', 'resolved');
  });

  it('删除回调', () => {
    const onDelete = vi.fn();
    render(<ReviewNoteCard note={note()} onDelete={onDelete} />);
    fireEvent.click(screen.getByTestId('note-delete-r1'));
    expect(onDelete).toHaveBeenCalledWith('n1');
  });

  it('data 属性带 mode 与 state', () => {
    render(<ReviewNoteCard note={note({ mode: 'region', state: 'resolved' })} />);
    const el = screen.getByTestId('review-note-r1');
    expect(el.getAttribute('data-mode')).toBe('region');
    expect(el.getAttribute('data-state')).toBe('resolved');
  });
});
