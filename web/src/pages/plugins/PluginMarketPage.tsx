/**
 * P6 · 插件市场页（需求 14 后半 · 前端）。
 *
 * 诚实性硬要求（工单原文）：
 *  - **必须显示包的风险等级与扫描摘要**——列表与详情都不得隐藏风险标记；
 *  - 安装前**必须**展示该包声称/派生的能力；
 *  - 安装失败（如缺授权）显式报错，绝不假装成功。
 *
 * 包 E 视觉层补充：
 *  - 风险分级**不只靠颜色**：徽标 = 颜色 + 图标 + 中文文字（「低/中/高风险」）；
 *  - 安装是 consequential 动作：列表上的「安装」走 `.ui-modal` 二次确认，
 *    弹窗里逐条列出**将获得的权限**，默认焦点落在「取消」；
 *  - 详情改抽屉（.fy-plugin-detail），不占页宽。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { pluginsApi, type PluginCard } from '../../api/plugins';
import { LineIcon, type LineIconName } from '../../components/ui/LineIcon';
import { KnowledgeNiIcon } from '../../components/knowledgeui/KnowledgeNiIcon';
import '../../styles/pages/knowledge.css';

const RISK_LABELS: Record<PluginCard['risk_level'], string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
};

/**
 * 风险 → 九档语义 + 图标。
 *
 * ⚠ 低风险刻意用 `--ui-st-complete` **深蓝**而不是绿色（总纲红线一）。
 * ⚠ `clock` 不在 LineIcon 集里（总纲 §3 却要求用），取本包局部补录集。
 */
type StatusGlyphName = LineIconName | 'clock';

function StatusGlyph({ name, size = 14 }: { name: StatusGlyphName; size?: number }) {
  if (name === 'clock') return <KnowledgeNiIcon name="clock" size={size} />;
  return <LineIcon name={name} size={size} />;
}

const RISK_META: Record<
  PluginCard['risk_level'],
  { tone: 'complete' | 'waiting' | 'failed'; icon: StatusGlyphName }
> = {
  low: { tone: 'complete', icon: 'check' },
  medium: { tone: 'waiting', icon: 'clock' },
  high: { tone: 'failed', icon: 'alert' },
};

const RISK_REASON_LABELS: Record<string, string> = {
  materializes_files: '会在磁盘物化并执行文件',
  unsigned: '未签名（来源不可验证）',
};

const CAPABILITY_LABELS: Record<string, string> = {
  'materialize:files': '物化文件并执行',
  'instruction:inline': '纯指令文本（不执行）',
};

function RiskBadge({ level }: { level: PluginCard['risk_level'] }) {
  const meta = RISK_META[level];
  return (
    <span className={`fy-plugin-risk-${level}`} data-testid={`plugin-risk-${level}`}>
      <StatusGlyph name={meta.icon} size={14} />
      {RISK_LABELS[level]}
    </span>
  );
}

function ScanSummary({ pkg }: { pkg: PluginCard }) {
  return (
    <div className="fy-plugin-scan" data-testid="plugin-scan-summary">
      {/* 扫描结果也是状态：图标 + 文字 + 数值，不只靠颜色 */}
      <span className={`ui-badge ui-badge--${pkg.scan.passed ? 'complete' : 'failed'}`}>
        <StatusGlyph name={pkg.scan.passed ? 'check' : 'xCircle'} size={14} />
        扫描：{pkg.scan.passed ? '通过' : '未通过'}
      </span>
      <span>扫描风险 {pkg.scan.risk_level}</span>
      <span>发现 {pkg.scan.finding_count} 条</span>
      {pkg.scan.scanner_version && <span>扫描器 v{pkg.scan.scanner_version}</span>}
    </div>
  );
}

function CapabilityList({ pkg }: { pkg: PluginCard }) {
  return (
    <div className="fy-plugin-caps" data-testid="plugin-capabilities">
      <strong>
        <LineIcon name="alert" size={16} /> 该包的能力（安装前必读）：
      </strong>
      <ul>
        {pkg.capabilities.map((c) => (
          <li key={c}>{CAPABILITY_LABELS[c] ?? c}</li>
        ))}
        {pkg.risk_reasons.map((r) => (
          <li key={r}>风险来源：{RISK_REASON_LABELS[r] ?? r}</li>
        ))}
        {!pkg.signature_verified && <li>包未经签名校验</li>}
      </ul>
    </div>
  );
}

/** 「将获得的权限」清单：安装确认弹窗与详情共用同一份渲染。 */
function GrantList({ pkg }: { pkg: PluginCard }) {
  const grants = pkg.capabilities.map((c) => CAPABILITY_LABELS[c] ?? c);
  if (pkg.risk_reasons.includes('materializes_files')) {
    grants.push('在本机磁盘创建并执行文件');
  }
  if (!pkg.signature_verified) {
    grants.push('接受未签名来源（无法校验发布者身份）');
  }
  return (
    <ul className="fy-plugin-caps" data-testid="plugin-grant-list" style={{ margin: 0 }}>
      <li style={{ fontWeight: 700, color: 'var(--ui-ink-2)' }}>安装后你将获得：</li>
      {grants.map((g) => (
        <li key={g}>· {g}</li>
      ))}
    </ul>
  );
}

export function PluginMarketPage() {
  const [items, setItems] = useState<PluginCard[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<PluginCard | null>(null);
  const [grantId, setGrantId] = useState('');
  const [installNote, setInstallNote] = useState<string | null>(null);
  /** 二次确认的目标包（非空时弹 .ui-modal） */
  const [confirming, setConfirming] = useState<PluginCard | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const limit = 20;

  const load = useCallback(
    async (opts?: { query?: string; offset?: number }) => {
      setLoading(true);
      setError(null);
      try {
        const res = await pluginsApi.list({
          query: opts?.query ?? query,
          offset: opts?.offset ?? offset,
          limit,
        });
        setItems(res.items);
        setTotal(res.total);
        setOffset(res.offset);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [query, offset],
  );

  useEffect(() => {
    void load({ query: '', offset: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 二次确认弹窗：默认焦点在「取消」，Esc 关闭，回车/空格触发「取消」。 */
  useEffect(() => {
    if (!confirming) return;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setConfirming(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [confirming]);

  const doQuery = (e: React.FormEvent) => {
    e.preventDefault();
    void load({ query, offset: 0 });
  };

  const openDetail = (pkg: PluginCard) => {
    setSelected(pkg);
    setGrantId('');
    setInstallNote(null);
  };

  const install = async (pkg: PluginCard) => {
    setInstallNote(null);
    try {
      const res = await pluginsApi.install(pkg.skill_id, pkg.gate_profile === 'plugin' ? grantId || null : null);
      setInstallNote(`已安装 ${res.name}（授权：${res.grant_id ?? '无需'}）`);
      setConfirming(null);
    } catch (e) {
      // 诚实失败：缺授权等错误原样展示，绝不假装安装成功。
      setInstallNote(`安装失败：${e instanceof Error ? e.message : String(e)}`);
      setConfirming(null);
    }
  };

  const selectedFresh = selected
    ? items.find((i) => i.skill_id === selected.skill_id) ?? selected
    : null;

  return (
    <div className="page" data-testid="plugin-market-page">
      <div className="page-head">
        <h2>插件市场</h2>
        <span className="muted">
          只展示已通过服务端上架门禁（签名校验 + 静态扫描）的包；未过门禁的包在服务端就不可见。
        </span>
      </div>

      <form onSubmit={doQuery} className="fy-plugin-toolbar" data-testid="plugin-toolbar">
        <input
          className="ui-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="按名称搜索…"
          aria-label="搜索插件包"
          autoComplete="off"
          data-testid="plugin-query"
        />
        <button type="submit" className="ui-btn ui-btn--primary" data-testid="plugin-search">
          <LineIcon name="search" size={16} />
          搜索
        </button>
      </form>

      {error && (
        <div className="error-text" role="alert">
          <LineIcon name="alert" size={16} /> {error}
        </div>
      )}
      {loading && (
        <div className="muted" data-testid="plugin-loading">
          加载中…
        </div>
      )}

      <ul className="fy-plugin-list" data-testid="plugin-list">
        {items.map((pkg) => (
          <li key={pkg.skill_id} className="fy-plugin-item" data-testid="plugin-item">
            <div className="row spread">
              <strong>
                <LineIcon name="plugins" size={16} /> {pkg.name}{' '}
                <span className="muted">v{pkg.version}</span>
              </strong>
              <RiskBadge level={pkg.risk_level} />
            </div>
            <div className="muted small">
              {pkg.domain} · {pkg.license} · hash {pkg.package_hash} ·{' '}
              {pkg.signature_verified ? '已验签' : '未验签'}
            </div>
            <ScanSummary pkg={pkg} />
            <div style={{ display: 'flex', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
              <button
                className="ui-btn"
                onClick={() => openDetail(pkg)}
                data-testid={`plugin-detail-${pkg.skill_id}`}
              >
                <LineIcon name="eye" size={16} />
                查看详情
              </button>
              {/* 列表快捷安装是 consequential 动作 → 二次确认 */}
              <button
                className="ui-btn ui-btn--primary"
                onClick={() => {
                  setConfirming(pkg);
                  setInstallNote(null);
                }}
                data-testid={`plugin-install-${pkg.skill_id}`}
              >
                安装
              </button>
            </div>
          </li>
        ))}
      </ul>

      {items.length === 0 && !loading && (
        <div className="muted" data-testid="plugin-empty">
          市场暂无符合条件的包。
        </div>
      )}

      <div className="fy-plugin-pager">
        <button
          className="ui-btn"
          disabled={offset === 0}
          onClick={() => void load({ offset: Math.max(0, offset - limit) })}
          data-testid="plugin-prev"
        >
          <LineIcon name="chevronLeft" size={16} />
          上一页
        </button>
        <span className="muted">共 {total} 个包</span>
        <button
          className="ui-btn"
          disabled={offset + limit >= total}
          onClick={() => void load({ offset: offset + limit })}
          data-testid="plugin-next"
        >
          下一页
          <LineIcon name="chevronRight" size={16} />
        </button>
      </div>

      {/* ---------- 详情抽屉 ---------- */}
      {selectedFresh && (
        <>
          <button
            type="button"
            className="ui-overlay"
            aria-label="关闭插件包详情"
            onClick={() => setSelected(null)}
          />
          <div className="fy-plugin-detail" role="dialog" aria-modal="true" aria-label="插件包详情" data-testid="plugin-detail">
            <header style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-3)' }}>
              <LineIcon name="plugins" size={20} />
              <strong>详情 · {selectedFresh.name} v{selectedFresh.version}</strong>
              <RiskBadge level={selectedFresh.risk_level} />
              <span className="ui-spacer" />
              <button
                type="button"
                className="ui-btn ui-btn--icon"
                aria-label="关闭详情"
                onClick={() => setSelected(null)}
                data-testid="plugin-detail-close"
              >
                <LineIcon name="close" size={18} />
              </button>
            </header>

            <CapabilityList pkg={selectedFresh} />
            <ScanSummary pkg={selectedFresh} />
            <GrantList pkg={selectedFresh} />

            {selectedFresh.scan.findings && selectedFresh.scan.findings.length > 0 && (
              <ul data-testid="plugin-findings">
                {selectedFresh.scan.findings.map((f, i) => (
                  <li key={i}>
                    <span className={`ui-badge ui-badge--${f.severity === 'high' ? 'failed' : 'waiting'}`}>
                      {f.severity}
                    </span>{' '}
                    <code>{f.code}</code>：{f.message}
                  </li>
                ))}
              </ul>
            )}

            {selectedFresh.gate_profile === 'plugin' && (
              <label className="fy-plugin-grant">
                授权 ID（plugin 包必须出示由 GrantService 创建的有效授权）：
                <input
                  className="ui-input"
                  value={grantId}
                  onChange={(e) => setGrantId(e.target.value)}
                  placeholder="grant_id"
                  autoComplete="off"
                  data-testid="plugin-grant-input"
                />
              </label>
            )}

            <div style={{ display: 'flex', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
              <button
                className="ui-btn ui-btn--primary"
                onClick={() => void install(selectedFresh)}
                data-testid="plugin-detail-install"
              >
                <LineIcon name="download" size={16} />
                安装{selectedFresh.gate_profile === 'plugin' ? '（需授权）' : ''}
              </button>
              <button className="ui-btn" onClick={() => setSelected(null)} data-testid="plugin-detail-back">
                关闭
              </button>
            </div>
            {/* 安装结果：role=status 让读屏即时播报 */}
            {installNote && (
              <div
                role="status"
                data-testid="plugin-install-note"
                className={installNote.startsWith('安装失败') ? 'ui-toast ui-toast--error' : 'ui-toast'}
              >
                {installNote}
              </div>
            )}
          </div>
        </>
      )}

      {/* ---------- 安装二次确认（consequential 动作） ---------- */}
      {confirming && (
        <>
          <button
            type="button"
            className="ui-overlay"
            aria-label="取消安装"
            onClick={() => setConfirming(null)}
          />
          <div
            className="ui-modal"
            role="dialog"
            aria-modal="true"
            aria-label="安装确认"
            style={{ padding: 'var(--ui-s-5)', display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-3)' }}
            data-testid="plugin-confirm"
          >
            <h3 style={{ margin: 0, fontSize: 'var(--ui-fs-lg)', color: 'var(--ui-ink-1)' }}>
              <LineIcon name="alert" size={18} /> 安装「{confirming.name}」？
            </h3>
            <RiskBadge level={confirming.risk_level} />
            <CapabilityList pkg={confirming} />
            <GrantList pkg={confirming} />
            {confirming.gate_profile === 'plugin' && (
              <p className="ui-hint" style={{ margin: 0 }}>
                这是 plugin 包，还需要在详情里填入授权 ID 才能安装。
              </p>
            )}
            <div style={{ display: 'flex', gap: 'var(--ui-s-2)', justifyContent: 'flex-end' }}>
              {/* 默认焦点在「取消」：安装是后果不可逆的动作，别让用户误触 */}
              <button
                ref={cancelRef}
                className="ui-btn"
                onClick={() => setConfirming(null)}
                data-testid="plugin-confirm-cancel"
              >
                取消
              </button>
              <button
                className="ui-btn ui-btn--primary"
                onClick={() => {
                  setSelected(confirming);
                  setConfirming(null);
                }}
                data-testid="plugin-confirm-continue"
              >
                去详情确认安装
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}