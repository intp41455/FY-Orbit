import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/knowledge', async () => {
  const actual = await vi.importActual<typeof import('../api/knowledge')>('../api/knowledge');
  return {
    ...actual,
    knowledgeApi: {
      listDocuments: vi.fn(),
      upload: vi.fn(),
      remove: vi.fn(),
      listChunks: vi.fn(),
      search: vi.fn(),
      listSources: vi.fn(),
      configureSource: vi.fn(),
      forgetSource: vi.fn(),
      probeSource: vi.fn(),
      syncSource: vi.fn(),
      imaSearch: vi.fn(),
      imaStatus: vi.fn(),
    },
  };
});

import { knowledgeApi } from '../api/knowledge';
import type { KBDocument, KBSearchHit, KBSourceStatus } from '../api/knowledge';
import { KnowledgePage } from './KnowledgePage';

const mocked = () => knowledgeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const DOC_READY: KBDocument = {
  id: 'd1',
  name: '小屋设计.md',
  source: 'local',
  external_id: '',
  size: 2048,
  status: 'ready',
  error: '',
  chunk_count: 3,
  version: 1,
  created_at: null,
};

const DOC_FAILED: KBDocument = {
  id: 'd2',
  name: '扫描件.pdf',
  source: 'local',
  external_id: '',
  size: 40960,
  status: 'failed',
  error: 'empty_content: 文档解析后没有可索引的文本（可能是扫描件 PDF 或空文档）',
  chunk_count: 0,
  version: 1,
  created_at: null,
};

const HIT: KBSearchHit = {
  chunk_id: 'c1',
  doc_id: 'd1',
  doc_name: '小屋设计.md',
  source: 'local',
  seq: 0,
  content: '家具必须遵守碰撞规则，地板贴图按房间尺寸生成。',
  content_hash: 'hash',
  score: 3.14159,
  matched_terms: ['碰撞规则'],
};

const IMA: KBSourceStatus = {
  source_id: 'ima',
  display_name: 'ima 知识库',
  available: false,
  configured: false,
  degraded: false,
  latency_ms: null,
  detail: '未接入：请填写 ima API Key 与 Base URL（仅存内存，不入库）',
  hint: '',
  credential_fields: ['api_key', 'base_url'],
  credentials_present: { api_key: false, base_url: false },
  storage: 'memory',
  persist_restart: false,
  capabilities: { searchable: true, full_text: true, incremental: false, retryable: true },
};

const BAIDU: KBSourceStatus = {
  ...IMA,
  source_id: 'baidu_pan',
  display_name: '百度网盘',
  available: false,
  detail: '未接入：百度网盘适配器 v1 仅有骨架。',
  credential_fields: ['app_key', 'app_secret', 'redirect_uri'],
  credentials_present: { app_key: false, app_secret: false, redirect_uri: false },
  capabilities: { searchable: false, full_text: true, incremental: false, retryable: true },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/knowledge']}>
      <KnowledgePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  const api = mocked();
  api.listDocuments.mockResolvedValue({
    documents: [],
    count: 0,
    limits: { max_bytes: 20 * 1024 * 1024, extensions: ['.md', '.txt', '.pdf', '.docx'] },
  });
  api.listSources.mockResolvedValue({ sources: [IMA, BAIDU], count: 2 });
  api.search.mockResolvedValue({ query: 'x', count: 0, results: [] });
  api.remove.mockResolvedValue({ id: 'd1', deleted_chunks: 3 });
});

describe('KnowledgePage · 导入', () => {
  it('空库时给出诚实空态，不塞示例文档', async () => {
    renderPage();
    expect(await screen.findByTestId('kb-empty')).toHaveTextContent('还没有文档');
  });

  it('文档列表展示真实状态，failed 必须显示后端原因', async () => {
    mocked().listDocuments.mockResolvedValue({
      documents: [DOC_READY, DOC_FAILED],
      count: 2,
      limits: { max_bytes: 1, extensions: ['.md'] },
    });
    renderPage();
    expect(await screen.findByTestId('kb-doc-d1')).toHaveTextContent('ready · 已索引');
    const failed = await screen.findByTestId('kb-doc-d2');
    expect(failed).toHaveTextContent('failed · 解析失败');
    expect(failed).toHaveTextContent('empty_content');
  });

  it('拖入文件走真实上传并回报切片数', async () => {
    const api = mocked();
    api.upload.mockResolvedValue({ ...DOC_READY, chunk_count: 7 });
    renderPage();
    const input = await screen.findByTestId('kb-file-input');
    const file = new File(['# 小屋\n\n碰撞规则'], '小屋.md', { type: 'text/markdown' });
    await fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(api.upload).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId('kb-upload-log')).toHaveTextContent('已索引 7 个切片');
  });

  it('上传失败按后端 failed 原因显示，不写成「已导入」', async () => {
    mocked().upload.mockResolvedValue(DOC_FAILED);
    renderPage();
    const input = await screen.findByTestId('kb-file-input');
    await fireEvent.change(input, {
      target: { files: [new File(['x'], '扫描件.pdf', { type: 'application/pdf' })] },
    });
    const log = await screen.findByTestId('kb-upload-log');
    expect(log).toHaveTextContent('导入失败');
    expect(log).toHaveTextContent('empty_content');
    expect(log).not.toHaveTextContent('已索引');
  });

  it('不支持的类型在前端就拦下，不发请求', async () => {
    const api = mocked();
    renderPage();
    const input = await screen.findByTestId('kb-file-input');
    await fireEvent.change(input, {
      target: { files: [new File(['x'], 'photo.png', { type: 'image/png' })] },
    });
    expect(await screen.findByTestId('kb-upload-log')).toHaveTextContent('不支持的文件类型');
    expect(api.upload).not.toHaveBeenCalled();
  });

  it('删除文档后展示级联清理的切片数', async () => {
    mocked().listDocuments.mockResolvedValue({
      documents: [DOC_READY],
      count: 1,
      limits: { max_bytes: 1, extensions: ['.md'] },
    });
    renderPage();
    await fireEvent.click(await screen.findByLabelText('删除 小屋设计.md'));
    expect(await screen.findByTestId('kb-upload-log')).toHaveTextContent('级联清理 3 个切片');
  });
});

describe('KnowledgePage · 检索', () => {
  it('命中时展示来源文档、得分与高亮', async () => {
    mocked().search.mockResolvedValue({ query: '碰撞规则', count: 1, results: [HIT] });
    renderPage();
    await fireEvent.change(await screen.findByLabelText('知识库检索词'), {
      target: { value: '碰撞规则' },
    });
    await fireEvent.click(screen.getByRole('button', { name: '检索' }));
    const results = await screen.findByTestId('kb-results');
    expect(results).toHaveTextContent('小屋设计.md');
    expect(results).toHaveTextContent('得分 3.14');
    expect(results.querySelector('mark')?.textContent).toBe('碰撞规则');
    expect(await screen.findByTestId('kb-search-summary')).toHaveTextContent('命中 1 个切片');
  });

  it('没有命中时明说没有命中，不渲染结果卡片', async () => {
    renderPage();
    await fireEvent.change(await screen.findByLabelText('知识库检索词'), {
      target: { value: '区块链' },
    });
    await fireEvent.click(screen.getByRole('button', { name: '检索' }));
    expect(await screen.findByTestId('kb-search-summary')).toHaveTextContent('没有命中');
    expect(screen.queryByTestId('kb-results')).toBeNull();
  });

  it('空检索词直接报错，不发请求', async () => {
    const api = mocked();
    renderPage();
    await fireEvent.click(await screen.findByRole('button', { name: '检索' }));
    expect(await screen.findByText('请输入检索词')).toBeInTheDocument();
    expect(api.search).not.toHaveBeenCalled();
  });
});

describe('KnowledgePage · 适配器', () => {
  it('未接入如实显示「未接入」，百度网盘不提供同步按钮', async () => {
    renderPage();
    expect(await screen.findByTestId('kb-source-detail-ima')).toHaveTextContent('未接入');
    expect(screen.getAllByText('未接入').length).toBeGreaterThanOrEqual(2);
    const baidu = screen.getByTestId('kb-source-baidu_pan');
    expect(baidu).toHaveTextContent('未接入：百度网盘适配器 v1 仅有骨架');
    expect(baidu.querySelectorAll('input')).toHaveLength(0);
  });

  it('同步按钮在未配置凭证时禁用', async () => {
    renderPage();
    const ima = await screen.findByTestId('kb-source-ima');
    const sync = [...ima.querySelectorAll('button')].find((b) => b.textContent?.includes('同步'));
    expect(sync).toBeDisabled();
  });

  it('保存凭证后刷新状态并按存储位置提示（内存）', async () => {
    const api = mocked();
    api.configureSource.mockResolvedValue({
      source_id: 'ima',
      configured: true,
      storage: 'memory',
      persist_restart: false,
    });
    api.listSources.mockResolvedValue({
      sources: [{ ...IMA, configured: true, available: true }, BAIDU],
      count: 2,
    });
    renderPage();
    await fireEvent.change(await screen.findByLabelText('ima-api_key'), {
      target: { value: 'secret-key' },
    });
    const card = screen.getByTestId('kb-source-ima');
    const save = [...card.querySelectorAll('button')].find((b) => b.textContent?.includes('保存凭证'));
    await fireEvent.click(save as HTMLButtonElement);
    await waitFor(() => expect(api.configureSource).toHaveBeenCalledWith('ima', { api_key: 'secret-key' }));
    expect(await screen.findByText(/凭证已保存（仅内存/)).toBeInTheDocument();
  });

  it('适配器接口报错时展示真实错误文本', async () => {
    mocked().listSources.mockRejectedValue(new Error('知识源清单读取失败（HTTP 503）'));
    renderPage();
    expect(await screen.findByText('知识源清单读取失败（HTTP 503）')).toBeInTheDocument();
  });
});