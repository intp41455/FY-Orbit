/**
 * N3 状态同步 · 切片 3：草稿生命周期端点与乐观锁的**线上契约**测试。
 *
 * 这里刻意打真实 `fetch`（只 mock 网络层），因为本切片要证明的是
 * 「请求体里真的带了 `expected_version`」与「409 能被可靠区分」——
 * 断言点在 `RequestInit.body` 与抛出的错误类型上，不是在 mock 调用参数上。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

import { ApiError } from './client';
import {
  DSL_DRAFT_CONFLICT_STATUS,
  DslDraftConflictError,
  dslCanvasApi,
  isDslDraftConflict,
  type DslDocument,
} from './dslCanvas';

const DOC: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '甲' }] } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

const FLOW_VIEW = {
  id: 'flow-1',
  name: '未命名流程',
  flow_type: 'workflow',
  draft_doc: DOC,
  published_version: 0,
  draft_version: 4,
};

const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>();

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }));
}

function bodyOf(index = 0): Record<string, unknown> {
  const init = fetchMock.mock.calls[index][1];
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  document.querySelectorAll('meta[name="csrf-token"]').forEach((el) => el.remove());
});

describe('N3 · 草稿保存带上乐观锁版本', () => {
  it('PUT /api/dsl-lifecycle/flows/{id}/draft 的请求体确实含 expected_version', async () => {
    fetchMock.mockReturnValue(json({ ...FLOW_VIEW, draft_version: 5 }));

    const view = await dslCanvasApi.saveDraft('flow-1', DOC, { expected_version: 4 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/dsl-lifecycle/flows/flow-1/draft');
    expect(init?.method).toBe('PUT');
    // 核心断言：版本号真的进了请求体（缺了它就等于没有乐观锁）。
    expect(bodyOf()).toMatchObject({ expected_version: 4 });
    expect(bodyOf().doc).toEqual(DOC);
    // 保存成功后版本号 +1，后续保存要拿它当新的 expected_version。
    expect(view.draft_version).toBe(5);
  });

  it('没给 expected_version 时请求体里就不该出现该键（而不是塞 null/undefined）', async () => {
    fetchMock.mockReturnValue(json(FLOW_VIEW));
    await dslCanvasApi.saveDraft('flow-1', DOC);
    expect(bodyOf()).not.toHaveProperty('expected_version');
    expect(bodyOf()).toMatchObject({ doc: DOC });
  });

  it('flow_type 显式给出时进请求体；省略时同样不出现该键', async () => {
    fetchMock.mockReturnValue(json(FLOW_VIEW));
    await dslCanvasApi.saveDraft('flow-1', DOC, { flow_type: 'workflow', expected_version: 1 });
    expect(bodyOf()).toMatchObject({ flow_type: 'workflow' });

    fetchMock.mockReturnValue(json(FLOW_VIEW));
    await dslCanvasApi.saveDraft('flow-1', DOC, { expected_version: 2 });
    expect(bodyOf(1)).not.toHaveProperty('flow_type');
  });

  it('GET 读草稿拿回 draft_version（乐观锁的版本来源）', async () => {
    fetchMock.mockReturnValue(json(FLOW_VIEW));
    const view = await dslCanvasApi.getFlow('flow-1');
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/dsl-lifecycle/flows/flow-1');
    expect(fetchMock.mock.calls[0][1]?.method ?? 'GET').toBe('GET');
    expect(view.draft_version).toBe(4);
    expect(view.draft_doc).toEqual(DOC);
  });
});

describe('N3 · 版本冲突按 HTTP 409 判定（不靠文案匹配）', () => {
  it('409 → DslDraftConflictError，带 status=409 且 isDslDraftConflict 为真', async () => {
    // 后端 `_translate` 把领域异常转成 HTTPException，`error.code` 在 HTTP
    // 路径上已被吞掉：这里 detail 故意给一段**与前端文案不同**的英文，
    // 用来证明前端不是靠字符串匹配判定的。
    fetchMock.mockReturnValue(
      json({ detail: 'flow draft was modified by another session' }, 409),
    );

    const err = await dslCanvasApi
      .saveDraft('flow-1', DOC, { expected_version: 4 })
      .then(() => null, (e: unknown) => e);

    expect(err).toBeInstanceOf(DslDraftConflictError);
    expect((err as DslDraftConflictError).status).toBe(DSL_DRAFT_CONFLICT_STATUS);
    expect((err as DslDraftConflictError).status).toBe(409);
    expect((err as DslDraftConflictError).flowId).toBe('flow-1');
    expect(isDslDraftConflict(err)).toBe(true);
  });

  it('409 的包裹形态（统一错误信封）同样被判为冲突', async () => {
    fetchMock.mockReturnValue(
      json({ error: { code: 'dsl_draft_version_conflict', message: '版本不一致' } }, 409),
    );
    const err = await dslCanvasApi
      .saveDraft('flow-1', DOC, { expected_version: 4 })
      .then(() => null, (e: unknown) => e);
    expect(isDslDraftConflict(err)).toBe(true);
  });

  it('非 409 的错误**不**被判为冲突（避免把普通失败误报成版本冲突）', async () => {
    fetchMock.mockReturnValue(json({ detail: 'nodes must not be empty' }, 422));
    const err = await dslCanvasApi
      .saveDraft('flow-1', { version: '1', nodes: [], edges: [] }, { expected_version: 4 })
      .then(() => null, (e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect(isDslDraftConflict(err)).toBe(false);
  });

  it('isDslDraftConflict 认裸 ApiError(409)，且不认其它状态码', () => {
    expect(isDslDraftConflict(new ApiError(409, null, 'x'))).toBe(true);
    expect(isDslDraftConflict(new ApiError(500, null, 'x'))).toBe(false);
    expect(isDslDraftConflict(new Error('409 Conflict'))).toBe(false);
    expect(isDslDraftConflict(null)).toBe(false);
  });
});

describe('N3 · 首次保存前先建流程（不带空图，避开空图校验）', () => {
  it('createFlow 只在给了 doc 时才带上 doc', async () => {
    fetchMock.mockReturnValue(json({ ...FLOW_VIEW, draft_doc: null }));
    await dslCanvasApi.createFlow({ name: '未命名流程' });
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/dsl-lifecycle/flows');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(bodyOf()).toEqual({ name: '未命名流程', flow_type: 'workflow' });
  });

  it('给了 doc 就原样带上', async () => {
    fetchMock.mockReturnValue(json(FLOW_VIEW));
    await dslCanvasApi.createFlow({ name: '带图', flow_type: 'workflow', doc: DOC });
    expect(bodyOf()).toMatchObject({ name: '带图', doc: DOC });
  });
});