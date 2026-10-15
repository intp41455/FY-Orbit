/**
 * N3 状态同步 · 切片 3：页面级草稿通道 `useDslDraft` 的行为测试。
 *
 * 覆盖三条硬要求：
 *   ① 保存请求**真的带上 expected_version**（断言到 `dslCanvasApi.saveDraft` 的实参，
 *      线上契约那层见 `api/dslCanvas.draft.test.ts`）；
 *   ② 版本冲突（409）后**用户可见地**置冲突、**停掉自动保存**，不静默覆盖；
 *   ③ 未落盘前 storage 不是假绿灯 `'ok'`。
 *
 * 定时器不另造：本 hook 直接用 `useAutosave` 的 800ms / 5000ms，
 * 所以测试沿用 `useAutosave.test.tsx` 的做法（fake timers + 一次推进 5000ms）。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/dslCanvas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/dslCanvas')>()),
  dslCanvasApi: {
    getFlow: vi.fn(),
    saveDraft: vi.fn(),
    createFlow: vi.fn(),
  },
}));

import { dslCanvasApi, type DslDocument, type DslFlowView } from '../api/dslCanvas';
import { DslDraftConflictError } from '../api/dslCanvas';
import { useDslDraft } from './useDslDraft';

const FLUSH = 5_000;

const DOC_A: DslDocument = {
  version: '1',
  nodes: [{ id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '甲' }] } }],
  edges: [],
};
const DOC_B: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '甲' }] } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

function view(patch: Partial<DslFlowView> = {}): DslFlowView {
  return {
    id: 'flow-1',
    flow_type: 'workflow',
    draft_doc: null,
    published_version: 0,
    draft_version: 7,
    ...patch,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('N3 · useDslDraft 乐观锁', () => {
  it('首次保存：先建流程拿 draft_version，再带 expected_version 保存', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft).mockResolvedValue(view({ draft_version: 2 }));

    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });

    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(dslCanvasApi.createFlow).toHaveBeenCalledTimes(1);
    expect(dslCanvasApi.saveDraft).toHaveBeenCalledTimes(1);
    const [flowId, doc, opts] = vi.mocked(dslCanvasApi.saveDraft).mock.calls[0]!;
    expect(flowId).toBe('flow-1');
    expect(doc).toEqual(DOC_B);
    // 建流程返回 draft_version=1 → 第一次保存必须带 expected_version: 1
    expect(opts).toEqual({ expected_version: 1 });
    expect(result.current.draftVersion).toBe(2);
  });

  it('第二次保存用**上一次保存返回的** draft_version（不是旧的）', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft)
      .mockResolvedValueOnce(view({ draft_version: 2 }))
      .mockResolvedValueOnce(view({ draft_version: 3 }));

    const { rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    rerender({
      doc: { ...DOC_B, nodes: [...DOC_B.nodes, { id: 'x', type: 'template', params: { name: 'a', kind: 'generic' } }] },
    });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(vi.mocked(dslCanvasApi.saveDraft).mock.calls[0]![2]).toEqual({ expected_version: 1 });
    expect(vi.mocked(dslCanvasApi.saveDraft).mock.calls[1]![2]).toEqual({ expected_version: 2 });
  });

  it('409 → conflict 置位、自动保存停摆、**不再发任何保存请求**（无静默覆盖）', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft)
      .mockRejectedValueOnce(new DslDraftConflictError('flow-1'));

    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(result.current.conflict).toBe(true);
    expect(vi.mocked(dslCanvasApi.saveDraft)).toHaveBeenCalledTimes(1);

    // 冲突后再改文档 + 再等 6 秒（远超 debounce 与 interval）：
    // 一次都不许再发保存，更不许悄悄把本地内容覆盖上去。
    rerender({
      doc: { ...DOC_B, nodes: [...DOC_B.nodes, { id: 'y', type: 'template', params: { name: 'b', kind: 'generic' } }] },
    });
    await act(async () => { vi.advanceTimersByTime(FLUSH * 2); });
    expect(vi.mocked(dslCanvasApi.saveDraft)).toHaveBeenCalledTimes(1);
  });

  it('出口①「重新读取」：取回服务端版本、清冲突、解除自动保存停摆', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft)
      .mockRejectedValueOnce(new DslDraftConflictError('flow-1'))
      .mockResolvedValueOnce(view({ draft_version: 9 }));
    vi.mocked(dslCanvasApi.getFlow).mockResolvedValue(
      view({ draft_version: 9, draft_doc: DOC_A }),
    );

    const onLoad = vi.fn();
    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc, onLoad }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });
    expect(result.current.conflict).toBe(true);

    await act(async () => { await result.current.reload(); });

    expect(dslCanvasApi.getFlow).toHaveBeenCalledWith('flow-1');
    expect(onLoad).toHaveBeenCalledWith(DOC_A);
    expect(result.current.conflict).toBe(false);
    expect(result.current.draftVersion).toBe(9);

    // 冲突解除后恢复自动保存，并按**刚读到的** 9 继续带版本
    rerender({
      doc: { ...DOC_B, nodes: [...DOC_B.nodes, { id: 'z', type: 'template', params: { name: 'c', kind: 'generic' } }] },
    });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });
    const last = vi.mocked(dslCanvasApi.saveDraft).mock.calls.at(-1);
    expect(last?.[2]).toEqual({ expected_version: 9 });
  });

  it('出口②「覆盖保存」：刻意不带 expected_version（显式覆盖语义）', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft)
      .mockRejectedValueOnce(new DslDraftConflictError('flow-1'))
      .mockResolvedValueOnce(view({ draft_version: 12 }));

    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });
    expect(result.current.conflict).toBe(true);

    await act(async () => { await result.current.overwrite(); });

    const last = vi.mocked(dslCanvasApi.saveDraft).mock.calls.at(-1);
    expect(last?.[0]).toBe('flow-1');
    expect(last?.[1]).toEqual(DOC_B);
    // 覆盖 = 无条件写入，绝不能再拿旧版本号去撞 409
    expect(last?.[2]?.expected_version).toBeUndefined();
    expect(result.current.conflict).toBe(false);
    expect(result.current.draftVersion).toBe(12);
  });

  it('覆盖失败时给用户可见消息（不静默）', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft).mockRejectedValue(new Error('服务端炸了'));

    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });
    await act(async () => { await result.current.overwrite(); });

    expect(result.current.error).toContain('覆盖保存失败');
    expect(result.current.error).toContain('服务端炸了');
  });
});

describe('N3 · useDslDraft 诚实性（不是假绿灯）', () => {
  it('未落盘前 storage 不是 ok，对外状态是 idle 而不是 saved', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );

    expect(result.current.storage.state).not.toBe('ok');
    expect(result.current.storage.state).toBe('unlinked');
    expect(result.current.state).toBe('idle');
    expect(result.current.auto.savedAt).toBeNull();
    // 真的一个字节都没写出去过
    expect(dslCanvasApi.saveDraft).not.toHaveBeenCalled();
  });

  it('真实落盘成功后 storage 才升为 ok', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft).mockResolvedValue(view({ draft_version: 2 }));

    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(result.current.storage.state).toBe('ok');
    expect(result.current.state).toBe('saved');
    expect(result.current.storage.location).toContain('flow-1');
  });

  it('空画布（nodes 为空）不落盘：后端只收非空图，报 idle 而不是报已保存', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: { version: '1', nodes: [], edges: [] } as DslDocument } },
    );
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(dslCanvasApi.createFlow).not.toHaveBeenCalled();
    expect(dslCanvasApi.saveDraft).not.toHaveBeenCalled();
    expect(result.current.state).toBe('idle');
    expect(result.current.storage.state).not.toBe('ok');
  });

  it('保存失败（普通错误）不报 ok，错误可读', async () => {
    vi.useFakeTimers();
    vi.mocked(dslCanvasApi.createFlow).mockResolvedValue(view({ draft_version: 1 }));
    vi.mocked(dslCanvasApi.saveDraft).mockRejectedValue(new Error('磁盘炸了'));

    const { result, rerender } = renderHook(
      ({ doc }) => useDslDraft({ doc }),
      { initialProps: { doc: DOC_A as DslDocument | null } },
    );
    rerender({ doc: DOC_B });
    await act(async () => { vi.advanceTimersByTime(FLUSH); });

    expect(result.current.storage.state).not.toBe('ok');
    expect(result.current.state).toBe('error');
  });
});

describe('N3 · useDslDraft 首屏载入', () => {
  it('已知 flow_id：读回 draft_version 与远端文档（不静默丢弃本地）', async () => {
    vi.useRealTimers();
    vi.mocked(dslCanvasApi.getFlow).mockResolvedValue(
      view({ draft_version: 5, draft_doc: DOC_B }),
    );
    const onLoad = vi.fn();

    renderHook(() => useDslDraft({ doc: DOC_A, flowId: 'flow-1', onLoad }));

    await waitFor(() => expect(onLoad).toHaveBeenCalledWith(DOC_B));
    expect(dslCanvasApi.getFlow).toHaveBeenCalledWith('flow-1');
  });

  it('没有 flow_id 时不白读一次（首次保存才建）', () => {
    renderHook(() => useDslDraft({ doc: DOC_A }));
    expect(dslCanvasApi.getFlow).not.toHaveBeenCalled();
    expect(dslCanvasApi.createFlow).not.toHaveBeenCalled();
  });
});