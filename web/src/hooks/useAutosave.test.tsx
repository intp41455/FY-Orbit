/**
 * P1 前端半边测试（A-基座质保-12 W2 / A-基座质保-14 W4 / A-基座质保-11 W1 钩子面）。
 *
 * 只测契约，不测像素：
 *   - W2 四态：dirty → saving → saved；失败 → error；四态文案由基座统一给出。
 *   - W4 ① 写前预检；② 空间不足 / 只读时进降级路径且**改动不丢**；
 *     ③ 结构化回执（code / message / hint）；④ 恢复后立即补写积压改动。
 *   - W1 `useBase` 声明与未知能力上报、留痕 sink 的诚实状态。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  BASE_CAPABILITIES,
  SAVE_STATE_LABELS,
  formatBytes,
  formatRelative,
  formatSavedAt,
  receiptFromError,
  receiptFromPreflight,
  useAutosave,
  useBase,
  type PreflightResult,
  type SaveContext,
  type StorageErrorReceipt,
} from './useAutosave';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

const FLUSH = 5_000; // 覆盖 debounce(800) 与最长间隔(5000)

describe('useAutosave · W2 四态', () => {
  it('初始为「已保存」，改动后进「未保存改动」并计数', () => {
    const save = vi.fn();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save }),
      { initialProps: { value: 'a' } },
    );

    expect(result.current.state).toBe('saved');
    expect(result.current.pendingChanges).toBe(0);
    expect(result.current.summary.stateLabel).toBe(SAVE_STATE_LABELS.saved);

    rerender({ value: 'b' });
    expect(result.current.state).toBe('dirty');
    expect(result.current.pendingChanges).toBe(1);
    expect(result.current.summary.stateLabel).toBe(SAVE_STATE_LABELS.dirty);

    rerender({ value: 'c' });
    expect(result.current.pendingChanges).toBe(2);
  });

  it('停止输入后落盘：dirty → saving → saved 并记录时刻与位置', async () => {
    const save = vi.fn((_v: string, _ctx: SaveContext) => ({ location: '/local/draft.json', bytes: 2048 }));
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save, now: () => 1_700_000_000_000 }),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    await act(async () => {
      vi.advanceTimersByTime(FLUSH);
    });

    expect(save).toHaveBeenCalledTimes(1);
    const ctx = save.mock.calls[0][1] as unknown as SaveContext;
    expect(ctx.key).toBe('k');
    expect(ctx.fallback).toBe(false);

    expect(result.current.state).toBe('saved');
    expect(result.current.pendingChanges).toBe(0);
    expect(result.current.location).toBe('/local/draft.json');
    expect(result.current.bytes).toBe(2048);
    expect(result.current.savedAt).toBe(1_700_000_000_000);
    expect(result.current.summary.stateLabel).toMatch(/^已保存到 /);
    expect(result.current.history.at(-1)?.state).toBe('saved');
  });

  it('落盘期间处于「保存中」（可被界面上报，不阻断输入）', async () => {
    let release: (() => void) | undefined;
    const save = vi.fn(() => new Promise<void>((resolve) => { release = resolve; }));
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save }),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    await act(async () => {
      vi.advanceTimersByTime(FLUSH);
      await Promise.resolve();
    });
    expect(result.current.state).toBe('saving');
    expect(result.current.summary.stateLabel).toBe(SAVE_STATE_LABELS.saving);

    await act(async () => {
      release?.();
      await Promise.resolve();
    });
    expect(result.current.state).toBe('saved');
  });

  it('连续输入时最长间隔也会落盘一次（不是等用户停手才存）', async () => {
    const save = vi.fn();
    const { rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save, delayMs: 800, intervalMs: 2000 }),
      { initialProps: { value: 'v0' } },
    );

    // 每 700ms 改一次（< delayMs=800）→ debounce 被反复推后（最后推到 t=2900），
    // 所以下面这一次落盘只可能是 intervalMs=2000 的「最长间隔」（interval 在 t=700 建，
    // t=2700 到点）触发的——证明「不是等用户停手才存」。
    for (let i = 1; i <= 3; i += 1) {
      await act(async () => {
        vi.advanceTimersByTime(700);
      });
      rerender({ value: `v${i}` });
    }
    expect(save).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(700);   // t=2700：最长间隔到点
    });
    expect(save).toHaveBeenCalledTimes(1);
  });

  it('写失败 → error 且**保留排队改动**；重试成功后清零', async () => {
    const save = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue(undefined);
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save }),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    await act(async () => {
      vi.advanceTimersByTime(FLUSH);
    });

    expect(result.current.state).toBe('error');
    expect(result.current.error?.code).toBe('write_failed');
    expect(result.current.error?.hint).toContain('重试');
    expect(result.current.pendingChanges).toBe(1);   // 现场保留，不静默丢弃
    expect(result.current.history.at(-1)?.state).toBe('error');

    await act(async () => {
      await result.current.retry();
    });
    expect(result.current.state).toBe('saved');
    expect(result.current.error).toBeNull();
    expect(result.current.pendingChanges).toBe(0);
  });

  it('saveNow 可绕过 debounce 立即落盘', async () => {
    const save = vi.fn();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save }),
      { initialProps: { value: 'a' } },
    );
    rerender({ value: 'b' });
    await act(async () => {
      await result.current.saveNow();
    });
    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe('saved');
  });
});

describe('useAutosave · W4 磁盘写满 / 只读盘', () => {
  it('写前预检：空间不足 → 走备用目录，改动不丢，并给出结构化回执', async () => {
    const calls: SaveContext[] = [];
    const save = vi.fn((_v: string, ctx: SaveContext) => {
      calls.push(ctx);
      return ctx.fallback ? { location: ctx.directory } : undefined;
    });
    let pre: PreflightResult = {
      writable: true, directory: '/root', freeBytes: 1024, requiredBytes: 4096,
    };
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({
        key: 'k', value, save, preflight: () => pre, fallbackDirectory: '/backup',
      }),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    await act(async () => {
      vi.advanceTimersByTime(FLUSH);
    });

    expect(calls.map((c) => c.directory)).toEqual(['/backup']);   // 主盘没写
    expect(result.current.storage).toBe('degraded');
    expect(result.current.state).toBe('saved');                   // 降级但没丢
    expect(result.current.error?.code).toBe('disk_full');
    expect(result.current.error?.fallbackUsed).toBe(true);
    expect(result.current.error?.message).toContain('无法保存');
    expect(result.current.error?.hint).toContain('/backup');
    expect(result.current.pendingChanges).toBe(0);

    // 盘恢复：继续编辑 → 回到主盘，storage 复位
    pre = { writable: true, directory: '/root', freeBytes: 1_000_000, requiredBytes: 4096 };
    rerender({ value: 'c' });
    await act(async () => {
      vi.advanceTimersByTime(FLUSH);
    });
    expect(calls.at(-1)?.directory).toBe('/root');
    expect(calls.at(-1)?.fallback).toBe(false);
    expect(result.current.storage).toBe('ok');
    expect(result.current.error).toBeNull();
  });

  it('只读盘且没有备用目录 → 明确失败、进入降级、改动留在队列；恢复后补写', async () => {
    const save = vi.fn();
    let pre: PreflightResult = {
      writable: false, directory: '/root', reason: '磁盘被挂载为只读',
    };
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save, preflight: () => pre }),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(save).not.toHaveBeenCalled();
    expect(result.current.state).toBe('error');
    expect(result.current.storage).toBe('degraded');
    expect(result.current.error?.code).toBe('read_only');
    expect(result.current.error?.hint).toContain('只读');

    // 降级期间的改动继续排队（只增不减）
    rerender({ value: 'c' });
    rerender({ value: 'd' });
    expect(result.current.pendingChanges).toBeGreaterThanOrEqual(3);
    expect(save).not.toHaveBeenCalled();

    // 恢复可写：自动重试把积压改动一次补写干净
    pre = { writable: true, directory: '/root', freeBytes: 1_000_000, requiredBytes: 1 };
    await act(async () => {
      vi.advanceTimersByTime(3_000);
    });
    expect(save).toHaveBeenCalledTimes(1);
    expect((save.mock.calls[0][0] as unknown as string)).toBe('d');  // 写的是最新值
    expect(result.current.storage).toBe('ok');
    expect(result.current.pendingChanges).toBe(0);
    expect(result.current.state).toBe('saved');
  });

  it('写时抛 QuotaExceededError → 归一成 quota_exceeded，并能降级到备用目录', async () => {
    const quota = () => {
      const e = new Error('quota');
      e.name = 'QuotaExceededError';
      return e;
    };
    const save = vi.fn((_v: string, ctx: SaveContext) => {
      if (!ctx.fallback) throw quota();
      return undefined;
    });
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save, fallbackDirectory: '/backup' }),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(save).toHaveBeenCalledTimes(2);
    expect(result.current.storage).toBe('degraded');
    expect(result.current.state).toBe('saved');
    expect(result.current.error?.code).toBe('quota_exceeded');
    expect(result.current.location).toBe('/backup');
  });

  it('预检本身拿不到信息时不编造：不阻断落盘', async () => {
    const save = vi.fn();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save, preflight: () => undefined }),
      { initialProps: { value: 'a' } },
    );
    rerender({ value: 'b' });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });
    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.storage).toBe('ok');
    expect(result.current.freeBytes).toBeNull();
  });

  it('enabled=false 时完全不落盘（只读界面）', async () => {
    const save = vi.fn();
    const { rerender } = renderHook(
      ({ value }) => useAutosave({ key: 'k', value, save, enabled: false }),
      { initialProps: { value: 'a' } },
    );
    rerender({ value: 'b' });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });
    expect(save).not.toHaveBeenCalled();
  });
});

describe('useAutosave · 纯函数', () => {
  it('formatSavedAt 给出「已保存到 HH:mm」', () => {
    const at = new Date(2026, 9, 7, 14, 3, 5).getTime();
    expect(formatSavedAt(at, at)).toBe('已保存到 14:03');
    expect(formatSavedAt(null)).toBe(SAVE_STATE_LABELS.saved);
  });

  it('formatBytes / formatRelative 不猜、不装作知道', () => {
    expect(formatBytes(null)).toBe('未知');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(formatRelative(null, 0)).toBe('尚无记录');
    expect(formatRelative(1000, 2000)).toBe('刚刚');
    expect(formatRelative(0, 30_000)).toBe('30 秒前');
    expect(formatRelative(0, 5 * 60_000)).toBe('5 分钟前');
  });

  it('receiptFromError 把 DOMException 名字映射成结构化 code', () => {
    const mk = (name: string) => {
      const e = new Error('x');
      e.name = name;
      return e;
    };
    expect(receiptFromError(mk('QuotaExceededError'), '/d', false, 1).code).toBe('quota_exceeded');
    expect(receiptFromError(mk('ReadOnlyError'), '/d', false, 1).code).toBe('read_only');
    expect(receiptFromError(mk('NotAllowedError'), '/d', false, 1).code).toBe('permission_denied');
    expect(receiptFromError(new Error('ENOSPC'), '/d', false, 1).code).toBe('disk_full');
    expect(receiptFromError(new Error('nope'), '/d', false, 1).code).toBe('write_failed');
    const named = receiptFromError(mk('QuotaExceededError'), '/d', true, 1);
    expect(named.fallbackUsed).toBe(true);
    expect(named.hint).toContain('备用目录都不可写');
  });

  it('receiptFromPreflight 区分「空间不足」与「只读」', () => {
    const full = receiptFromPreflight(
      { writable: true, directory: '/d', freeBytes: 10, requiredBytes: 99 }, 1);
    expect(full.code).toBe('disk_full');
    expect(full.message).toContain('10 B');
    expect(full.message).toContain('99 B');
    const ro = receiptFromPreflight({ writable: false, directory: '/d', reason: '挂载为只读' }, 1);
    expect(ro.code).toBe('read_only');
    expect(ro.message).toContain('挂载为只读');
  });
});

describe('useBase · W1 接入声明', () => {
  it('省略 capabilities = 声明全部四项', () => {
    const { result } = renderHook(() => useBase({ surface: 'workbench' }));
    expect(result.current.surface).toBe('workbench');
    expect(result.current.capabilities).toEqual([...BASE_CAPABILITIES]);
    expect(result.current.unknownCapabilities).toEqual([]);
  });

  it('只声明一部分时如实报告缺的部分（不补齐、不假装）', () => {
    const { result } = renderHook(() => useBase({
      surface: 'chat', capabilities: ['realtime_save', 'telepathy'],
    }));
    expect(result.current.capabilities).toEqual(['realtime_save']);
    expect(result.current.unknownCapabilities).toEqual(['telepathy']);
  });

  it('留痕：注入 sink 才标 sent；未注入只在本地排队（pending-sink）', async () => {
    const sink = vi.fn();
    const withSink = renderHook(() => useBase({ surface: 'a' }, { record: sink, now: () => 7 }));
    const frame = withSink.result.current.audit.record('task.updated', { field: 'goal' });
    expect(frame.status).toBe('sent');
    expect(sink).toHaveBeenCalledWith(frame);
    expect(frame.at).toBe(7);

    const noSink = renderHook(() => useBase({ surface: 'b' }));
    expect(noSink.result.current.audit.record('task.updated').status).toBe('pending-sink');
  });

  it('留痕入链失败时状态退回 pending-sink（不假装已入链）', async () => {
    const sink = vi.fn().mockRejectedValue(new Error('chain down'));
    const { result } = renderHook(() => useBase({ surface: 'a' }, { record: sink }));
    const frame = result.current.audit.record('task.updated');
    await act(async () => { await Promise.resolve(); });
    expect(frame.status).toBe('pending-sink');
  });

  it('统一错误回执：任意异常都经它归一，供界面直接展示', () => {
    const { result } = renderHook(() => useBase({ surface: 'a' }, { now: () => 5 }));
    let receipt: StorageErrorReceipt | undefined;
    act(() => {
      receipt = result.current.errors.receipt(new Error('ENOSPC'), { directory: '/d' });
    });
    expect(receipt?.code).toBe('disk_full');
    expect(receipt?.directory).toBe('/d');
    expect(result.current.errors.last?.code).toBe('disk_full');
  });

  it('本地优先：存储位置从 useAutosave 递进来，不另查一份', () => {
    const { result } = renderHook(() => useBase(
      { surface: 'a' },
      { storage: { state: 'degraded', location: '/backup', bytes: 12 } },
    ));
    expect(result.current.storage).toEqual({ state: 'degraded', location: '/backup', bytes: 12 });
  });
});
