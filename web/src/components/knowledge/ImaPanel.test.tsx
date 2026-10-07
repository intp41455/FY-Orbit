import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/knowledge', async () => {
  const actual = await vi.importActual<typeof import('../../api/knowledge')>('../../api/knowledge');
  return { ...actual, knowledgeApi: { imaSearch: vi.fn(), imaStatus: vi.fn() } };
});

import { knowledgeApi } from '../../api/knowledge';
import type { ImaSearchHit, ImaSearchResponse } from '../../api/knowledge';
import { ImaPanel } from './ImaPanel';

const api = () => knowledgeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const KB = '7509748362520236';

const HIT: ImaSearchHit = {
  media_id: 'm1',
  title: '八字入门.md',
  introduction: '摘要：日主强弱',
  content: '全文：日主强弱判断口诀',
  content_truncated: false,
  tags: ['八字'],
  folder: '八字基础',
  type: '7',
  can_fetch_content: true,
  can_preview: true,
  preview_only: false,
  origin_url: '',
  src: `ima://${KB}/m1`,
};

function response(over: Partial<ImaSearchResponse> = {}): ImaSearchResponse {
  return {
    query: '八字',
    kb_id: KB,
    total: 1,
    page: 1,
    page_size: 10,
    pages: 1,
    results: [HIT],
    channel: 'mcp',
    cached: false,
    cache_time: null,
    errors: [],
    ...over,
  };
}

const STATUS = {
  source_id: 'ima',
  kb_id: KB,
  configured: true,
  channels: { mcp: { configured: true, available: true }, rest: { configured: false } },
  credentials_present: {},
  cache_path: '.runtime/ima_cache',
};

async function search(q = '八字') {
  fireEvent.change(screen.getByLabelText('ima 检索词'), { target: { value: q } });
  fireEvent.click(screen.getByTestId('ima-search-btn'));
}

describe('ImaPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api().imaStatus.mockResolvedValue(STATUS);
  });

  it('展示来源标注', async () => {
    render(<ImaPanel />);
    expect(screen.getByTestId('ima-attribution').textContent).toContain('陛下的 ima 公共知识库');
  });

  it('搜索「八字」展示命中与 src: 出处，并可展开原文', async () => {
    api().imaSearch.mockResolvedValue(response());
    render(<ImaPanel />);
    await search();
    expect(await screen.findByTestId('ima-hit-m1')).toBeInTheDocument();
    expect(screen.getByTestId('ima-src-m1').textContent).toBe(`src: ima://${KB}/m1`);
    expect(api().imaSearch).toHaveBeenCalledWith(expect.objectContaining({ query: '八字', page: 1 }));
    fireEvent.click(screen.getByTestId('ima-open-m1'));
    expect(screen.getByText(/全文：日主强弱判断口诀/)).toBeInTheDocument();
  });

  it('有 origin_url 时才渲染外链', async () => {
    api().imaSearch.mockResolvedValue(
      response({ results: [{ ...HIT, origin_url: 'https://ima.qq.com/x' }] }),
    );
    render(<ImaPanel />);
    await search();
    const link = await screen.findByTestId('ima-url-m1');
    expect(link.getAttribute('href')).toBe('https://ima.qq.com/x');
  });

  it('离线缓存结果显示横幅，不冒充实时', async () => {
    api().imaSearch.mockResolvedValue(
      response({ cached: true, cache_time: '2026-10-07T08:00:00+00:00', channel: 'mcp+cache' }),
    );
    render(<ImaPanel />);
    await search();
    expect(await screen.findByTestId('ima-cache-banner')).toBeInTheDocument();
  });

  it('零命中如实显示空态', async () => {
    api().imaSearch.mockResolvedValue(response({ total: 0, results: [], pages: 1 }));
    render(<ImaPanel />);
    await search('不存在');
    expect(await screen.findByTestId('ima-empty')).toBeInTheDocument();
  });

  it('分页：下一页请求 page=2', async () => {
    api().imaSearch.mockResolvedValue(response({ total: 20, pages: 2 }));
    render(<ImaPanel />);
    await search();
    await screen.findByTestId('ima-hit-m1');
    fireEvent.click(screen.getByTestId('ima-next-page'));
    await waitFor(() =>
      expect(api().imaSearch).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    );
  });

  it('类型与标签过滤传给后端', async () => {
    api().imaSearch.mockResolvedValue(response());
    render(<ImaPanel />);
    fireEvent.change(screen.getByTestId('ima-type-filter'), { target: { value: '7' } });
    fireEvent.change(screen.getByTestId('ima-tag-filter'), { target: { value: '八字' } });
    await search();
    await screen.findByTestId('ima-hit-m1');
    expect(api().imaSearch).toHaveBeenCalledWith(
      expect.objectContaining({ type: '7', tag: '八字' }),
    );
  });

  it('后端报错原样展示', async () => {
    api().imaSearch.mockRejectedValue(new Error('ima 检索失败且无本地缓存'));
    render(<ImaPanel />);
    await search();
    expect((await screen.findByTestId('ima-search-error')).textContent).toContain('无本地缓存');
  });
});
