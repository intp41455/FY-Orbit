// B1 · ima 命理知识库面板（/knowledge 页「ima 命理库」子 Tab）。
//
// 派单书 B1/B2/B3 的前端落点：
//  - B1：从产品前端发起检索 → `/api/knowledge/ima/search`（MCP 优先 / REST 兜底）
//    → 展示结果；支持分页 + 按类型/标签过滤；
//  - B2：每条命中展示标题 / 摘要 / 来源标签，并附 `src:` 出处回溯（media_id →
//    「查看原文」就地展开全文；远端有 url 时才给外链，不构造不存在的跳转）；
//  - B3：断网时后端走本地缓存，前端以 warn 提示「本次为离线缓存结果」并给出
//    缓存时间——不把缓存冒充实时结果。
//
// 诚实性约定（总纲铁律 3）：未接入 / 未配置时显示设置页指引；没有命中就显示
// 「没有命中」；preview_only 条目如实标注「仅预览」；错误原样展示 message。
import { useCallback, useEffect, useState } from 'react';
import { knowledgeApi } from '../../api/knowledge';
import type { ImaChannelStatus, ImaSearchHit, ImaSearchResponse } from '../../api/knowledge';
import { errorMessage } from '../ui';
import { LineIcon } from '../ui/LineIcon';
import { useBase } from '../../hooks/useAutosave';

const PAGE_SIZES = [5, 10, 20];

/** 类型下拉的常用值（实测 media_type：7=md）；也允许手填其它值。 */
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部类型' },
  { value: '7', label: '7 · Markdown' },
  { value: '1', label: '1 · 文档' },
  { value: '9', label: '9 · 其它' },
];

export function ImaPanel() {
  // A-基座质保-01：可编辑组件须逐文件声明基座（b1-ima 合入晚于热保存包，此处补接线）。
  useBase({ surface: 'web/src/components/knowledge/ImaPanel' });
  const [status, setStatus] = useState<ImaChannelStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [pageSize, setPageSize] = useState(10);

  const [result, setResult] = useState<ImaSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [openOriginal, setOpenOriginal] = useState<Record<string, boolean>>({});

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await knowledgeApi.imaStatus());
      setStatusError(null);
    } catch (e) {
      setStatusError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const runSearch = useCallback(
    async (page: number) => {
      const q = query.trim();
      if (!q) {
        setSearchError('请输入检索词');
        setResult(null);
        setSearched(false);
        return;
      }
      setSearching(true);
      setSearchError(null);
      try {
        const res = await knowledgeApi.imaSearch({
          query: q,
          page,
          page_size: pageSize,
          type: typeFilter || undefined,
          tag: tagFilter.trim() || undefined,
        });
        setResult(res);
        setSearched(true);
        setOpenOriginal({});
      } catch (e) {
        setSearchError(errorMessage(e));
        setResult(null);
        setSearched(false);
      } finally {
        setSearching(false);
      }
    },
    [query, pageSize, typeFilter, tagFilter],
  );

  const channelLabel = useCallback((channel: string): string => {
    const base = channel.replace('+cache', '');
    const name = base === 'mcp' ? 'MCP' : base === 'rest' ? 'REST 兜底' : base || '未走通道';
    return channel.endsWith('+cache') ? `${name} + 本地缓存` : name;
  }, []);

  return (
    <div className="ima-panel" data-testid="ima-panel">
      {/* 来源标注（B2 · 落地即如实标注） */}
      <p className="ui-hint" data-testid="ima-attribution">
        <LineIcon name="info" size={14} />
        来源：陛下的 ima 公共知识库（库 {status?.kb_id ?? '7509748362520236'}，资料全部由主理人本人上传）。
        检索经 ima 通道实时进行；断网时自动展示本地缓存并标注缓存时间。
      </p>

      {/* 通道状态：如实报告 MCP / REST 各自可用性，不假绿灯 */}
      {statusError && (
        <div className="notice danger" role="alert" data-testid="ima-status-error">
          <LineIcon name="alert" size={16} /> 通道状态读取失败：{statusError}
        </div>
      )}
      {status && (
        <div
          className={status.configured ? 'muted' : 'notice warn'}
          role={status.configured ? undefined : 'status'}
          data-testid="ima-status"
        >
          {status.configured ? (
            <span>
              <LineIcon name="check" size={14} /> 通道就绪：
              MCP {status.channels.mcp.configured ? (status.channels.mcp.available === false ? '已配置但探活失败' : '已配置') : '未配置'}
              {' · '}REST 兜底 {status.channels.rest.configured ? '已配置' : '未配置'}
              {status.channels.mcp.error ? `（${status.channels.mcp.error}）` : ''}
              {status.detail ? ` · ${status.detail}` : ''}
            </span>
          ) : (
            <span>
              <LineIcon name="key" size={16} /> ima 未接入：请到「设置」页「ima 知识库」卡片填写
              App ID / API Key / Secret Key；或在 FY_MCP_SERVERS 配置 ima MCP 服务器。
              {status.detail ? `（${status.detail}）` : ''}
            </span>
          )}
        </div>
      )}

      {/* 检索行：检索词 + 类型/标签过滤 + 每页条数 */}
      <div className="ui-row" style={{ gap: 'var(--ui-s-2)', flexWrap: 'wrap', marginTop: 'var(--ui-s-2)' }}>
        <div className="kn-search" style={{ flex: '1 1 16rem' }}>
          <LineIcon name="search" size={18} />
          <input
            className="ui-input"
            aria-label="ima 检索词"
            placeholder="检索陛下的命理库，例如：八字 / 紫微 / 日主强弱…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runSearch(1);
            }}
          />
        </div>
        <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          类型
          <select
            className="ui-input"
            aria-label="ima 类型过滤"
            data-testid="ima-type-filter"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            {TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          标签
          <input
            className="ui-input"
            aria-label="ima 标签过滤"
            data-testid="ima-tag-filter"
            style={{ width: '7rem' }}
            placeholder="如：八字"
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
          />
        </label>
        <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          每页
          <select
            className="ui-input"
            aria-label="ima 每页条数"
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value))}
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="ui-btn ui-btn--primary"
          data-testid="ima-search-btn"
          disabled={searching}
          onClick={() => void runSearch(1)}
        >
          <LineIcon name="search" size={16} />
          {searching ? '检索中…' : '检索 ima 库'}
        </button>
      </div>

      {searchError && (
        <div className="notice danger" role="alert" data-testid="ima-search-error" style={{ marginTop: 'var(--ui-s-2)' }}>
          <LineIcon name="alert" size={16} /> {searchError}
        </div>
      )}

      {/* 离线缓存横幅（B3 验收 3）：不许把缓存冒充实时 */}
      {result?.cached && (
        <div className="notice warn" role="status" data-testid="ima-cache-banner" style={{ marginTop: 'var(--ui-s-2)' }}>
          <LineIcon name="clock" size={16} /> 当前展示的是<strong>离线缓存结果</strong>
          （缓存于 {result.cache_time ? new Date(result.cache_time).toLocaleString('zh-CN') : '未知时间'}）。
          恢复联网后重新检索即取实时结果。
        </div>
      )}

      {/* 结果统计行：命中数 / 通道 / 分页 */}
      {searched && !searchError && result && (
        <div
          className="row"
          style={{ justifyContent: 'space-between', gap: 'var(--ui-s-2)', flexWrap: 'wrap', marginTop: 'var(--ui-s-2)' }}
          data-testid="ima-summary"
        >
          <span className="muted">
            {result.total > 0
              ? `命中 ${result.total} 条（第 ${result.page}/${result.pages} 页）`
              : '没有命中'}
            {' · '}通道：{channelLabel(result.channel)}
            {result.errors.length > 0 ? ` · 降级记录：${result.errors.join('；')}` : ''}
          </span>
          <span className="ui-row" style={{ gap: 'var(--ui-s-1)' }}>
            <button
              type="button"
              className="ui-btn ui-btn--sm"
              data-testid="ima-prev-page"
              disabled={searching || result.page <= 1}
              onClick={() => void runSearch(result.page - 1)}
            >
              <LineIcon name="chevronLeft" size={16} /> 上一页
            </button>
            <button
              type="button"
              className="ui-btn ui-btn--sm"
              data-testid="ima-next-page"
              disabled={searching || result.page >= result.pages}
              onClick={() => void runSearch(result.page + 1)}
            >
              下一页 <LineIcon name="chevronRight" size={16} />
            </button>
          </span>
        </div>
      )}

      {/* 结果列表 */}
      {result && result.results.length > 0 && (
        <div className="kb-results" data-testid="ima-results" style={{ marginTop: 'var(--ui-s-2)' }}>
          {result.results.map((hit) => (
            <ImaHitCard
              key={hit.src + hit.title}
              hit={hit}
              open={!!openOriginal[hit.src]}
              onToggle={() => setOpenOriginal((m) => ({ ...m, [hit.src]: !m[hit.src] }))}
            />
          ))}
        </div>
      )}

      {searched && !searchError && result && result.total === 0 && (
        <p className="muted" data-testid="ima-empty" style={{ marginTop: 'var(--ui-s-2)' }}>
          ima 库中没有命中「{result.query}」的条目
          {typeFilter || tagFilter.trim() ? '（当前过滤条件下）' : ''}——如实返回空，不塞占位结果。
        </p>
      )}
    </div>
  );
}

/** 单条命中：标题 / 摘要 / 标签 / `src:` 出处 / 查看原文（就地展开全文）。 */
function ImaHitCard({
  hit,
  open,
  onToggle,
}: {
  hit: ImaSearchHit;
  open: boolean;
  onToggle: () => void;
}) {
  const body = open ? hit.content : hit.introduction;
  return (
    <div className="card kb-hit ui-panel ui-panel--pad" data-testid={`ima-hit-${hit.media_id || 'x'}`}>
      <div className="row" style={{ justifyContent: 'space-between', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
        <strong>{hit.title || '（无标题条目）'}</strong>
        <span className="muted ui-row" style={{ gap: 'var(--ui-s-1)' }}>
          {hit.type && <span className="badge">{`类型 ${hit.type}`}</span>}
          {hit.folder && <span className="badge">{hit.folder}</span>}
          {hit.preview_only ? (
            <span className="badge warn" title="该条目在 ima 侧仅提供预览">
              仅预览
            </span>
          ) : (
            <span className="badge ok">全文可读</span>
          )}
        </span>
      </div>

      <p style={{ whiteSpace: 'pre-wrap' }}>
        {body || '（该条目没有可展示的正文）'}
        {open && hit.content_truncated && (
          <span className="muted">（正文过长，此处展示前 20000 字）</span>
        )}
      </p>

      {hit.tags.length > 0 && (
        <div className="muted">标签：{hit.tags.join('、')}</div>
      )}

      {/* B2 · 出处回溯：src: 可点开原文 */}
      <div className="muted" style={{ fontFamily: 'var(--ui-font-mono, monospace)' }}>
        <code data-testid={`ima-src-${hit.media_id || 'x'}`}>src: {hit.src}</code>
      </div>

      <div className="row" style={{ gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          className="ui-btn ui-btn--sm"
          data-testid={`ima-open-${hit.media_id || 'x'}`}
          onClick={onToggle}
          aria-expanded={open}
          title="就地展开该条目的全文（来自检索结果，不额外请求）"
        >
          <LineIcon name="eye" size={16} />
          {open ? '收起原文' : '查看原文'}
        </button>
        {hit.origin_url && (
          <a
            className="ui-btn ui-btn--sm"
            href={hit.origin_url}
            target="_blank"
            rel="noreferrer noopener"
            data-testid={`ima-url-${hit.media_id || 'x'}`}
          >
            <LineIcon name="external" size={16} /> 打开 ima 条目
          </a>
        )}
      </div>
    </div>
  );
}
