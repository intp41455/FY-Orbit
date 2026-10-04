import { describe, expect, it } from 'vitest';
import {
  credentialHint,
  describeDocument,
  documentStatus,
  fileExtension,
  formatBytes,
  highlightParts,
  isSupportedFile,
  rejectReason,
  sourceStateLabel,
  summarizeHits,
  topScoreLabel,
  MAX_FILE_BYTES,
} from './kbFormat';
import type { KBDocument, KBSearchHit, KBSourceStatus } from '../../api/knowledge';

const doc = (over: Partial<KBDocument> = {}): KBDocument => ({
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
  ...over,
});

const source = (over: Partial<KBSourceStatus> = {}): KBSourceStatus => ({
  source_id: 'ima',
  display_name: 'ima 知识库',
  available: false,
  configured: false,
  degraded: false,
  latency_ms: null,
  detail: '未接入：请填写 API Key',
  hint: '',
  credential_fields: ['api_key', 'base_url'],
  credentials_present: { api_key: false, base_url: false },
  storage: 'memory',
  persist_restart: false,
  capabilities: { searchable: true, full_text: true, incremental: false, retryable: true },
  ...over,
});

describe('kbFormat · 文件校验与文案', () => {
  it('识别支持的扩展名（含大小写）', () => {
    expect(fileExtension('a.PDF')).toBe('.pdf');
    expect(isSupportedFile('note.MD')).toBe(true);
    expect(isSupportedFile('photo.png')).toBe(false);
    expect(isSupportedFile('无扩展名')).toBe(false);
  });

  it('拒绝不支持类型 / 超限 / 空文件，并给出中文原因', () => {
    expect(rejectReason('photo.png', 10)).toContain('不支持的文件类型');
    expect(rejectReason('big.md', MAX_FILE_BYTES + 1)).toContain('超过');
    expect(rejectReason('empty.md', 0)).toContain('空的');
    expect(rejectReason('ok.md', 100)).toBeNull();
  });

  it('格式化字节数', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(20 * 1024 * 1024)).toBe('20.0 MB');
  });

  it('失败文档必须带原因，成功文档不带', () => {
    expect(describeDocument(doc({ status: 'failed', error: 'pdf_parse_failed: PDF 解析失败' })))
      .toContain('pdf_parse_failed');
    expect(describeDocument(doc())).not.toContain('原因：');
    expect(describeDocument(doc({ status: 'failed', error: '' }))).toContain('后端未返回原因');
  });

  it('状态 → 文案 + 色调（禁绿色）', () => {
    expect(documentStatus('ready')).toEqual({ label: 'ready · 已索引', tone: 'ok' });
    expect(documentStatus('failed').tone).toBe('danger');
    expect(documentStatus('indexing').tone).toBe('warn');
  });
});

describe('kbFormat · 检索结果', () => {
  const hit = (over: Partial<KBSearchHit> = {}): KBSearchHit => ({
    chunk_id: 'c1',
    doc_id: 'd1',
    doc_name: '小屋设计.md',
    source: 'local',
    seq: 0,
    content: '家具必须遵守碰撞规则',
    content_hash: 'h',
    score: 1.2345,
    matched_terms: ['碰撞规则'],
    ...over,
  });

  it('没有命中时明说没有命中，不编造结果', () => {
    expect(summarizeHits([])).toContain('没有命中');
  });

  it('命中摘要统计切片与文档数', () => {
    expect(summarizeHits([hit(), hit({ chunk_id: 'c2' })])).toBe('命中 2 个切片，来自 1 份文档');
    expect(summarizeHits([hit(), hit({ chunk_id: 'c2', doc_id: 'd2' })]))
      .toBe('命中 2 个切片，来自 2 份文档');
  });

  it('得分标签保留两位小数与段号', () => {
    expect(topScoreLabel(hit({ seq: 2 }))).toBe('得分 1.23 · 第 3 段');
  });

  it('高亮命中词并保持原文顺序', () => {
    const parts = highlightParts('家具必须遵守碰撞规则，地板贴图', ['碰撞规则']);
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual(['碰撞规则']);
    expect(parts.map((p) => p.text).join('')).toBe('家具必须遵守碰撞规则，地板贴图');
  });

  it('多命中词 / 无命中词 / 空内容', () => {
    expect(highlightParts('碰撞规则与探险玩法', ['碰撞', '玩法']).filter((p) => p.hit).length).toBe(2);
    expect(highlightParts('无关文本', ['不存在'])).toEqual([{ text: '无关文本', hit: false }]);
    expect(highlightParts('', ['x'])).toEqual([{ text: '', hit: false }]);
  });
});

describe('kbFormat · 适配器状态', () => {
  it('三态区分：未接入 / 已配置但探测失败 / 可用', () => {
    expect(sourceStateLabel(source()).label).toBe('未接入');
    expect(sourceStateLabel(source({ configured: true })).label).toContain('探测失败');
    expect(sourceStateLabel(source({ configured: true, available: true })).label).toBe('可用');
  });

  it('凭证提示列出缺失字段 / 提示仅存内存', () => {
    expect(credentialHint(source())).toBe('待填写：api_key、base_url');
    expect(credentialHint(source({ credentials_present: { api_key: true, base_url: true } })))
      .toContain('仅存内存');
  });
});