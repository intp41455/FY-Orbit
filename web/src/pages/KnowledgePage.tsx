// W3 本地知识库页面：拖拽导入 → 文档列表（真实状态）→ 关键词检索（来源可溯）→ 适配器管理。
//
// 诚实性约定（交接总纲铁律 3 / FROZEN_CONTRACT §11）：
//  - 导入失败（status=failed）原样展示后端 error，绝不显示成「已导入」；
//  - 没有命中就显示「没有命中」，不塞占位结果；
//  - 适配器未接入显示「未接入」，不提供假的可用按钮。
import { useCallback, useEffect, useState } from 'react';
import { knowledgeApi } from '../api/knowledge';
import type {
  KBDocument,
  KBLimits,
  KBSearchHit,
  KBSourceStatus,
  KBSyncSummary,
} from '../api/knowledge';
import { KnowledgeDropzone } from '../components/knowledge/KnowledgeDropzone';
import { SourceCard } from '../components/knowledge/SourceCard';
import {
  MAX_FILE_BYTES,
  SUPPORTED_EXTENSIONS,
  credentialHint,
  describeDocument,
  documentStatus,
  formatBytes,
  highlightParts,
  rejectReason,
  summarizeHits,
  topScoreLabel,
} from '../components/knowledge/kbFormat';
import { errorMessage } from '../components/ui';

const DEFAULT_LIMITS: KBLimits = {
  max_bytes: MAX_FILE_BYTES,
  extensions: SUPPORTED_EXTENSIONS,
};

export function KnowledgePage() {
  const [documents, setDocuments] = useState<KBDocument[]>([]);
  const [limits, setLimits] = useState<KBLimits>(DEFAULT_LIMITS);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<KBSearchHit[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [sources, setSources] = useState<KBSourceStatus[]>([]);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [sourceNotice, setSourceNotice] = useState<string | null>(null);

  const [importing, setImporting] = useState(false);
  const [uploadLog, setUploadLog] = useState<string[]>([]);

  const refreshDocuments = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const data = await knowledgeApi.listDocuments();
      setDocuments(data.documents);
      setLimits(data.limits);
    } catch (e) {
      setListError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshSources = useCallback(async () => {
    try {
      const data = await knowledgeApi.listSources();
      setSources(data.sources);
    } catch (e) {
      setSourceNotice(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void refreshDocuments();
    void refreshSources();
  }, [refreshDocuments, refreshSources]);

  const onFiles = async (files: File[]) => {
    setImporting(true);
    const log: string[] = [];
    for (const file of files) {
      const reason = rejectReason(file.name, file.size);
      if (reason) {
        log.push(`✗ ${file.name}：${reason}`);
        continue;
      }
      try {
        const doc = await knowledgeApi.upload(file);
        log.push(
          doc.status === 'ready'
            ? `✓ ${doc.name}：已索引 ${doc.chunk_count} 个切片`
            : `✗ ${doc.name}：导入失败（${doc.error}）`,
        );
      } catch (e) {
        log.push(`✗ ${file.name}：${errorMessage(e)}`);
      }
    }
    setUploadLog(log);
    setImporting(false);
    await refreshDocuments();
  };

  const onSearch = async () => {
    const q = query.trim();
    if (!q) {
      setSearchError('请输入检索词');
      setHits([]);
      setSearched(false);
      return;
    }
    setSearching(true);
    setSearchError(null);
    try {
      const res = await knowledgeApi.search(q, 8);
      setHits(res.results);
      setSearched(true);
    } catch (e) {
      setSearchError(errorMessage(e));
      setHits([]);
      setSearched(false);
    } finally {
      setSearching(false);
    }
  };

  const onDelete = async (doc: KBDocument) => {
    try {
      const res = await knowledgeApi.remove(doc.id);
      setUploadLog([`已删除 ${doc.name}（级联清理 ${res.deleted_chunks} 个切片）`]);
    } catch (e) {
      setUploadLog([`删除失败：${errorMessage(e)}`]);
    }
    await refreshDocuments();
  };

  const withSources = async (fn: () => Promise<unknown>, ok?: (result: unknown) => string) => {
    setSourceBusy(true);
    setSourceNotice(null);
    try {
      const result = await fn();
      if (ok) setSourceNotice(ok(result));
    } catch (e) {
      setSourceNotice(errorMessage(e));
    } finally {
      await refreshSources();
      setSourceBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <h2>📚 本地知识库</h2>
        <span className="muted">文档 RAG · 数据全部留在本机 · 检索严格按账号隔离</span>
      </div>

      <KnowledgeDropzone
        onFiles={(files) => void onFiles(files)}
        busy={importing}
        hint={`支持 ${limits.extensions.join(' / ')}，单文件 ≤ ${formatBytes(limits.max_bytes)}；文件只留在本机。`}
      />
      {uploadLog.length > 0 && (
        <ul className="kb-log" data-testid="kb-upload-log">
          {uploadLog.map((line, i) => (
            <li key={`${line}-${i}`}>{line}</li>
          ))}
        </ul>
      )}

      <h3>文档（{documents.length}）</h3>
      {loading && <div className="muted">读取中…</div>}
      {listError && <div className="notice danger">{listError}</div>}
      {!loading && !listError && documents.length === 0 && (
        <div className="muted" data-testid="kb-empty">
          还没有文档。拖入 .md / .txt / .pdf / .docx 后即可检索。
        </div>
      )}
      {documents.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>状态</th>
                <th>来源</th>
                <th>切片</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => {
                const st = documentStatus(doc.status);
                return (
                  <tr key={doc.id} data-testid={`kb-doc-${doc.id}`}>
                    <td>
                      <div>{doc.name}</div>
                      <div className="muted">{describeDocument(doc)}</div>
                    </td>
                    <td>
                      <span className={`badge ${st.tone}`}>{st.label}</span>
                    </td>
                    <td>{doc.source}</td>
                    <td>{doc.chunk_count}</td>
                    <td>
                      <button
                        type="button"
                        className="small ghost"
                        aria-label={`删除 ${doc.name}`}
                        onClick={() => void onDelete(doc)}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <h3>检索</h3>
      <div className="row" style={{ gap: '0.5rem', flexWrap: 'wrap' }}>
        <input
          aria-label="知识库检索词"
          placeholder="输入关键词，例如：碰撞规则"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void onSearch();
          }}
          style={{ minWidth: '16rem' }}
        />
        <button type="button" onClick={() => void onSearch()} disabled={searching}>
          {searching ? '检索中…' : '检索'}
        </button>
      </div>
      {searchError && <div className="notice danger">{searchError}</div>}
      {searched && !searchError && (
        <p className="muted" data-testid="kb-search-summary">
          {summarizeHits(hits)}
        </p>
      )}
      {hits.length > 0 && (
        <div className="kb-results" data-testid="kb-results">
          {hits.map((hit) => (
            <div className="card kb-hit" key={hit.chunk_id} data-testid={`kb-hit-${hit.chunk_id}`}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <strong>{hit.doc_name}</strong>
                <span className="muted">{topScoreLabel(hit)}</span>
              </div>
              <p>
                {highlightParts(hit.content, hit.matched_terms).map((part, i) =>
                  part.hit ? (
                    <mark key={`h-${i}`}>{part.text}</mark>
                  ) : (
                    <span key={`t-${i}`}>{part.text}</span>
                  ),
                )}
              </p>
              <div className="muted">
                来源：{hit.source} · 文档 {hit.doc_id} · 命中词 {hit.matched_terms.join('、')}
              </div>
            </div>
          ))}
        </div>
      )}

      <h3>知识源适配器</h3>
      <p className="muted">
        未配置凭证的适配器显示「未接入」；凭证只保存在本进程内存中，重启后需重新填写
        （不入库、不落盘、不回显）。
      </p>
      {sourceNotice && <div className="notice warn">{sourceNotice}</div>}
      <div className="kb-sources">
        {sources.map((source) => (
          <SourceCard
            key={source.source_id}
            source={source}
            busy={sourceBusy}
            onConfigure={(id, values) =>
              withSources(
                () => knowledgeApi.configureSource(id, values),
                () => `${id}：凭证已保存（仅内存，重启后失效）`,
              )
            }
            onForget={(id) =>
              withSources(() => knowledgeApi.forgetSource(id), () => `${id}：凭证已清除`)
            }
            onProbe={(id) =>
              withSources(
                () => knowledgeApi.probeSource(id),
                (result) => `${id}：${(result as KBSourceStatus).detail}`,
              )
            }
            onSync={(id) =>
              withSources(
                () => knowledgeApi.syncSource(id),
                (result) => {
                  const s = result as KBSyncSummary;
                  return (
                    `${id}：拉取 ${s.collections} 个集合，导入 ${s.imported}，` +
                    `替换 ${s.replaced}，失败 ${s.failed}` +
                    (s.preview_only ? `，其中 ${s.preview_only} 条只有预览` : '')
                  );
                },
              )
            }
          />
        ))}
        {sources.length === 0 && <div className="muted">适配器清单读取中…</div>}
      </div>
      <p className="muted">
        小提示：在 Chat 调试页绑定 <code>kb.search</code> 工具后，问「用知识库查 …」即可让
        Agent 走同一套检索。
      </p>
      {sources.length > 0 && (
        <p className="muted">
          {sources.map((s) => `${s.display_name}：${credentialHint(s)}`).join(' · ')}
        </p>
      )}
    </>
  );
}