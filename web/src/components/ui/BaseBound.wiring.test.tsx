/**
 * P0-1 基座接入 · 端到端行为验收（接的是**真实界面路径**，不是裸 hook）。
 *
 * 派单书判据 4 / 5 说的是「接好之后的行为」，所以这里渲染的是一块**真的接了基座
 * 的可编辑界面**：`<BaseBound>` 包住一个受控 textarea，状态由 `useAutosave` 驱动
 * ——页面里就是这么写的，测试走的也是这条路径。
 *
 *   判据 ④ 行为可用：编辑后界面出现「已保存到 HH:MM」，且 ≤5s 内转 `saved`；
 *   判据 ⑤ 降级不丢：模拟 IO 失败 → 状态转 `error`、`pendingChanges` 只增不减，
 *             恢复后（用户点「重试」）把队列里的**最新值**补写。
 *
 * 时钟用注入的 `now` 与假定时器，断言不依赖真实等待。
 */
import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';

import { useAutosave, type SaveContext } from '../../hooks/useAutosave';
import { BaseBound } from './SaveStatusIndicator';

/** 固定时钟：14:03（「已保存到 14:03」是可断言的）。 */
const NOW = new Date(2026, 9, 7, 14, 3, 5).getTime();

type SaveFn = (value: string, ctx: SaveContext) => unknown;

function WiredDraft({
  save, delayMs = 800, intervalMs = 5000,
}: { save: SaveFn; delayMs?: number; intervalMs?: number }) {
  const [draft, setDraft] = useState('初稿');
  const auto = useAutosave({
    key: 'p0-wiring', value: draft,
    save: save as never,
    delayMs, intervalMs,
    now: () => NOW,
  });
  return (
    <BaseBound
      surface="p0-wiring"
      state={auto.state}
      savedAt={auto.savedAt}
      pendingChanges={auto.pendingChanges}
      error={auto.error}
      onRetry={() => { void auto.retry(); }}
      now={NOW}
    >
      <label htmlFor="draft">草稿</label>
      <textarea
        id="draft"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
    </BaseBound>
  );
}

function statusText(): string {
  return document.querySelector('[role="status"]')?.textContent ?? '';
}

function queuedChanges(container: HTMLElement): number {
  const m = /排队\s*(\d+)\s*处改动/.exec(container.textContent ?? '');
  return m ? Number(m[1]) : 0;
}

afterEach(() => {
  vi.useRealTimers();
});

describe('P0-1 判据④ · 接好之后编辑即保存（界面可见）', () => {
  it('编辑后界面出现「已保存到 14:03」，且在 5s 内转 saved', async () => {
    vi.useFakeTimers();
    const save = vi.fn((_v: string) => ({ location: 'local:p0-wiring', bytes: 12 }));
    render(<WiredDraft save={save} />);

    // 已接入基座：声明与指示器都在
    const bound = document.querySelector('[data-base-bound="true"]');
    expect(bound?.getAttribute('data-base-surface')).toBe('p0-wiring');
    expect(bound?.getAttribute('data-base-capabilities'))
      .toBe('realtime_save,audit_trail,local_first,error_receipt');

    fireEvent.change(document.getElementById('draft') as HTMLTextAreaElement,
                     { target: { value: '改了一行' } });
    expect(statusText()).toBe('未保存改动');

    // 最长不落盘间隔（5s）内必定落盘 —— 判据给的就是这个上界
    await act(async () => { vi.advanceTimersByTime(5000); });

    expect(save).toHaveBeenCalledTimes(1);
    expect((save.mock.calls[0] as unknown[])[0]).toBe('改了一行');
    expect(statusText()).toBe('已保存到 14:03');
  });

  it('输入不被打断：状态变化后 textarea 仍然是文档焦点', async () => {
    vi.useFakeTimers();
    const save = vi.fn(() => ({ location: 'local:p0-wiring', bytes: 1 }));
    render(<WiredDraft save={save} />
    );
    const box = document.getElementById('draft') as HTMLTextAreaElement;
    box.focus();
    fireEvent.change(box, { target: { value: '还在打字' } });
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(document.activeElement).toBe(box);
  });
});

describe('P0-1 判据⑤ · IO 失败不丢改动，恢复后补写', () => {
  it('失败 → error + 排队只增不减；重试后补写的是最新值', async () => {
    vi.useFakeTimers();
    let failing = true;
    const save = vi.fn((_v: string) => {
      if (failing) throw new Error('磁盘写爆了');
      return { location: 'local:p0-wiring', bytes: 7 };
    });
    const { container } = render(
      <WiredDraft save={save} delayMs={100} intervalMs={500} />,
    );
    const box = document.getElementById('draft') as HTMLTextAreaElement;

    fireEvent.change(box, { target: { value: 'b' } });
    await act(async () => { vi.advanceTimersByTime(100); });

    // 失败态：可见回执（role=alert）+ 明确文案
    expect(statusText()).toBe('保存失败');
    const alert = document.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain('重试');

    const queuedAfterFirstFailure = queuedChanges(container);
    expect(queuedAfterFirstFailure).toBeGreaterThan(0);

    // 降级期间继续编辑：排队只增不减（现场绝不被静默清掉）
    fireEvent.change(box, { target: { value: 'c' } });
    fireEvent.change(box, { target: { value: 'd' } });
    await act(async () => { vi.advanceTimersByTime(500); });
    expect(queuedChanges(container))
      .toBeGreaterThanOrEqual(queuedAfterFirstFailure);

    // 盘恢复：用户点「重试」→ 一次补写干净，写的是最新值 'd'
    failing = false;
    const retry = document.querySelector<HTMLButtonElement>(
      '.ui-save-status__retry',
    );
    expect(retry).not.toBeNull();
    await act(async () => {
      retry?.click();
      await Promise.resolve();
    });

    expect(statusText()).toBe('已保存到 14:03');
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(queuedChanges(container)).toBe(0);
    const values = save.mock.calls.map((c) => (c as unknown[])[0]);
    expect(values.at(-1)).toBe('d');
  });
});
