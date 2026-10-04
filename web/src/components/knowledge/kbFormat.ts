// W3 知识库 · 纯逻辑层（可单测，无 React / 无网络）。
// 页面只做渲染与编排，所有判定都在这里，方便覆盖边界（尤其是诚实性文案）。

import type { KBDocument, KBSearchHit, KBSourceStatus } from '../../api/knowledge';

export const SUPPORTED_EXTENSIONS = ['.md', '.markdown', '.txt', '.pdf', '.docx'];
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export function fileExtension(name: string): string {
  const idx = name.lastIndexOf('.');
  return idx >= 0 ? name.slice(idx).toLowerCase() : '';
}

export function isSupportedFile(name: string): boolean {
  return SUPPORTED_EXTENSIONS.includes(fileExtension(name));
}

/** 拖拽/选择前的本地预检：返回 null 表示可上传，否则返回人类可读原因。 */
export function rejectReason(name: string, size: number): string | null {
  if (!isSupportedFile(name)) {
    return `不支持的文件类型 ${fileExtension(name) || '(无扩展名)'}；支持 ${SUPPORTED_EXTENSIONS.join('、')}`;
  }
  if (size > MAX_FILE_BYTES) {
    return `文件超过 ${formatBytes(MAX_FILE_BYTES)} 上限（实际 ${formatBytes(size)}）`;
  }
  if (size === 0) {
    return '文件是空的，没有可索引的文本';
  }
  return null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type DocumentStatusTone = 'ok' | 'warn' | 'danger';

/** 状态 → 中文文案 + 徽章色调。禁绿色：ready 用冰蓝 ok，indexing 用琥珀 warn。 */
export function documentStatus(status: KBDocument['status']): {
  label: string;
  tone: DocumentStatusTone;
} {
  switch (status) {
    case 'ready':
      return { label: 'ready · 已索引', tone: 'ok' };
    case 'indexing':
      return { label: 'indexing · 索引中', tone: 'warn' };
    case 'failed':
      return { label: 'failed · 解析失败', tone: 'danger' };
    default:
      return { label: String(status), tone: 'warn' };
  }
}

export function describeDocument(doc: KBDocument): string {
  const base = `${doc.name} · ${formatBytes(doc.size)} · ${doc.chunk_count} 个切片`;
  if (doc.status === 'failed') {
    // 失败必须带原因，不允许只显示「导入失败」。
    return `${base} · 原因：${doc.error || '后端未返回原因（异常）'}`;
  }
  return base;
}

export interface HighlightPart {
  text: string;
  hit: boolean;
}

/** 把命中词在切片正文中标出来（大小写不敏感、按首次出现顺序切分）。 */
export function highlightParts(content: string, terms: string[]): HighlightPart[] {
  const clean = [...new Set(terms.map((t) => t.trim().toLowerCase()).filter(Boolean))];
  if (!clean.length || !content) return [{ text: content, hit: false }];
  const lower = content.toLowerCase();
  const marks: { start: number; end: number }[] = [];
  for (const term of clean) {
    let from = 0;
    for (;;) {
      const at = lower.indexOf(term, from);
      if (at < 0) break;
      marks.push({ start: at, end: at + term.length });
      from = at + term.length;
    }
  }
  if (!marks.length) return [{ text: content, hit: false }];
  marks.sort((a, b) => a.start - b.start || b.end - a.end);
  const merged: { start: number; end: number }[] = [];
  for (const mark of marks) {
    const last = merged[merged.length - 1];
    if (last && mark.start <= last.end) {
      last.end = Math.max(last.end, mark.end);
    } else {
      merged.push({ ...mark });
    }
  }
  const parts: HighlightPart[] = [];
  let cursor = 0;
  for (const mark of merged) {
    if (mark.start > cursor) parts.push({ text: content.slice(cursor, mark.start), hit: false });
    parts.push({ text: content.slice(mark.start, mark.end), hit: true });
    cursor = mark.end;
  }
  if (cursor < content.length) parts.push({ text: content.slice(cursor), hit: false });
  return parts;
}

/** 检索结果摘要：真实条数 + 命中文档数；没有命中就明说没有。 */
export function summarizeHits(hits: KBSearchHit[]): string {
  if (!hits.length) return '没有命中任何切片（未返回任何占位内容）';
  const docs = new Set(hits.map((h) => h.doc_id));
  return `命中 ${hits.length} 个切片，来自 ${docs.size} 份文档`;
}

export function topScoreLabel(hit: KBSearchHit): string {
  return `得分 ${hit.score.toFixed(2)} · 第 ${hit.seq + 1} 段`;
}

/** 适配器卡片文案：未接入 / 已接入未验证 / 可用 —— 三态必须区分。 */
export function sourceStateLabel(source: KBSourceStatus): {
  label: string;
  tone: DocumentStatusTone;
} {
  if (!source.configured) return { label: '未接入', tone: 'warn' };
  if (!source.available) return { label: '已配置 · 探测失败', tone: 'danger' };
  return { label: '可用', tone: 'ok' };
}

export function credentialHint(source: KBSourceStatus): string {
  const missing = source.credential_fields.filter((f) => !source.credentials_present[f]);
  if (missing.length) return `待填写：${missing.join('、')}`;
  return '凭证已填写（仅存内存，重启后需重新填写）';
}