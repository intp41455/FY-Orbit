// W3 知识库：拖拽导入 → 文档列表（真实状态）→ 关键词检索（来源可溯）→ 适配器管理。
//
// 包 E 视觉层：/knowledge 改为**星图主视图**（用户已裁决，不新增路由），
// 原「文档源 / 适配器管理」降级为左侧子 Tab，检索结果条常驻底部。
//
// 诚实性约定（交接总纲铁律 3 / FROZEN_CONTRACT §11）：
//  - 导入失败（status=failed）原样展示后端 error，绝不显示成「已导入」；
//  - 没有命中就显示「没有命中」，不塞占位结果；
//  - 适配器未接入显示「未接入」，不提供假的可用按钮；
//  - 高级配置里没有真实后端支持的项标「待接线」并禁用（红线二）。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
import { ImaPanel } from '../components/knowledge/ImaPanel';
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
import { LineIcon } from '../components/ui/LineIcon';
import '../styles/pages/knowledge.css';
import { StarGraph } from '../components/knowledgeui/StarGraph';
import { KnowledgeNiIcon } from '../components/knowledgeui/KnowledgeNiIcon';
import {
  AdvancedConfigDrawer,
  DEFAULT_KN_CONFIG,
  type KnConfig,
} from '../components/knowledgeui/AdvancedConfigDrawer';
import { useKnowledgeGraph } from '../components/knowledgeui/useKnowledgeGraph';
import { CLUSTER_LABEL, clusterLabel, type StarNode } from '../components/knowledgeui/starLogic';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

const DEFAULT_LIMITS: KBLimits = {
  max_bytes: MAX_FILE_BYTES,
  extensions: SUPPORTED_EXTENSIONS,
};

/** 层级视图的树节点形状（由文档名的目录部分推导）。 */
interface KnTreeNode {
  path: string;
  label: string;
  children: Map<string, KnTreeNode>;
  doc: KBDocument | null;
}

/* ------------------------------------------------------------------ */
/* 页面                                                                */
/* ------------------------------------------------------------------ */

type TabId = 'star' | 'timeline' | 'hierarchy' | 'list' | 'ima' | 'sources' | 'adapters' | 'logs';
type ViewId = 'star' | 'timeline' | 'hierarchy' | 'list';

const VIEW_TABS: { id: ViewId; label: string }[] = [
  { id: 'star', label: '星图' },
  { id: 'timeline', label: '时间线' },
  { id: 'hierarchy', label: '层级' },
  { id: 'list', label: '列表' },
];

const ALL_TABS: { id: TabId; label: string; icon: 'network' | 'timeline' | 'layers' | 'list' | 'knowledge' | 'folder' | 'database' | 'history' }[] = [
  { id: 'star', label: '星图（默认）', icon: 'network' },
  { id: 'timeline', label: '时间线', icon: 'timeline' },
  { id: 'hierarchy', label: '层级', icon: 'layers' },
  { id: 'list', label: '列表', icon: 'list' },
  { id: 'ima', label: 'ima 命理库', icon: 'knowledge' },
  { id: 'sources', label: '文档源', icon: 'folder' },
  { id: 'adapters', label: '适配器', icon: 'database' },
  { id: 'logs', label: '检索日志', icon: 'history' },
];

/** 系统是否要求减弱动效（canvas 动画与 CSS 动画都要听这个）。 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const mq = matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReduced(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

export function KnowledgePage() {
  const navigate = useNavigate();
  const reducedMotion = usePrefersReducedMotion();

  /* ---------------- 原有状态（保留业务契约） ---------------- */
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

  /* ---------------- 包 E 视觉层状态 ---------------- */
  const [tab, setTab] = useState<TabId>('star');
  const [cfgOpen, setCfgOpen] = useState(false);
  const [cfg, setCfg] = useState<KnConfig>(DEFAULT_KN_CONFIG);
  const [selectedNode, setSelectedNode] = useState<StarNode | null>(null);
  const [focusToken, setFocusToken] = useState(0);
  const [treeOpen, setTreeOpen] = useState<Record<string, boolean>>({});

  const graph = useKnowledgeGraph({ enabled: tab === 'star' || tab === 'timeline' || tab === 'hierarchy' || tab === 'list' });

  /* ---------------- 数据源 ---------------- */
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

  /* ---------------- 导入 / 检索 / 删除 ---------------- */
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
    graph.reload();
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
      // 命中并入星图：命中的切片升到全亮 + 加外环，其余弱化。
      graph.applyHits(res.results.map((h) => ({ chunk_id: h.chunk_id, score: h.score })));
      setFocusToken((t) => t + 1);
    } catch (e) {
      setSearchError(errorMessage(e));
      setHits([]);
      setSearched(false);
    } finally {
      setSearching(false);
    }
  };

  /** 输入即过滤：只改本地过滤态，不打后端（最少点击守则第 2 条）。 */
  const onQueryChange = (v: string) => {
    setQuery(v);
    if (v.trim().length > 0) {
      setFocusToken((t) => t + 1);
    } else {
      graph.clearMatches();
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
    graph.reload();
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

  /* ---------------- 星图边过滤（渲染层真接线） ---------------- */
  const edges = useMemo(
    () =>
      graph.edges.filter((e) => {
        if (!cfg.showWeakEdges && e.kind === 'sequence') return false;
        if (!cfg.showSequenceEdges && e.kind === 'sequence') return false;
        if (!cfg.showCohitEdges && e.kind === 'cohit') return false;
        return true;
      }),
    [graph.edges, cfg],
  );

  const matchedCount = graph.nodes.filter((n) => n.matched).length;
  const hitCount = hits.length;

  /* ---------------- 时间线：按文档 created_at 分组（真实字段） ---------------- */
  const timeline = useMemo(() => {
    const byDate = new Map<string, KBDocument[]>();
    for (const d of documents) {
      const key = d.created_at ? new Date(d.created_at).toLocaleDateString('zh-CN') : '未回传时间';
      const arr = byDate.get(key);
      if (arr) arr.push(d);
      else byDate.set(key, [d]);
    }
    return [...byDate.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [documents]);

  /* ---------------- 层级：由文档名的目录部分推导 ---------------- */
  const tree = useMemo<KnTreeNode>(() => {
    const root: KnTreeNode = { path: '', label: '', children: new Map(), doc: null };
    for (const d of documents) {
      const parts = d.name.split('/');
      let cur = root;
      parts.forEach((part, i) => {
        const isLeaf = i === parts.length - 1;
        const p = cur.path ? `${cur.path}/${part}` : part;
        let next = cur.children.get(part);
        if (!next) {
          next = { path: p, label: part, children: new Map(), doc: null };
          cur.children.set(part, next);
        }
        if (isLeaf) next.doc = d;
        cur = next;
      });
    }
    return root;
  }, [documents]);

  /** 节点 → 所属文档（用于节点卡的「打开原文件」与跳转）。 */
  const nodeDoc = useCallback(
    (n: StarNode): KBDocument | null =>
      graph.nodes.length > 0 ? documents.find((d) => d.id === n.docId) ?? null : null,
    [documents, graph.nodes.length],
  );

  /**
   * 「基于此内容对话」：跳 `/chat` 并带上上下文。
   *
   * ⚠ 跨包依赖（已列入交付报告越界发现）：
   *   ChatPage 归包 C，本包无权修改，且它当前**不读** location.search。
   *   因此这里把上下文放进 URL 查询串（可分享、可回跳、可被包 C 后续接线消费），
   *   同时在节点卡上写明「对话页接入后会自动带入」——
   *   **不假装已经自动带入**（红线二）。
   */
  const chatAbout = (n: StarNode) => {
    const params = new URLSearchParams();
    params.set('ctx_doc', n.docName);
    params.set('ctx_seq', String(n.seq));
    params.set('ctx_text', n.excerpt.slice(0, 600));
    navigate(`/chat?${params.toString()}`);
  };

  const renderTree = (node: KnTreeNode, depth: number) => {
    return (
      <div className="kn-tree-node" key={node.path || '__root'}>
        {node.label && (
          <div
            className="kn-tree-row"
            data-depth={Math.min(depth, 2)}
            role="button"
            tabIndex={0}
            aria-expanded={treeOpen[node.path] !== false && node.children.size > 0}
            onClick={() => setTreeOpen((t) => ({ ...t, [node.path]: t[node.path] === false }))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setTreeOpen((t) => ({ ...t, [node.path]: t[node.path] === false }));
              }
            }}
          >
            {node.children.size > 0 ? (
              <LineIcon name={treeOpen[node.path] === false ? 'chevronRight' : 'chevronDown'} size={16} />
            ) : (
              <LineIcon name="file" size={16} />
            )}
            <span>{node.label}</span>
            {node.doc && (
              <span className={`badge ${documentStatus(node.doc.status).tone}`}>
                {documentStatus(node.doc.status).label}
              </span>
            )}
          </div>
        )}
        {node.label && treeOpen[node.path] === false ? null : (
          <div className="kn-tree-kids">
            {[...node.children.values()].map((c) => renderTree(c, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <BaseBound surface="knowledge">
    <div className="kn-root">
      <div className="page-head">
        <h2>本地知识库</h2>
        <span className="muted">文档 RAG · 数据全部留在本机 · 检索严格按账号隔离</span>
      </div>

      {/* ============ 顶栏：数据源 / 搜索 / 视图切换 / 高级配置 ============ */}
      <div className="kn-topbar">
        {/* 数据源：芯片来自后端真实 source 清单 + 本地文件入口 */}
        <div className="ui-row" style={{ gap: 'var(--ui-s-2)', flexWrap: 'wrap' }} data-testid="kn-source-chips">
          <span className="cabin-ni-panel-hd" style={{ color: 'var(--ui-ink-3)' }}>
            <KnowledgeNiIcon name="cube" size={16} />
            数据源
          </span>
          <button
            type="button"
            className="ui-chip"
            aria-pressed={tab === 'sources'}
            data-testid="kn-source-local"
            onClick={() => setTab('sources')}
          >
            <LineIcon name="folder" size={16} />
            本地目录
          </button>
          {sources.map((s) => (
            <button
              key={s.source_id}
              type="button"
              className="ui-chip"
              aria-pressed={tab === 'adapters'}
              data-testid={`kn-source-${s.source_id}`}
              onClick={() => setTab('adapters')}
              title={s.detail}
            >
              <LineIcon name="database" size={16} />
              {s.display_name}
            </button>
          ))}
          <button
            type="button"
            className="ui-btn"
            data-testid="kn-import-open"
            onClick={() => setTab('sources')}
          >
            <LineIcon name="upload" size={16} />
            导入
          </button>
        </div>

        {/* 搜索：输入即过滤 + 回车/按钮检索（最少点击守则第 2 条） */}
        <div className="kn-search">
          <LineIcon name="search" size={18} />
          <input
            className="ui-input"
            aria-label="知识库检索词"
            placeholder="输入关键词，例如：碰撞规则…"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void onSearch();
            }}
          />
        </div>
        <button type="button" className="ui-btn ui-btn--primary" onClick={() => void onSearch()} disabled={searching}>
          <LineIcon name="search" size={16} />
          {searching ? '检索中…' : '检索'}
        </button>

        <span className="kn-topbar-spacer" />

        {/* 视图切换常驻（最少点击守则第 4 条） */}
        <div className="ui-tabs" role="group" aria-label="视图切换">
          {VIEW_TABS.map((v) => (
            <button
              key={v.id}
              type="button"
              className="ui-tab"
              aria-pressed={tab === v.id}
              data-testid={`kn-view-${v.id}`}
              onClick={() => setTab(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>

        {/* ⚙ 高级配置常驻右上角（最少点击守则第 5 条） */}
        <button
          type="button"
          className="ui-btn ui-btn--icon"
          aria-label="高级配置"
          title="高级配置（切片 / 向量 / 关联 / 渲染）"
          aria-expanded={cfgOpen}
          data-testid="kn-cfg-open"
          onClick={() => setCfgOpen(true)}
        >
          <LineIcon name="settings" size={20} />
        </button>
      </div>

      {searchError && (
        <div className="notice danger" role="alert">
          <LineIcon name="alert" size={16} /> {searchError}
        </div>
      )}

      {/* 适配器/清单的提示放全局：子 Tab 切走后用户也必须看得见错误 */}
      {sourceNotice && (
        <div className="notice warn" role="status" data-testid="kn-source-notice">
          <LineIcon name="info" size={16} /> {sourceNotice}
        </div>
      )}

      <div className="kn-body">
        {/* ============ 左侧竖排子 Tab（不许消失，窄屏收成横条） ============ */}
        <nav className="kn-subtabs" role="tablist" aria-label="知识库分区">
          {ALL_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`kn-tabbtn-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`kn-panel-${t.id}`}
              className="kn-subtab"
              data-testid={`kn-tab-${t.id}`}
              onClick={() => setTab(t.id)}
            >
              <LineIcon name={t.icon} size={18} />
              {t.label}
            </button>
          ))}
        </nav>

        <div className="kn-main">
          {/* ============ 星图（主视图） ============ */}
          <div
            role="tabpanel"
            id="kn-panel-star"
            aria-labelledby="kn-tabbtn-star"
            hidden={tab !== 'star'}
          >
            {/* 图例：簇色 + 线型语义（颜色不是唯一通道） */}
              <div className="kn-legend" data-testid="kn-legend">
                {CLUSTER_LABEL.map((label, i) => (
                  <span key={label}>
                    <i
                      style={{
                        background: ['var(--ui-sky-500)', 'var(--ui-teal-300)', 'var(--ui-violet-400)'][i],
                      }}
                      aria-hidden="true"
                    />
                    {label}
                  </span>
                ))}
                <span>
                  <i className="kn-legend-line" style={{ borderTopColor: 'var(--ui-line-strong)' }} aria-hidden="true" />
                  顺序（同文档相邻段）
                </span>
                <span>
                  <i className="kn-legend-line" style={{ borderTopColor: 'var(--ui-sky-600)' }} aria-hidden="true" />
                  共同命中（同次检索）
                </span>
                <span>
                  <LineIcon name="info" size={14} />
                  节点大小/亮度 = 权重（被同次检索共同命中的次数）
                </span>
                <span className="kn-count">
                  命中 {matchedCount} / 共 {graph.totalNodes}
                  {graph.truncated ? `（已显示 ${graph.nodes.length}，超出 ${graph.totalNodes - graph.nodes.length} 个受上限保护）` : ''}
                </span>
              </div>

              <StarGraph
                nodes={graph.nodes}
                edges={edges}
                selectedId={selectedNode?.id ?? null}
                filter={query}
                focusFirstMatchToken={focusToken}
                reducedMotion={reducedMotion}
                onSelect={setSelectedNode}
                cardActions={
                  selectedNode ? (
                    <>
                      <button
                        type="button"
                        className="ui-btn ui-btn--sm"
                        data-testid="kn-node-open"
                        onClick={() => {
                          const d = nodeDoc(selectedNode);
                          if (d) setTab('sources');
                        }}
                        title={
                          nodeDoc(selectedNode)
                            ? '查看该切片所属文档的源文件状态'
                            : '后端未提供按切片名取回原文件正文的端点'
                        }
                      >
                        <LineIcon name="file" size={16} />
                        打开原文件
                      </button>
                      <button
                        type="button"
                        className="ui-btn ui-btn--sm ui-btn--primary"
                        data-testid="kn-node-chat"
                        onClick={() => chatAbout(selectedNode)}
                      >
                        <LineIcon name="chat" size={16} />
                        基于此内容对话
                      </button>
                    </>
                  ) : null
                }
              />

              {/* 诚实标注：图的边到底是什么 */}
              <p className="ui-hint" data-testid="kn-graph-provenance">
                <LineIcon name="info" size={14} />
                节点 = 真实切片（<code>GET /api/kb/documents/*/chunks</code>）；权重 = 被同次检索共同命中的次数
                （后端未提供中心度接口）。连线只含两类：同文档顺序（虚线）与同次检索共同命中（实线）。
                语义相似度 / 双向链接后端无端点，已在高级配置标「待接线」并禁用 —— 不画不存在的关联。
              </p>

              {graph.chunkErrors.length > 0 && (
                <div className="notice warn" role="status" data-testid="kn-chunk-errors">
                  <LineIcon name="alert" size={16} />
                  以下文档的切片读取失败，星图未包含它们：
                  {graph.chunkErrors.map((e) => `${e.docName}（${e.message}）`).join('；')}
                </div>
              )}

              {graph.loading && (
                <p className="muted" data-testid="kn-graph-loading">
                  正在从本机读取切片…
                </p>
              )}

              {graph.nodes.length === 0 && !graph.loading && (
                <div className="kn-stage" style={{ height: 180 }}>
                  <div className="kn-stage-note">
                    <strong>还没有可成图的切片</strong>
                    <p>
                      导入 .md / .txt / .pdf / .docx 后，切片会立即出现在星图里。
                      也可以先去「文档源」标签页拖入文件。
                    </p>
                  </div>
                </div>
              )}
          </div>

          {/* ============ 时间线 ============ */}
          <div
            role="tabpanel"
            id="kn-panel-timeline"
            aria-labelledby="kn-tabbtn-timeline"
            hidden={tab !== 'timeline'}
          >
            <div className="kn-timeline" data-testid="kn-timeline">
              {timeline.length === 0 && (
                <p className="muted">还没有文档时间线。</p>
              )}
              {timeline.map(([date, docs]) => (
                <div className="kn-tl-col" key={date}>
                  <div className="kn-tl-date">
                    <LineIcon name="history" size={14} /> {date}
                  </div>
                  {docs.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      className="kn-tl-item"
                      data-testid={`kn-tl-${d.id}`}
                      onClick={() => setTab('sources')}
                    >
                      <span className="kn-tl-item-name">{d.name}</span>
                      <span className="kn-tl-item-meta">
                        {d.chunk_count} 切片 · {documentStatus(d.status).label}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {/* ============ 层级 ============ */}
          <div
            role="tabpanel"
            id="kn-panel-hierarchy"
            aria-labelledby="kn-tabbtn-hierarchy"
            hidden={tab !== 'hierarchy'}
          >
            <div className="kn-tree" data-testid="kn-hierarchy">
              {documents.length === 0 ? (
                <p className="muted">还没有文档层级。</p>
              ) : (
                renderTree(tree, 0)
              )}
              <p className="ui-hint">
                层级由文档名的目录部分推导；此视图的连线恒为虚线（同文档从属）。
              </p>
            </div>
          </div>

          {/* ============ 列表（图形视图的等价兜底） ============ */}
          <div
            role="tabpanel"
            id="kn-panel-list"
            aria-labelledby="kn-tabbtn-list"
            hidden={tab !== 'list'}
          >
            <div className="kn-list" data-testid="kn-node-list">
              {graph.nodes.length === 0 && <p className="muted">还没有切片。</p>}
              {graph.nodes.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  className="kn-list-card"
                  onClick={() => {
                    setSelectedNode(n);
                    setTab('star');
                  }}
                  data-testid={`kn-list-${n.id}`}
                  title={n.excerpt}
                >
                  <span className="kn-list-card-title">
                    <LineIcon name="file" size={16} />
                    <span>{n.docName}</span>
                  </span>
                  <span className="kn-list-card-meta">
                    第 {n.seq + 1} 段 · {n.chars} 字 · {clusterLabel(n.cluster)} · 权重 {n.weight}
                    {n.score !== null && ` · 得分 ${n.score.toFixed(2)}`}
                  </span>
                  <span className="kn-list-card-body">{n.excerpt || '（该切片正文为空）'}</span>
                  <span className="kn-hover-act">
                    <span className="ui-btn ui-btn--sm ui-btn--primary">在星图查看</span>
                  </span>
                </button>
              ))}
              {graph.truncated && (
                <p className="ui-hint" style={{ gridColumn: '1 / -1' }}>
                  节点上限保护：共 {graph.totalNodes} 个切片，本页显示前 {graph.nodes.length} 个。
                  其余切片仍可在「列表」检索结果与「文档源」里找到。
                </p>
              )}
            </div>
          </div>

          {/* ============ 文档源（导入 + 文档清单） ============ */}
          <div
            role="tabpanel"
            id="kn-panel-sources"
            aria-labelledby="kn-tabbtn-sources"
            hidden={tab !== 'sources'}
          >
            <>
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
                <div className="card ui-panel ui-panel--pad">
                  <table className="table">
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
                            <td data-label="名称">
                              <div>{doc.name}</div>
                              <div className="muted">{describeDocument(doc)}</div>
                            </td>
                            <td data-label="状态">
                              {/* 状态文字 + 徽标：颜色不是唯一通道 */}
                              <span
                                className={`ui-badge ${
                                  st.tone === 'ok'
                                    ? 'ui-badge--complete'
                                    : st.tone === 'warn'
                                      ? 'ui-badge--waiting'
                                      : 'ui-badge--failed'
                                }`}
                              >
                                {st.label}
                              </span>
                            </td>
                            <td data-label="来源">{doc.source}</td>
                            <td data-label="切片">{doc.chunk_count}</td>
                            <td data-label="操作">
                              <button
                                type="button"
                                className="ui-btn ui-btn--sm"
                                aria-label={`删除 ${doc.name}`}
                                data-testid={`kb-delete-${doc.id}`}
                                onClick={() => void onDelete(doc)}
                              >
                                <LineIcon name="trash" size={16} />
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
            </>
          </div>

          {/* ============ 适配器 ============ */}
          <div
            role="tabpanel"
            id="kn-panel-adapters"
            aria-labelledby="kn-tabbtn-adapters"
            hidden={tab !== 'adapters'}
          >
            <>
              <p className="muted">
                未配置凭证的适配器显示「未接入」；凭证经加密存储（不回显明文）；ima 的 Key 也可在「设置」页填写。
              </p>
              <div className="kb-sources">
                {sources.map((source) => (
                  <SourceCard
                    key={source.source_id}
                    source={source}
                    busy={sourceBusy}
                    onConfigure={(id, values) =>
                      withSources(
                        () => knowledgeApi.configureSource(id, values),
                        (result) =>
                          (result as { persist_restart?: boolean }).persist_restart
                            ? `${id}：凭证已保存（加密存储，重启后仍有效）`
                            : `${id}：凭证已保存（仅内存，重启后失效）`,
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
          </div>

          {/* ============ ima 命理库（B1） ============ */}
          <div
            role="tabpanel"
            id="kn-panel-ima"
            aria-labelledby="kn-tabbtn-ima"
            hidden={tab !== 'ima'}
          >
            <ImaPanel />
          </div>

          {/* ============ 检索日志 ============ */}
          <div
            role="tabpanel"
            id="kn-panel-logs"
            aria-labelledby="kn-tabbtn-logs"
            hidden={tab !== 'logs'}
          >
            <>
              <h3>检索</h3>
              <p className="muted">
                检索结果条在所有视图下方常驻，这里是历史视图，便于回看同一次命中的来源与得分。
              </p>
              {!searched && <p className="muted" data-testid="kn-log-empty">还没有发起过检索。</p>}
              {searched && searchError && <p className="muted">上一次检索失败：{searchError}</p>}
            </>
          </div>

          {/* ============ 检索结果条：所有视图常驻（保证「命中 N」永远看得到） ============ */}
          {searched && !searchError && (
            <p className="muted" data-testid="kb-search-summary">
              {summarizeHits(hits)}
            </p>
          )}
          {hits.length > 0 && (
            <div className="kb-results" data-testid="kb-results">
              {hits.map((hit) => (
                <div className="card kb-hit ui-panel ui-panel--pad" key={hit.chunk_id} data-testid={`kb-hit-${hit.chunk_id}`}>
                  <div className="row" style={{ justifyContent: 'space-between', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
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
        </div>
      </div>

      <AdvancedConfigDrawer open={cfgOpen} onClose={() => setCfgOpen(false)} config={cfg} onChange={setCfg} />

      {/* 命中计数给读屏用户一份等价通道 */}
      <p className="cabin-ni-sr-only" role="status" data-testid="kn-live-count">
        {searched
          ? `命中 ${hitCount} / 共 ${graph.totalNodes} 个切片`
          : `共 ${graph.totalNodes} 个切片`}
      </p>
    </div>
    </BaseBound>
  );
}