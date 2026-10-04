// Typed client for the W3 本地知识库 API (mirrors api/routes/knowledge.py).
//
// Honesty contract (FROZEN_CONTRACT §11 / 交接总纲铁律 3):
//  - 上传失败由后端返回 {status:'failed', error}，页面必须原样展示 error，不得隐藏；
//  - 适配器未接入时后端返回 available:false + detail，页面显示「未接入」，
//    绝不用空列表冒充「已接入但没有内容」；
//  - 上传走裸字节（Content-Type: application/octet-stream）+ 文件名放 query，
//    因为后端刻意没有引入 python-multipart；会话 Cookie 同源自动带上，CSRF 头必发。
import { buildApiUrl, getCsrfToken } from './client';

const BASE = '/api/kb';

export interface KBDocument {
  id: string;
  name: string;
  source: string;
  external_id: string;
  size: number;
  status: 'indexing' | 'ready' | 'failed';
  /** 失败原因（后端填，前端必须展示）；成功时为空串。 */
  error: string;
  chunk_count: number;
  version: number;
  created_at: string | null;
}

export interface KBLimits {
  max_bytes: number;
  extensions: string[];
}

export interface KBDocumentListResponse {
  documents: KBDocument[];
  count: number;
  limits: KBLimits;
}

export interface KBChunk {
  id: string;
  seq: number;
  content: string;
  content_hash: string;
}

export interface KBSearchHit {
  chunk_id: string;
  doc_id: string;
  doc_name: string;
  source: string;
  seq: number;
  content: string;
  content_hash: string;
  score: number;
  matched_terms: string[];
}

export interface KBSearchResponse {
  query: string;
  count: number;
  results: KBSearchHit[];
}

export interface KBSourceStatus {
  source_id: string;
  display_name: string;
  available: boolean;
  configured: boolean;
  degraded: boolean;
  latency_ms: number | null;
  detail: string;
  hint: string;
  credential_fields: string[];
  credentials_present: Record<string, boolean>;
  storage: string;
  persist_restart: boolean;
  capabilities: {
    searchable: boolean;
    full_text: boolean;
    incremental: boolean;
    retryable: boolean;
  };
  /**
   * 验收 F2 桥接：来源标注。原生源无此字段；hub 源为 `hub:<connection_id>`。
   * 后端另外会带 connection_id / origin / builtin_source_id / health 等桥接专有字段。
   */
  source?: string;
  origin?: 'connection' | 'shared_store' | 'none' | 'unmapped' | 'error';
  connection_id?: string;
  connection_state?: string;
  connection_state_text?: string;
  builtin_source_id?: string;
  health?: { ok: boolean | null; checked_at: string | null; latency_ms: number | null; detail: string };
}

/** 该源是否由超级中台的 knowledge_source 连接派生（W6 验收 F2）。 */
export function isHubSource(source: { source_id: string }): boolean {
  return source.source_id.startsWith('hub:');
}

export interface KBSyncSummary {
  source_id: string;
  collections: number;
  imported: number;
  replaced: number;
  failed: number;
  preview_only: number;
  errors: { name: string; error: string }[];
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!res.ok) {
    const envelope = parsed as { error?: { code?: string; message?: string } } | null;
    const err = new Error(envelope?.error?.message ?? `请求失败（HTTP ${res.status}）`) as Error & {
      code?: string;
      status?: number;
    };
    err.code = envelope?.error?.code;
    err.status = res.status;
    throw err;
  }
  return parsed as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(buildApiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<T>(res);
}

export const knowledgeApi = {
  listDocuments: () =>
    fetch(buildApiUrl(`${BASE}/documents`), { credentials: 'same-origin' }).then((r) =>
      jsonOrThrow<KBDocumentListResponse>(r),
    ),

  /** Raw-bytes upload: 后端按 query 里的 name 判定扩展名。 */
  upload: (file: File) => {
    const url = buildApiUrl(`${BASE}/documents`, { name: file.name });
    const headers: Record<string, string> = {
      'Content-Type': 'application/octet-stream',
    };
    const csrf = getCsrfToken();
    if (csrf) headers['X-CSRF-Token'] = csrf;
    return fetch(url, { method: 'POST', headers, credentials: 'same-origin', body: file })
      .then((r) => jsonOrThrow<{ document: KBDocument }>(r))
      .then((r) => r.document);
  },

  remove: (docId: string) =>
    fetch(buildApiUrl(`${BASE}/documents/${encodeURIComponent(docId)}`), {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: csrfHeaders(),
    }).then((r) => jsonOrThrow<{ id: string; deleted_chunks: number }>(r)),

  listChunks: (docId: string) =>
    fetch(buildApiUrl(`${BASE}/documents/${encodeURIComponent(docId)}/chunks`), {
      credentials: 'same-origin',
    }).then((r) => jsonOrThrow<{ doc_id: string; chunks: KBChunk[]; count: number }>(r)),

  search: (query: string, topK = 8) =>
    postJson<KBSearchResponse>(`${BASE}/search`, { query, top_k: topK }),

  listSources: () =>
    fetch(buildApiUrl(`${BASE}/sources`), { credentials: 'same-origin' }).then((r) =>
      jsonOrThrow<{ sources: KBSourceStatus[]; count: number }>(r),
    ),

  configureSource: (sourceId: string, values: Record<string, string>) =>
    postJson<{ source_id: string; configured: boolean; storage: string }>(
      `${BASE}/sources/${encodeURIComponent(sourceId)}/configure`,
      values,
    ),

  forgetSource: (sourceId: string) =>
    fetch(buildApiUrl(`${BASE}/sources/${encodeURIComponent(sourceId)}`), {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: csrfHeaders(),
    }).then((r) => jsonOrThrow<{ source_id: string; forgotten: boolean }>(r)),

  probeSource: (sourceId: string) =>
    postJson<KBSourceStatus>(`${BASE}/sources/${encodeURIComponent(sourceId)}/probe`, {}),

  syncSource: (sourceId: string) =>
    postJson<KBSyncSummary>(`${BASE}/sources/${encodeURIComponent(sourceId)}/sync`, {}),
};

function csrfHeaders(): Record<string, string> {
  const csrf = getCsrfToken();
  return csrf ? { 'X-CSRF-Token': csrf } : {};
}