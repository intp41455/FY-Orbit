/**
 * P6 · 插件市场页（需求 14 后半 · 前端）。
 *
 * 诚实性硬要求（工单原文）：
 *  - **必须显示包的风险等级与扫描摘要**——列表与详情都不得隐藏风险标记；
 *  - 安装前**必须**展示该包声称/派生的能力；
 *  - 安装失败（如缺授权）显式报错，绝不假装成功。
 */
import { useCallback, useEffect, useState } from 'react';
import { pluginsApi, type PluginCard } from '../../api/plugins';

const RISK_LABELS: Record<PluginCard['risk_level'], string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
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
  return (
    <span className={`badge fy-plugin-risk-${level}`} data-testid={`plugin-risk-${level}`}>
      ⚠ {RISK_LABELS[level]}
    </span>
  );
}

function ScanSummary({ pkg }: { pkg: PluginCard }) {
  return (
    <div className="fy-plugin-scan" data-testid="plugin-scan-summary">
      <span>扫描：{pkg.scan.passed ? '✅ 通过' : '❌ 未通过'}</span>
      <span> · 扫描风险 {pkg.scan.risk_level}</span>
      <span> · 发现 {pkg.scan.finding_count} 条</span>
      {pkg.scan.scanner_version && <span> · 扫描器 v{pkg.scan.scanner_version}</span>}
    </div>
  );
}

function CapabilityList({ pkg }: { pkg: PluginCard }) {
  return (
    <div className="fy-plugin-caps" data-testid="plugin-capabilities">
      <strong>该包的能力（安装前必读）：</strong>
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
  const limit = 20;

  const load = useCallback(async (opts?: { query?: string; offset?: number }) => {
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
  }, [query, offset]);

  useEffect(() => {
    void load({ query: '', offset: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      setInstallNote(`✅ 已安装 ${res.name}（授权：${res.grant_id ?? '无需'}）`);
    } catch (e) {
      // 诚实失败：缺授权等错误原样展示，绝不假装安装成功。
      setInstallNote(`❌ 安装失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const selectedFresh = selected
    ? items.find((i) => i.skill_id === selected.skill_id) ?? selected
    : null;

  return (
    <div className="page" data-testid="plugin-market-page">
      <h2>插件市场</h2>
      <p className="muted">
        只展示已通过服务端上架门禁（签名校验 + 静态扫描）的包；未过门禁的包在服务端就不可见。
      </p>

      <form onSubmit={doQuery} className="fy-plugin-toolbar" data-testid="plugin-toolbar">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="按名称搜索…"
          aria-label="搜索插件包"
          data-testid="plugin-query"
        />
        <button type="submit" className="primary" data-testid="plugin-search">搜索</button>
      </form>

      {error && <div className="error-text" role="alert">{error}</div>}
      {loading && <div className="muted" data-testid="plugin-loading">加载中…</div>}

      <ul className="fy-plugin-list" data-testid="plugin-list">
        {items.map((pkg) => (
          <li key={pkg.skill_id} className="card fy-plugin-item" data-testid="plugin-item">
            <div className="row spread">
              <strong>{pkg.name} <span className="muted">v{pkg.version}</span></strong>
              <RiskBadge level={pkg.risk_level} />
            </div>
            <div className="muted small">
              {pkg.domain} · {pkg.license} · hash {pkg.package_hash} ·{' '}
              {pkg.signature_verified ? '已验签' : '未验签'}
            </div>
            <ScanSummary pkg={pkg} />
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <button onClick={() => openDetail(pkg)} data-testid={`plugin-detail-${pkg.skill_id}`}>
                查看详情
              </button>
              <button className="primary" onClick={() => void install(pkg)}
                data-testid={`plugin-install-${pkg.skill_id}`}>
                安装
              </button>
            </div>
          </li>
        ))}
      </ul>

      {items.length === 0 && !loading && (
        <div className="muted" data-testid="plugin-empty">市场暂无符合条件的包。</div>
      )}

      <div className="fy-plugin-pager">
        <button disabled={offset === 0} onClick={() => void load({ offset: Math.max(0, offset - limit) })}
          data-testid="plugin-prev">上一页</button>
        <span className="muted">共 {total} 个包</span>
        <button disabled={offset + limit >= total}
          onClick={() => void load({ offset: offset + limit })} data-testid="plugin-next">下一页</button>
      </div>

      {selectedFresh && (
        <div className="card fy-plugin-detail" data-testid="plugin-detail">
          <div className="row spread">
            <strong>详情 · {selectedFresh.name} v{selectedFresh.version}</strong>
            <RiskBadge level={selectedFresh.risk_level} />
          </div>
          <CapabilityList pkg={selectedFresh} />
          <ScanSummary pkg={selectedFresh} />
          {selectedFresh.scan.findings && selectedFresh.scan.findings.length > 0 && (
            <ul data-testid="plugin-findings">
              {selectedFresh.scan.findings.map((f, i) => (
                <li key={i}><code>{f.severity}</code> {f.code}：{f.message}</li>
              ))}
            </ul>
          )}
          {selectedFresh.gate_profile === 'plugin' && (
            <label className="fy-plugin-grant">
              授权 ID（plugin 包必须出示由 GrantService 创建的有效授权）：
              <input
                value={grantId}
                onChange={(e) => setGrantId(e.target.value)}
                placeholder="grant_id"
                data-testid="plugin-grant-input"
              />
            </label>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="primary" onClick={() => void install(selectedFresh)}
              data-testid="plugin-detail-install">
              安装{selectedFresh.gate_profile === 'plugin' ? '（需授权）' : ''}
            </button>
            <button onClick={() => setSelected(null)} data-testid="plugin-detail-close">关闭</button>
          </div>
          {installNote && <div role="status" data-testid="plugin-install-note">{installNote}</div>}
        </div>
      )}
    </div>
  );
}
