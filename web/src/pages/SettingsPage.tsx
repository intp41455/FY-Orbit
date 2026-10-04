import { useCallback, useMemo, useState } from 'react';
import { dataApi } from '../api/data';
import { modelsApi } from '../api/models';
import { authApi } from '../api/auth';
import { useOptionalAuth } from '../auth/AuthContext';
import type {
  ModelCatalogSummary,
  ProviderHealthInfo,
  ProviderId,
} from '../api/models';
import { useAsync, Spinner, errorMessage } from '../components/ui';
import { AutomationPermissionCard } from '../components/settings/AutomationPermissionCard';

export function SettingsPage() {
  const { data, loading, error } = useAsync(() => dataApi.settings(), []);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  async function requestExport() {
    setExporting(true);
    setExportMsg(null);
    try {
      const res = await dataApi.export({ scope: 'all' });
      setExportMsg(`导出已请求（ID ${res.export_id}）。完成后通过短期鉴权链接下载；该链接不会被 Service Worker 缓存。`);
    } catch (e) {
      setExportMsg(errorMessage(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <div className="page-head"><h2>设置与数据</h2></div>
      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{error}</div>}
      <AccountCard />
      {data && (
        <div className="card">
          <h3>系统配置状态</h3>
          <table>
            <tbody>
              <tr><td>模型供应商</td><td>{data.model_configured ? <span className="badge ok">已配置</span> : <span className="badge warn">未配置（付费调用关闭）</span>}</td></tr>
              <tr><td>OIDC 登录</td><td>{data.oidc_configured ? <span className="badge ok">已配置</span> : <span className="badge warn">未配置</span>}</td></tr>
              <tr><td>本地 dev-token</td><td>{data.local_dev_token_allowed ? <span className="badge warn">允许（仅 127.0.0.1）</span> : <span className="badge ok">已禁用</span>}</td></tr>
              <tr><td>数据域</td><td>{data.data_domains.join(', ') || '（无）'}</td></tr>
            </tbody>
          </table>
        </div>
      )}
      <ModelAccessCard />
      <AutomationPermissionCard />
      <div className="card">
        <h3 style={{ marginTop: 0 }}>导出与删除</h3>
        <p className="muted">导出为短期鉴权下载链接，不含系统密钥；私人原文不进入 Service Worker 缓存。</p>
        <button className="primary" onClick={() => void requestExport()} disabled={exporting}>
          {exporting ? '请求中…' : '请求导出我的数据'}
        </button>
        {exportMsg && <div className="notice info" style={{ marginTop: '0.6rem' }}>{exportMsg}</div>}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>隐私说明</h3>
        <p className="muted">
          Service Worker 仅预缓存静态外壳；API、私人聊天、导出/下载链接与令牌均不被缓存。离线时不可提交。登出会清理敏感客户端状态。
        </p>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* W8 · 账号卡（游客 / 注册 / 会员位）                                          */
/*                                                                            */
/* 三层账号的产品承诺在这里落地：                                                 */
/*   游客 = 默认态，工作台免登录全功能可用；                                       */
/*   注册 = 解锁云同步 / 社区 / 导出分享；                                        */
/*   会员 = **只留位**。v1 不接支付通道，因此本卡明确写「付费通道未开通」，       */
/*          不用任何「开通」「立即升级」这类会误导用户的按钮。                      */
/* 升级走的是同 id 就地 UPDATE，用户此前产生的数据全部保留（任务书 §4）。       */
/* -------------------------------------------------------------------------- */

function AccountCard() {
  // Optional read: a missing provider must not blank out the whole settings
  // page. When it is absent we say so instead of faking a session.
  const auth = useOptionalAuth();
  const isGuest = auth?.isGuest ?? false;
  const plan = auth?.plan ?? 'unknown';
  const refreshAuth = auth?.refresh;
  const { data, loading, error, reload } = useAsync(() => authApi.account(), []);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // 供「导出我的数据」等入口复登后刷新状态
  const reloadAll = useCallback(() => {
    reload();
    void refreshAuth?.();
  }, [reload, refreshAuth]);

  const planLabel = useMemo(() => {
    if (plan === 'pro') return 'Pro（会员位）';
    if (plan === 'free') return 'Free';
    return '未登记账号';
  }, [plan]);

  // All hooks above this line run unconditionally, per the Rules of Hooks.
  if (!auth || !auth.upgradeGuest) {
    return (
      <div className="card" id="account" data-testid="account-card">
        <h3 style={{ marginTop: 0 }}>账号</h3>
        <div className="notice danger" role="alert" data-testid="account-no-provider">
          账号信息不可用：当前页面未接入会话上下文（缺少 AuthProvider）。
        </div>
      </div>
    );
  }
  // Non-optional alias: TypeScript cannot narrow `auth.upgradeGuest` across the
  // function boundary of `submitUpgrade` below.
  const doUpgrade = auth.upgradeGuest;

  async function submitUpgrade(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    setMsg(null);
    if (!consent) {
      setFormError('需要先同意隐私政策才能完成注册。');
      return;
    }
    setSubmitting(true);
    try {
      await doUpgrade({
        email,
        password,
        consent_accepted: true,
        ...(displayName ? { display_name: displayName } : {}),
      });
      setPassword('');
      setEmail('');
      setConsent(false);
      setMsg('账号升级完成。此前你以游客身份产生的所有数据都保留在同一个账号下。');
      reloadAll();
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="card" id="account" data-testid="account-card">
      <h3 style={{ marginTop: 0 }}>账号</h3>

      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{error}</div>}

      {data && (
        <table>
          <tbody>
            <tr>
              <td>身份</td>
              <td data-testid="account-tier">
                {data.is_guest
                  ? <span className="badge warn">游客（免登录使用中）</span>
                  : <span className="badge ok">已注册</span>}
              </td>
            </tr>
            <tr><td>邮箱</td><td data-testid="account-email">{data.email || '—'}</td></tr>
            <tr><td>会员位</td><td data-testid="account-plan">{planLabel}</td></tr>
            <tr>
              <td>付费通道</td>
              <td data-testid="account-payment">
                {/* 诚实标注：v1 没有接支付，不做任何“立即开通”类误导入口。 */}
                {data.payment_enabled
                  ? <span className="badge ok">已开通</span>
                  : <span className="badge warn">未开通（v1 不接支付，会员位仅预留）</span>}
              </td>
            </tr>
          </tbody>
        </table>
      )}

      {isGuest && (
        <form onSubmit={(e) => void submitUpgrade(e)} style={{ marginTop: '1rem' }}>
          <h4>升级为正式账号</h4>
          <p className="muted">
            升级是<strong>同一个账号就地升级</strong>：游客期间创建的对话、知识库、
            小屋与画布都会完整保留，只需补一个邮箱和密码。
          </p>
          <div className="field">
            <label htmlFor="acc-email">邮箱</label>
            <input
              id="acc-email" data-testid="upgrade-email" type="email" required
              value={email} onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="acc-name">昵称（可选）</label>
            <input
              id="acc-name" data-testid="upgrade-name" value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="acc-password">密码（至少 8 位）</label>
            <input
              id="acc-password" data-testid="upgrade-password" type="password" required
              minLength={8} value={password} onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="acc-consent" style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <input
                id="acc-consent" data-testid="upgrade-consent" type="checkbox"
                checked={consent} onChange={(e) => setConsent(e.target.checked)}
              />
              <span>我已阅读并同意隐私政策</span>
            </label>
          </div>
          {formError && <div className="notice danger" role="alert" data-testid="upgrade-error">{formError}</div>}
          <div className="row">
            <button className="primary" type="submit" disabled={submitting} data-testid="upgrade-submit">
              {submitting ? '升级中…' : '升级为正式账号'}
            </button>
          </div>
        </form>
      )}

      {msg && <div className="notice info" style={{ marginTop: '0.6rem' }} data-testid="upgrade-msg">{msg}</div>}

      {!isGuest && !loading && (
        <p className="muted" style={{ marginTop: '0.8rem' }}>
          当前为正式账号，云同步 / 社区 / 导出分享已解锁。
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* W4 · 模型接入卡                                                             */
/*                                                                            */
/* v1 明确边界：页面**不做运行时热改配置**。表单只产生 (a) 本机浏览器草稿和      */
/* (b) 一段可直接粘贴到项目根目录 .env 的文本；配置生效必须写 .env 并重启服务。  */
/* 桌面端自动落盘由 W8（Tauri 壳）负责。                                        */
/* 密钥处理：**绝不写入 localStorage**，草稿只保留非密字段；输入框内的密钥只用于 */
/* 生成 .env 片段占位，不回显到界面文本中。                                     */
/* -------------------------------------------------------------------------- */

const DRAFT_STORAGE_KEY = 'fy.model-access.draft';

interface FallbackRow {
  provider: ProviderId;
  model: string;
}

interface ModelDraft {
  provider: ProviderId;
  baseUrl: string;
  model: string;
  fallbacks: FallbackRow[];
}

const PROVIDER_LABELS: Record<ProviderId, string> = {
  openai_compat: 'OpenAI 兼容接口',
  ollama: 'Ollama 本地模型',
  anthropic: 'Anthropic Messages API',
};

const PROVIDER_IDS: ProviderId[] = ['openai_compat', 'ollama', 'anthropic'];

function isProviderId(value: unknown): value is ProviderId {
  return typeof value === 'string' && (PROVIDER_IDS as string[]).includes(value);
}

function readDraft(): ModelDraft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ModelDraft>;
    if (!isProviderId(parsed.provider)) return null;
    return {
      provider: parsed.provider,
      baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : '',
      model: typeof parsed.model === 'string' ? parsed.model : '',
      fallbacks: Array.isArray(parsed.fallbacks)
        ? parsed.fallbacks
            .filter((row): row is FallbackRow => !!row && isProviderId(row.provider))
            .map((row) => ({ provider: row.provider, model: String(row.model ?? '') }))
        : [],
    };
  } catch {
    // 草稿损坏不应炸掉页面：静默回退到服务端当前配置。
    return null;
  }
}

function writeDraft(draft: ModelDraft): void {
  window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
}

function draftFromCatalog(catalog: ModelCatalogSummary): ModelDraft {
  const primary = catalog.chain.find((entry) => entry.source === 'primary');
  const provider = isProviderId(primary?.provider_id) ? primary.provider_id : 'openai_compat';
  return {
    provider,
    baseUrl: primary?.endpoint_ref ?? '',
    model: primary?.model || catalog.model_name || '',
    fallbacks: catalog.chain
      .filter((entry) => entry.source === 'fallback')
      .map((entry) => ({
        provider: isProviderId(entry.provider_id) ? entry.provider_id : 'ollama',
        model: entry.model || '',
      })),
  };
}

function normalizeUrl(value: string): string {
  return value.trim().replace(/\/+$/, '').toLowerCase();
}

/** 生成 .env 片段。密钥不回显：只给出占位提示，避免把 secret 写进 DOM。 */
function buildEnvSnippet(draft: ModelDraft, keyProvided: boolean): string {
  const lines = [
    `FY_MODEL_PROVIDER=${draft.provider}`,
    `FY_MODEL_BASE_URL=${draft.baseUrl || '（必填，例如 http://127.0.0.1:11434）'}`,
  ];
  const needsKey = draft.provider !== 'ollama';
  if (needsKey) {
    lines.push(`FY_MODEL_API_KEY=${keyProvided ? '（已在本页输入，出于安全不回显，请粘贴你自己的密钥）' : '（该厂商需要密钥）'}`);
  } else {
    lines.push('# Ollama 本地推理无需密钥');
  }
  if (draft.model) lines.push(`FY_MODEL_NAME=${draft.model}`);
  if (draft.fallbacks.length > 0) {
    const chain = draft.fallbacks
      .filter((row) => row.provider)
      .map((row) => ({ provider: row.provider, ...(row.model ? { model: row.model } : {}) }));
    lines.push(`FY_MODEL_FALLBACKS=${JSON.stringify(chain)}`);
  }
  return lines.join('\n');
}

function ModelAccessCard() {
  const { data: catalog, loading, error } = useAsync(() => modelsApi.catalog(false), []);
  const draft = useMemo(() => readDraft(), []);
  const [provider, setProvider] = useState<ProviderId>(draft?.provider ?? 'openai_compat');
  const [baseUrl, setBaseUrl] = useState(draft?.baseUrl ?? '');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState(draft?.model ?? '');
  const [fallbacks, setFallbacks] = useState<FallbackRow[]>(draft?.fallbacks ?? []);
  const [initialized, setInitialized] = useState(draft !== null);
  const [draftMsg, setDraftMsg] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ProviderHealthInfo | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);

  const serverProvider = useMemo<ModelDraft | null>(
    () => (catalog ? draftFromCatalog(catalog) : null),
    [catalog],
  );

  // 服务端已生效、而本地还没有草稿时，把当前真实配置回填表单，避免显示"空配置"误导。
  if (catalog && serverProvider && !initialized) {
    setInitialized(true);
    setProvider(serverProvider.provider);
    setBaseUrl(serverProvider.baseUrl);
    setModel(serverProvider.model);
    setFallbacks(serverProvider.fallbacks);
  }

  const requiresKey = provider !== 'ollama';
  const current: ModelDraft = { provider, baseUrl, model, fallbacks };
  const snippet = buildEnvSnippet(current, apiKey.length > 0);
  const liveTuple = catalog?.providers.find((p) => p.provider_id === provider);

  async function runProbe() {
    setProbing(true);
    setProbeResult(null);
    setProbeError(null);
    try {
      const report = await modelsApi.healthCheck({ providers: [provider], timeout_seconds: 4 });
      const hit = report.checks.find((c) => c.provider_id === provider) ?? report.checks[0] ?? null;
      if (!hit) {
        setProbeError(report.hint || '服务端没有可探测的已配置端点');
      } else {
        setProbeResult(hit);
      }
    } catch (e) {
      setProbeError(errorMessage(e));
    } finally {
      setProbing(false);
    }
  }

  function saveDraft() {
    writeDraft(current);
    setDraftMsg('草稿已保存到本机浏览器（不含密钥）。请按下方片段写入项目根目录 .env 并重启服务后生效——v1 不做运行时热改配置。');
  }

  function addFallback() {
    setFallbacks([...fallbacks, { provider: 'ollama', model: '' }]);
  }

  function updateFallback(index: number, patch: Partial<FallbackRow>) {
    setFallbacks(fallbacks.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function removeFallback(index: number) {
    setFallbacks(fallbacks.filter((_, i) => i !== index));
  }

  // 探测走的是服务端当前生效配置；与表单不一致时必须说明，不能假装测的就是表单里的地址。
  const probedEndpointMismatch =
    probeResult && normalizeUrl(probeResult.endpoint_ref) !== normalizeUrl(baseUrl);

  return (
    <div className="card model-access" data-testid="model-access-card">
      <h3 style={{ marginTop: 0 }}>模型接入</h3>
      <p className="muted">
        多 Provider 网关：主通道失败后按降级链切换，切换会在结果里如实标注，不静默换模型。
      </p>
      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{error}</div>}

      <div className="field">
        <label htmlFor="ma-provider">Provider</label>
        <select
          id="ma-provider"
          data-testid="provider-select"
          value={provider}
          onChange={(e) => setProvider(e.target.value as ProviderId)}
        >
          {PROVIDER_IDS.map((id) => (
            <option key={id} value={id}>{PROVIDER_LABELS[id]}</option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="ma-base-url">Base URL</label>
        <input
          id="ma-base-url"
          data-testid="base-url-input"
          value={baseUrl}
          placeholder={liveTuple?.default_base_url || 'https://api.openai.com/v1'}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </div>

      <div className="field">
        <label htmlFor="ma-api-key">API Key</label>
        <input
          id="ma-api-key"
          data-testid="api-key-input"
          type="password"
          autoComplete="off"
          value={apiKey}
          disabled={!requiresKey}
          placeholder={requiresKey ? '仅用于生成 .env 片段，不会上传也不会保存' : '本地推理无需密钥'}
          onChange={(e) => setApiKey(e.target.value)}
        />
        {!requiresKey && (
          <p className="muted" data-testid="no-key-note">Ollama 本地推理无需密钥，留空即可。</p>
        )}
      </div>

      <div className="field">
        <label htmlFor="ma-model">模型名</label>
        <input
          id="ma-model"
          data-testid="model-input"
          value={model}
          placeholder="gpt-4o-mini / qwen2.5:7b"
          onChange={(e) => setModel(e.target.value)}
        />
      </div>

      <div className="field">
        <div className="row spread">
          <span className="muted">降级链（主 provider 连续失败后依次尝试）</span>
          <button className="small" data-testid="fallback-add" onClick={addFallback}>+ 添加降级</button>
        </div>
        {fallbacks.length === 0 && <p className="muted" data-testid="fallback-empty">未配置降级链。</p>}
        {fallbacks.map((row, index) => (
          <div className="row fallback-row" key={index}>
            <select
              aria-label={`降级 ${index + 1} provider`}
              data-testid={`fallback-row-${index}-provider`}
              value={row.provider}
              onChange={(e) => updateFallback(index, { provider: e.target.value as ProviderId })}
            >
              {PROVIDER_IDS.map((id) => (
                <option key={id} value={id}>{PROVIDER_LABELS[id]}</option>
              ))}
            </select>
            <input
              aria-label={`降级 ${index + 1} 模型名`}
              data-testid={`fallback-row-${index}-model`}
              value={row.model}
              placeholder="qwen2.5:7b"
              onChange={(e) => updateFallback(index, { model: e.target.value })}
            />
            <button
              className="small danger"
              data-testid={`fallback-remove-${index}`}
              onClick={() => removeFallback(index)}
            >
              移除
            </button>
          </div>
        ))}
      </div>

      <div className="row">
        <button className="primary" data-testid="probe-button" disabled={probing} onClick={() => void runProbe()}>
          {probing ? '探测中…' : '测试连接'}
        </button>
        <button data-testid="save-draft" onClick={saveDraft}>保存草稿</button>
      </div>

      <p className="small-text">
        提示：探测发生在服务端，测的是它当前生效的配置，不是本页尚未写入 .env 的草稿。
      </p>

      {draftMsg && <div className="notice info" data-testid="draft-msg" style={{ marginTop: '0.6rem' }}>{draftMsg}</div>}

      {probeResult && (
        <div className="notice" data-testid="probe-result" style={{ marginTop: '0.6rem' }}>
          {probeResult.ok ? (
            <>
              <span className="badge ok">已连通</span>{' '}
              真实往返延迟 <strong data-testid="probe-latency">{probeResult.latency_ms ?? '—'}</strong> ms
              {probeResult.models.length > 0 && (
                <div className="small-text" data-testid="probe-models">
                  可用模型：{probeResult.models.join('、')}
                </div>
              )}
            </>
          ) : (
            <>
              <span className="badge danger">连接失败</span>{' '}
              <span data-testid="probe-error">{probeResult.error || '未返回原因'}</span>
            </>
          )}
          {probeResult.endpoint_ref && (
            <div className="small-text">探测端点：{probeResult.endpoint_ref}</div>
          )}
        </div>
      )}
      {probeError && (
        <div className="notice danger" role="alert" data-testid="probe-error-box" style={{ marginTop: '0.6rem' }}>
          {probeError}
        </div>
      )}
      {probedEndpointMismatch && (
        <div className="notice warn" data-testid="probe-mismatch" style={{ marginTop: '0.6rem' }}>
          服务端当前探测的是 <code>{probeResult.endpoint_ref}</code>，与表单填写的 <code>{baseUrl}</code> 不一致；
          请先把配置写入 .env 并重启服务后再测，否则探测结果不代表这份草稿。
        </div>
      )}

      <div className="field" style={{ marginTop: '0.9rem' }}>
        <label htmlFor="ma-env">生成的 .env 片段（复制后写入项目根目录 .env 并重启服务）</label>
        <textarea id="ma-env" data-testid="env-snippet" readOnly rows={6} value={snippet} />
      </div>
    </div>
  );
}
