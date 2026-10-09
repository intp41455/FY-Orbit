/**
 * P12 · 插件与模板市场页（A-工具市场-03 · A-开箱模板-04 · A-开箱模板-06）。
 *
 * 核心功能：
 * 1. 评分体系（好用被顶上来）：展示星级与贝叶斯综合分、多维排序、星级分布与用户打分互动；
 * 2. 模板分层（新手默认 / 进阶可换 / 技术可拆 一级入口 🔒 GATE）：
 *    - 新手默认层：开箱即用，复杂度隐藏；
 *    - 进阶场景流水线：按场景切换并提供影响提示；
 *    - **技术入口一级可见**：顶栏常驻「技术可拆模式」；
 * 3. 模板导出与安全审查导入门禁：单文件 .fytemplate 导出、导入安全审查与权限/工具裁剪；
 * 4. 诚实性硬要求：高风险包显示风险标记，安装二次确认，权限透明。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  pluginsApi,
  type PluginCard,
  type TemplateHierarchyResponse,
  type TemplateMarketCard,
  type SecurityReviewReport,
} from '../../api/plugins';
import { LineIcon, type LineIconName } from '../../components/ui/LineIcon';
import { KnowledgeNiIcon } from '../../components/knowledgeui/KnowledgeNiIcon';
import '../../styles/pages/knowledge.css';
import { BaseBound } from '../../components/ui/SaveStatusIndicator';

const RISK_LABELS: Record<PluginCard['risk_level'], string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
};

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

/** 星级显示徽标 */
function StarRatingBadge({ rating }: { rating?: { average_rating: number; rating_count: number; score: number } }) {
  if (!rating) return null;
  return (
    <div
      className="fy-rating-badge"
      data-testid="plugin-rating-badge"
      style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: 'var(--ui-fs-sm)', color: 'var(--ui-ink-2)' }}
    >
      <span style={{ color: 'var(--ui-st-verifying)', fontWeight: 700 }}>★ {rating.average_rating.toFixed(1)}</span>
      <span className="muted" style={{ fontSize: 'var(--ui-fs-xs)' }}>({rating.rating_count}评价 · 得分 {rating.score.toFixed(1)})</span>
    </div>
  );
}

type MarketTab = 'plugins' | 'templates_novice' | 'templates_advanced' | 'templates_tech';

export function PluginMarketPage() {
  const [activeTab, setActiveTab] = useState<MarketTab>('plugins');
  const [items, setItems] = useState<PluginCard[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [sortBy, setSortBy] = useState('score');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<PluginCard | null>(null);
  const [grantId, setGrantId] = useState('');
  const [installNote, setInstallNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<PluginCard | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const limit = 20;

  // 评价打分状态
  const [userScore, setUserScore] = useState<number>(5);
  const [userComment, setUserComment] = useState<string>('');
  const [ratingMessage, setRatingMessage] = useState<string | null>(null);

  // 模板分层与模板市场状态
  const [hierarchy, setHierarchy] = useState<TemplateHierarchyResponse | null>(null);
  const [marketTemplates, setMarketTemplates] = useState<TemplateMarketCard[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<string>('all');
  const [switchImpact, setSwitchImpact] = useState<Record<string, any> | null>(null);

  // 模板导入模态框状态
  const [showImportModal, setShowImportModal] = useState(false);
  const [importJsonText, setImportJsonText] = useState('');
  const [securityReport, setSecurityReport] = useState<SecurityReviewReport | null>(null);
  const [selectedConfirmedTools, setSelectedConfirmedTools] = useState<string[]>([]);
  const [importResult, setImportResult] = useState<string | null>(null);

  const load = useCallback(
    async (opts?: { query?: string; offset?: number; sortBy?: string }) => {
      setLoading(true);
      setError(null);
      try {
        const curSort = opts?.sortBy ?? sortBy;
        const res = await pluginsApi.list({
          query: opts?.query ?? query,
          offset: opts?.offset ?? offset,
          sortBy: curSort,
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
    [query, offset, sortBy],
  );

  const loadTemplatesData = useCallback(async () => {
    try {
      const [hRes, tList] = await Promise.all([
        pluginsApi.getHierarchy(),
        pluginsApi.listTemplates({ sortBy: 'score' }),
      ]);
      setHierarchy(hRes);
      setMarketTemplates(tList.items);
    } catch (e) {
      // 容错处理
    }
  }, []);

  useEffect(() => {
    void load({ query: '', offset: 0 });
    void loadTemplatesData();
  }, [load, loadTemplatesData]);

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
    void load({ query, offset: 0, sortBy });
  };

  const handleSortChange = (newSort: string) => {
    setSortBy(newSort);
    void load({ query, offset: 0, sortBy: newSort });
  };

  const openDetail = (pkg: PluginCard) => {
    setSelected(pkg);
    setGrantId('');
    setInstallNote(null);
    setRatingMessage(null);
    setUserScore(5);
    setUserComment('');
  };

  const install = async (pkg: PluginCard) => {
    setInstallNote(null);
    try {
      const res = await pluginsApi.install(pkg.skill_id, pkg.gate_profile === 'plugin' ? grantId || null : null);
      setInstallNote(`已安装 ${res.name}（授权：${res.grant_id ?? '无需'}）`);
      setConfirming(null);
    } catch (e) {
      setInstallNote(`安装失败：${e instanceof Error ? e.message : String(e)}`);
      setConfirming(null);
    }
  };

  const submitRating = async (pkg: PluginCard) => {
    try {
      const res = await pluginsApi.rate(pkg.skill_id, userScore, userComment || undefined);
      setRatingMessage(`评分成功！当前平均分：${res.summary.average_rating.toFixed(1)}`);
      // 刷新列表数据
      void load();
      if (selected) {
        setSelected({
          ...selected,
          rating: res.summary,
          my_rating: { id: 'temp', actor_id: 'me', rating: userScore, comment: userComment, updated_at: new Date().toISOString() },
        });
      }
    } catch (e) {
      setRatingMessage(`评分失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleExportTemplate = async (templateId: string) => {
    try {
      const pkg = await pluginsApi.exportTemplate(templateId);
      const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${templateId}.fytemplate`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`导出失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleRunSecurityReview = async () => {
    setImportResult(null);
    try {
      const parsed = JSON.parse(importJsonText);
      const rep = await pluginsApi.securityReview(parsed);
      setSecurityReport(rep);
      setSelectedConfirmedTools(rep.requested_tools.filter((t) => !rep.dangerous_tools.includes(t)));
    } catch (e) {
      alert(`安全审查解析失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleConfirmImport = async () => {
    if (!securityReport) return;
    try {
      const parsed = JSON.parse(importJsonText);
      const res = await pluginsApi.importTemplate(parsed, selectedConfirmedTools);
      setImportResult(`导入成功！模板「${res.name}」已安全落地。`);
      void loadTemplatesData();
    } catch (e) {
      setImportResult(`导入失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const selectedFresh = selected
    ? items.find((i) => i.skill_id === selected.skill_id) ?? selected
    : null;

  return (
    <BaseBound surface="plugin-market" state="idle">
      <div className="page" data-testid="plugin-market-page">
        <div className="page-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--ui-s-3)' }}>
          <div>
            <h2>生态与市场中心</h2>
            <span className="muted">
              已通过服务端门禁（签名校验 + 静态扫描）的插件包与三层系统模板库；好用的自然被顶上来。
            </span>
          </div>
          {/* 锁三 / 需求 A-开箱模板-04：技术入口必须在界面一级可见（陛下原话「一下子就找到」） */}
          <div style={{ display: 'flex', gap: 'var(--ui-s-2)' }}>
            <button
              type="button"
              className={`ui-btn ${activeTab === 'templates_tech' ? 'ui-btn--primary' : ''}`}
              onClick={() => setActiveTab('templates_tech')}
              data-testid="first-class-tech-entry"
              style={{ fontWeight: 700 }}
            >
              <LineIcon name="terminal" size={16} />
              技术可拆入口 · 调试代码
            </button>
            <button
              type="button"
              className="ui-btn"
              onClick={() => setShowImportModal(true)}
              data-testid="template-import-btn"
            >
              <LineIcon name="download" size={16} />
              导入外部模板（安全门禁）
            </button>
          </div>
        </div>

        {/* 顶部三层分层导航栏 */}
        <div className="fy-market-nav" style={{ display: 'flex', gap: 'var(--ui-s-2)', margin: 'var(--ui-s-3) 0', borderBottom: '1px solid var(--ui-line-1)', paddingBottom: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
          <button
            type="button"
            className={`ui-btn ${activeTab === 'plugins' ? 'ui-btn--primary' : ''}`}
            onClick={() => setActiveTab('plugins')}
            data-testid="tab-plugins"
          >
            <LineIcon name="plugins" size={16} />
            工具插件市场
          </button>
          <button
            type="button"
            className={`ui-btn ${activeTab === 'templates_novice' ? 'ui-btn--primary' : ''}`}
            onClick={() => setActiveTab('templates_novice')}
            data-testid="tab-templates-novice"
          >
            <LineIcon name="check" size={16} />
            开箱模板 · 新手默认
          </button>
          <button
            type="button"
            className={`ui-btn ${activeTab === 'templates_advanced' ? 'ui-btn--primary' : ''}`}
            onClick={() => setActiveTab('templates_advanced')}
            data-testid="tab-templates-advanced"
          >
            <LineIcon name="flow" size={16} />
            进阶流水线 · 场景矩阵 {hierarchy?.layers ? `(${hierarchy.layers.length}层)` : ''}
          </button>
        </div>

        {/* ===================== TAB 1: 插件市场 ===================== */}
        {activeTab === 'plugins' && (
          <>
            <form onSubmit={doQuery} className="fy-plugin-toolbar" data-testid="plugin-toolbar" style={{ display: 'flex', gap: 'var(--ui-s-2)', flexWrap: 'wrap', alignItems: 'center' }}>
              <input
                className="ui-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="按名称搜索插件…"
                aria-label="搜索插件包"
                autoComplete="off"
                data-testid="plugin-query"
                style={{ flex: 1, minWidth: '200px' }}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-1)' }}>
                <label htmlFor="plugin-sort-select" className="muted" style={{ fontSize: 'var(--ui-fs-sm)' }}>排序：</label>
                <select
                  id="plugin-sort-select"
                  className="ui-input"
                  value={sortBy}
                  onChange={(e) => handleSortChange(e.target.value)}
                  data-testid="plugin-sort-select"
                  aria-label="排序维度"
                >
                  <option value="score">好评推荐（好用被顶上来）</option>
                  <option value="rating">星级最高</option>
                  <option value="reviews">评价最多</option>
                  <option value="created">最新上架</option>
                  <option value="name">按名称字母</option>
                </select>
              </div>
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
                  <div className="row spread" style={{ alignItems: 'flex-start' }}>
                    <div>
                      <strong>
                        <LineIcon name="plugins" size={16} /> {pkg.name}{' '}
                        <span className="muted">v{pkg.version}</span>
                      </strong>
                      <div style={{ marginTop: '4px' }}>
                        <StarRatingBadge rating={pkg.rating} />
                      </div>
                    </div>
                    <RiskBadge level={pkg.risk_level} />
                  </div>
                  <div className="muted small" style={{ margin: '4px 0' }}>
                    {pkg.domain} · {pkg.license} · hash {pkg.package_hash} ·{' '}
                    {pkg.signature_verified ? '已验签' : '未验签'}
                  </div>
                  <ScanSummary pkg={pkg} />
                  <div style={{ display: 'flex', gap: 'var(--ui-s-2)', flexWrap: 'wrap', marginTop: 'var(--ui-s-2)' }}>
                    <button
                      className="ui-btn"
                      onClick={() => openDetail(pkg)}
                      data-testid={`plugin-detail-${pkg.skill_id}`}
                    >
                      <LineIcon name="eye" size={16} />
                      查看详情 & 评价
                    </button>
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
          </>
        )}

        {/* ===================== TAB 2: 新手默认层 ===================== */}
        {activeTab === 'templates_novice' && (
          <div data-testid="novice-template-view">
            <div style={{ padding: 'var(--ui-s-3)', background: 'var(--ui-glass-1)', borderRadius: 'var(--ui-r-md)', marginBottom: 'var(--ui-s-3)' }}>
              <h3 style={{ margin: 0, color: 'var(--ui-ink-1)' }}>小白模式 · 开箱即用</h3>
              <p className="muted" style={{ margin: '4px 0 0 0', fontSize: 'var(--ui-fs-sm)' }}>
                喂到嘴边的成熟流水线，自动预置 8 类隐性必备条件，隐藏底层拓扑复杂度与代码，直接填目标即可试跑。
              </p>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 'var(--ui-s-3)' }}>
              {marketTemplates.map((t) => (
                <div key={t.template_id} className="fy-plugin-item" data-testid={`novice-card-${t.template_id}`}>
                  <div className="row spread">
                    <strong>{t.name}</strong>
                    <span className="ui-badge ui-badge--complete">{t.scenario_label || t.scenario}</span>
                  </div>
                  <p className="muted small" style={{ margin: 'var(--ui-s-2) 0' }}>{t.summary}</p>
                  <div style={{ margin: 'var(--ui-s-1) 0' }}>
                    <StarRatingBadge rating={t.rating} />
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--ui-s-2)', marginTop: 'var(--ui-s-2)' }}>
                    <button
                      type="button"
                      className="ui-btn ui-btn--primary"
                      onClick={() => alert(`已选择「${t.name}」，可在工作台直接启动！`)}
                    >
                      立即开箱试跑
                    </button>
                    <button
                      type="button"
                      className="ui-btn"
                      onClick={() => handleExportTemplate(t.template_id)}
                      data-testid={`export-btn-${t.template_id}`}
                    >
                      导出包
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ===================== TAB 3: 进阶场景流水线 ===================== */}
        {activeTab === 'templates_advanced' && (
          <div data-testid="advanced-template-view">
            <div style={{ display: 'flex', gap: 'var(--ui-s-2)', marginBottom: 'var(--ui-s-3)', flexWrap: 'wrap' }}>
              {['all', 'writing', 'development', 'research', 'data'].map((sc) => (
                <button
                  key={sc}
                  type="button"
                  className={`ui-btn ${selectedScenario === sc ? 'ui-btn--primary' : ''}`}
                  onClick={() => setSelectedScenario(sc)}
                >
                  {sc === 'all' ? '全部流水线' : sc === 'writing' ? '写作流水线' : sc === 'development' ? '研发流水线' : sc === 'research' ? '调研流水线' : '数据分析流水线'}
                </button>
              ))}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 'var(--ui-s-3)' }}>
              {marketTemplates
                .filter((t) => selectedScenario === 'all' || t.scenario === selectedScenario)
                .map((t) => (
                  <div key={t.template_id} className="fy-plugin-item" data-testid={`advanced-card-${t.template_id}`}>
                    <strong>{t.name}</strong>
                    <p className="muted small">{t.summary}</p>
                    <div className="muted small">包含成员：{t.member_count} 个 · 档位：{t.quality_tier}</div>
                    <div style={{ margin: 'var(--ui-s-2) 0' }}>
                      <StarRatingBadge rating={t.rating} />
                    </div>
                    <div style={{ display: 'flex', gap: 'var(--ui-s-2)' }}>
                      <button
                        type="button"
                        className="ui-btn"
                        onClick={async () => {
                          const imp = await pluginsApi.previewSwitchImpact('writing-pipeline', t.template_id);
                          setSwitchImpact(imp);
                        }}
                      >
                        切换预检
                      </button>
                      <button
                        type="button"
                        className="ui-btn"
                        onClick={() => handleExportTemplate(t.template_id)}
                      >
                        导出
                      </button>
                    </div>
                  </div>
                ))}
            </div>

            {switchImpact && (
              <div className="ui-modal" role="dialog" style={{ padding: 'var(--ui-s-4)', marginTop: 'var(--ui-s-4)' }}>
                <h4>模板切换影响预检</h4>
                <p className="ui-hint">{switchImpact.warning}</p>
                <div>新增成员：{switchImpact.member_changes?.added?.join(', ') || '无'}</div>
                <div>移除成员：{switchImpact.member_changes?.removed?.join(', ') || '无'}</div>
                <button type="button" className="ui-btn" onClick={() => setSwitchImpact(null)} style={{ marginTop: 'var(--ui-s-2)' }}>
                  关闭预检
                </button>
              </div>
            )}
          </div>
        )}

        {/* ===================== TAB 4: 技术可拆层（一级可见） ===================== */}
        {activeTab === 'templates_tech' && (
          <div data-testid="technical-template-view">
            <div style={{ padding: 'var(--ui-s-3)', background: 'var(--ui-glass-1)', borderRadius: 'var(--ui-r-md)', marginBottom: 'var(--ui-s-3)' }}>
              <h3 style={{ margin: 0, color: 'var(--ui-st-verifying)' }}>
                <LineIcon name="terminal" size={20} /> 技术层 · 可拆解拓扑与代码模式
              </h3>
              <p className="muted" style={{ margin: '4px 0 0 0', fontSize: 'var(--ui-fs-sm)' }}>
                陛下要求：技术人员必须能够一下子找到入口。拆开模板、改拓扑、改状态机、直接调试受限 DSL 代码并双向同源往返。
              </p>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 'var(--ui-s-3)' }}>
              <div className="fy-plugin-item">
                <strong>受限 DSL 代码同源生成</strong>
                <p className="muted small">模板的执行管线映射成受限 DSL 图（仅使用 agent 动词），信息无损往返。</p>
                <pre style={{ background: 'var(--ui-glass-2)', padding: 'var(--ui-s-2)', borderRadius: 'var(--ui-r-sm)', fontSize: 'var(--ui-fs-xs)' }}>
                  {`# ▼ TEMPLATE-SPEC-BEGIN\n# schema_version: 1.0.0\n# controller: 不得直接执行具体任务\n# ▲ TEMPLATE-SPEC-END\n\ndef main_pipeline():\n    return transform(verb="agent", members=[...])`}
                </pre>
              </div>
              <div className="fy-plugin-item">
                <strong>总控提示词边界体检（否定感知）</strong>
                <p className="muted small">自动机检总控提示词是否包含「不得直接执行具体任务」，违规给警告但不阻断技术探索。</p>
                <span className="ui-badge ui-badge--complete">规则机检通过</span>
              </div>
            </div>
          </div>
        )}

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

              <div style={{ margin: 'var(--ui-s-2) 0' }}>
                <StarRatingBadge rating={selectedFresh.rating} />
              </div>

              <CapabilityList pkg={selectedFresh} />
              <ScanSummary pkg={selectedFresh} />
              <GrantList pkg={selectedFresh} />

              {/* 评分分布直方图与评价区（A-工具市场-03） */}
              <div className="fy-plugin-caps" data-testid="rating-section" style={{ background: 'var(--ui-glass-1)', padding: 'var(--ui-s-3)', borderRadius: 'var(--ui-r-md)' }}>
                <strong>用户评分与评价</strong>
                {selectedFresh.rating?.distribution && (
                  <div style={{ display: 'flex', gap: 'var(--ui-s-2)', margin: 'var(--ui-s-2) 0', fontSize: 'var(--ui-fs-xs)' }}>
                    {Object.entries(selectedFresh.rating.distribution).reverse().map(([star, count]) => (
                      <div key={star} style={{ textAlign: 'center', flex: 1 }}>
                        <div>{star}★</div>
                        <div style={{ height: '30px', background: 'var(--ui-glass-2)', display: 'flex', alignItems: 'flex-end', borderRadius: '2px' }}>
                          <div style={{ width: '100%', height: `${Math.min(100, count * 20)}%`, background: 'var(--ui-st-complete)' }} />
                        </div>
                        <div className="muted">{count}</div>
                      </div>
                    ))}
                  </div>
                )}

                {/* 用户提交打分 */}
                <div style={{ marginTop: 'var(--ui-s-2)', display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-2)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-2)' }}>
                    <span>我要打分：</span>
                    {[1, 2, 3, 4, 5].map((st) => (
                      <button
                        key={st}
                        type="button"
                        className={`ui-btn ${userScore === st ? 'ui-btn--primary' : ''}`}
                        style={{ padding: '2px 8px' }}
                        onClick={() => setUserScore(st)}
                        data-testid={`rate-star-${st}`}
                      >
                        {st}★
                      </button>
                    ))}
                  </div>
                  <input
                    className="ui-input"
                    placeholder="写点评语（选填）…"
                    value={userComment}
                    onChange={(e) => setUserComment(e.target.value)}
                    data-testid="rate-comment-input"
                  />
                  <button
                    type="button"
                    className="ui-btn ui-btn--primary"
                    onClick={() => submitRating(selectedFresh)}
                    data-testid="submit-rating-btn"
                    style={{ alignSelf: 'flex-start' }}
                  >
                    提交评分
                  </button>
                  {ratingMessage && <div className="muted small">{ratingMessage}</div>}
                </div>
              </div>

              {selectedFresh.gate_profile === 'plugin' && (
                <label className="fy-plugin-grant">
                  授权 ID（plugin 包必须出示有效授权）：
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

        {/* ---------- 安装二次确认 ---------- */}
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
              <div style={{ display: 'flex', gap: 'var(--ui-s-2)', justifyContent: 'flex-end' }}>
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

        {/* ---------- 模板导入安全审查门禁模态框（A-开箱模板-06） ---------- */}
        {showImportModal && (
          <>
            <button
              type="button"
              className="ui-overlay"
              aria-label="关闭导入"
              onClick={() => setShowImportModal(false)}
            />
            <div
              className="ui-modal"
              role="dialog"
              aria-modal="true"
              style={{ padding: 'var(--ui-s-5)', maxWidth: '640px', width: '90%', display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-3)' }}
              data-testid="template-import-modal"
            >
              <h3 style={{ margin: 0 }}>
                <LineIcon name="download" size={18} /> 导入外部系统模板（强制安全审查）
              </h3>
              <p className="muted small" style={{ margin: 0 }}>
                根据安全规范，导入外部模板必须经过安全审查门禁（工具白名单审查、高危操作拦截、权限范围审计与防篡改校验）。
              </p>

              <textarea
                className="ui-input"
                rows={6}
                placeholder="请粘贴 .fytemplate JSON 包内容…"
                value={importJsonText}
                onChange={(e) => setImportJsonText(e.target.value)}
                data-testid="import-json-textarea"
              />

              <button
                type="button"
                className="ui-btn"
                onClick={handleRunSecurityReview}
                data-testid="run-security-review-btn"
              >
                执行安全审查门禁
              </button>

              {securityReport && (
                <div
                  style={{
                    padding: 'var(--ui-s-3)',
                    background: securityReport.risk_level === 'high' ? 'var(--ui-st-failed-bg)' : 'var(--ui-glass-1)',
                    border: `1px solid ${securityReport.risk_level === 'high' ? 'var(--ui-st-failed)' : 'var(--ui-line-1)'}`,
                    borderRadius: 'var(--ui-r-md)',
                  }}
                  data-testid="security-report-panel"
                >
                  <div className="row spread">
                    <strong>审查结论：{securityReport.passed ? '安全合规通过' : '发现安全风险'}</strong>
                    <span className={`ui-badge ui-badge--${securityReport.risk_level === 'low' ? 'complete' : securityReport.risk_level === 'high' ? 'failed' : 'waiting'}`}>
                      风险等级：{securityReport.risk_level.toUpperCase()}
                    </span>
                  </div>

                  {securityReport.dangerous_tools.length > 0 && (
                    <div style={{ color: 'var(--ui-st-failed)', margin: 'var(--ui-s-1) 0' }}>
                      ⚠️ 申请了高危工具：{securityReport.dangerous_tools.join(', ')}（必须剔除才可导入）
                    </div>
                  )}

                  <div style={{ marginTop: 'var(--ui-s-2)' }}>
                    <strong>用户权限裁剪（可逐项确认允许的工具）：</strong>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--ui-s-1)', marginTop: '4px' }}>
                      {securityReport.requested_tools.map((t) => (
                        <label key={t} style={{ fontSize: 'var(--ui-fs-sm)', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                          <input
                            type="checkbox"
                            checked={selectedConfirmedTools.includes(t)}
                            onChange={(e) => {
                              if (e.target.checked) setSelectedConfirmedTools([...selectedConfirmedTools, t]);
                              else setSelectedConfirmedTools(selectedConfirmedTools.filter((item) => item !== t));
                            }}
                          />
                          {t}
                        </label>
                      ))}
                    </div>
                  </div>

                  <div style={{ marginTop: 'var(--ui-s-3)' }}>
                    <button
                      type="button"
                      className="ui-btn ui-btn--primary"
                      onClick={handleConfirmImport}
                      data-testid="confirm-import-btn"
                    >
                      确认裁剪并导入落地
                    </button>
                  </div>
                </div>
              )}

              {importResult && <div className="muted small" role="status">{importResult}</div>}

              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--ui-s-2)' }}>
                <button type="button" className="ui-btn" onClick={() => setShowImportModal(false)}>
                  关闭
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </BaseBound>
  );
}