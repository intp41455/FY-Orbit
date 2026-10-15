import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { SettingsPage } from './SettingsPage';
import { dataApi } from '../api/data';
import { modelsApi } from '../api/models';
import type { ModelCatalogSummary, ProviderHealthInfo } from '../api/models';

vi.mock('../api/data', () => ({
  dataApi: {
    settings: vi.fn(),
    export: vi.fn(),
  },
}));

vi.mock('../api/models', () => ({
  modelsApi: {
    catalog: vi.fn(),
    healthCheck: vi.fn(),
    credentialsStatus: vi.fn(),
    saveCredentials: vi.fn(),
    forgetCredentials: vi.fn(),
  },
}));

const SETTINGS = {
  model_configured: false,
  oidc_configured: false,
  local_dev_token_allowed: true,
  data_domains: ['personal'],
};

function providerRow(over: Partial<ModelCatalogSummary['providers'][number]> = {}) {
  return {
    provider_id: over.provider_id ?? 'openai_compat',
    name: 'OpenAI 兼容接口',
    requires_api_key: true,
    local_inference: false,
    default_base_url: 'https://api.openai.com/v1',
    health_path: '/models',
    configured: false,
    credential_configured: false,
    credential_ref: 'env:FY_MODEL_API_KEY',
    role: '' as const,
    model: '',
    endpoint_ref: '',
    health: null,
    note: '',
    ...over,
  };
}

const CATALOG: ModelCatalogSummary = {
  catalog_version: 'catalog-2026-10-02',
  primary_provider_id: 'ollama',
  model_name: 'qwen2.5:7b',
  configured: true,
  config_error: '',
  route_errors: [],
  chain: [
    {
      provider_id: 'ollama',
      name: 'Ollama 本地模型',
      model: 'qwen2.5:7b',
      source: 'primary',
      local_inference: true,
      endpoint_ref: 'http://127.0.0.1:11434',
    },
  ],
  providers: [
    providerRow({
      provider_id: 'ollama',
      name: 'Ollama 本地模型',
      requires_api_key: false,
      local_inference: true,
      default_base_url: 'http://127.0.0.1:11434',
      configured: true,
      role: 'primary',
      model: 'qwen2.5:7b',
      endpoint_ref: 'http://127.0.0.1:11434',
    }),
    providerRow(),
    providerRow({
      provider_id: 'anthropic',
      name: 'Anthropic Messages API',
      default_base_url: 'https://api.anthropic.com',
      health_path: '/v1/models',
    }),
  ],
  models: [],
};

function okCheck(over: Partial<ProviderHealthInfo> = {}): ProviderHealthInfo {
  return {
    provider_id: 'ollama',
    ok: true,
    latency_ms: 42,
    models: ['qwen2.5:7b'],
    error: '',
    endpoint_ref: 'http://127.0.0.1:11434',
    ...over,
  };
}

const HEALTH_OK: import('../api/models').HealthCheckReport = {
  checked_at: '2026-10-04T00:00:00+00:00',
  primary_provider_id: 'ollama',
  config_error: '',
  probe_timeout_seconds: 4,
  checks: [okCheck()],
  hint: '',
};

function snippetText(): string {
  return (screen.getByTestId('env-snippet') as HTMLTextAreaElement).value;
}

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(dataApi.settings).mockResolvedValue(SETTINGS as never);
  vi.mocked(modelsApi.catalog).mockResolvedValue(CATALOG);
  vi.mocked(modelsApi.healthCheck).mockResolvedValue(HEALTH_OK);
  vi.mocked(modelsApi.credentialsStatus).mockResolvedValue({
    configured: false, provider: '', base_url: '', model: '',
    has_api_key: false, storage: '', persist_restart: false,
  });
});

describe('SettingsPage 模型接入卡 (W4)', () => {
  it('渲染 provider 下拉并回填服务端当前生效配置', async () => {
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    expect(screen.getByTestId('base-url-input')).toHaveValue('http://127.0.0.1:11434');
    expect(screen.getByTestId('model-input')).toHaveValue('qwen2.5:7b');
    expect(screen.getByTestId('fallback-empty')).toBeInTheDocument();
  });

  it('切到 Ollama 时密钥输入禁用并说明无需密钥', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.selectOptions(screen.getByTestId('provider-select'), 'openai_compat');
    expect(screen.getByTestId('api-key-input')).toBeEnabled();
    await user.selectOptions(screen.getByTestId('provider-select'), 'ollama');
    expect(screen.getByTestId('api-key-input')).toBeDisabled();
    expect(screen.getByTestId('no-key-note')).toBeInTheDocument();
  });

  it('测试连接成功显示真实延迟与可用模型，不编造数字', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.click(screen.getByTestId('probe-button'));

    await waitFor(() => expect(screen.getByTestId('probe-result')).toBeInTheDocument());
    expect(screen.getByTestId('probe-latency')).toHaveTextContent('42');
    expect(screen.getByTestId('probe-models')).toHaveTextContent('qwen2.5:7b');
    expect(modelsApi.healthCheck).toHaveBeenCalledWith({ providers: ['ollama'], timeout_seconds: 4 });
  });

  it('测试连接失败展示后端返回的真实错误原文', async () => {
    vi.mocked(modelsApi.healthCheck).mockResolvedValue({
      ...HEALTH_OK,
      checks: [okCheck({ ok: false, latency_ms: null, models: [], error: 'transport(连接被拒绝)' })],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.click(screen.getByTestId('probe-button'));

    await waitFor(() => expect(screen.getByTestId('probe-error')).toHaveTextContent('transport(连接被拒绝)'));
    expect(screen.queryByTestId('probe-latency')).not.toBeInTheDocument();
  });

  it('探测端点与表单不一致时给出警示而不是假装测的是草稿', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.clear(screen.getByTestId('base-url-input'));
    await user.type(screen.getByTestId('base-url-input'), 'http://192.168.1.9:11434');
    await user.click(screen.getByTestId('probe-button'));

    await waitFor(() => expect(screen.getByTestId('probe-mismatch')).toBeInTheDocument());
    expect(screen.getByTestId('probe-mismatch')).toHaveTextContent('http://127.0.0.1:11434');
  });

  it('无已配置端点时展示后端 hint 的指引', async () => {
    vi.mocked(modelsApi.healthCheck).mockResolvedValue({
      ...HEALTH_OK,
      checks: [],
      hint: '尚未检测到已配置的模型端点：请设置 FY_MODEL_BASE_URL',
    });
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.click(screen.getByTestId('probe-button'));

    await waitFor(() => expect(screen.getByTestId('probe-error-box')).toHaveTextContent('FY_MODEL_BASE_URL'));
  });

  it('添加/移除降级链会同步到 .env 片段', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.click(screen.getByTestId('fallback-add'));
    await user.type(screen.getByTestId('fallback-row-0-model'), 'qwen2.5:7b');

    const snippet = snippetText();
    expect(snippet).toContain('FY_MODEL_PROVIDER=ollama');
    expect(snippet).toContain('FY_MODEL_FALLBACKS=[{"provider":"ollama","model":"qwen2.5:7b"}]');

    await user.click(screen.getByTestId('fallback-remove-0'));
    expect(snippetText()).not.toContain('FY_MODEL_FALLBACKS');
  });

  it('保存草稿写入 localStorage 但绝不写入密钥', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.selectOptions(screen.getByTestId('provider-select'), 'openai_compat');
    await user.type(screen.getByTestId('api-key-input'), 'sk-super-secret');
    await user.click(screen.getByTestId('save-draft'));

    await waitFor(() => expect(screen.getByTestId('draft-msg')).toBeInTheDocument());
    const stored = window.localStorage.getItem('fy.model-access.draft') ?? '';
    expect(stored).toContain('openai_compat');
    expect(stored).not.toContain('sk-super-secret');
    expect(snippetText()).not.toContain('sk-super-secret');
  });

  it('远程 provider 的片段必须带 FY_OFFLINE_MODE=0 与单价声明（两个必踩坑）', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));
    await user.selectOptions(screen.getByTestId('provider-select'), 'openai_compat');
    await user.type(screen.getByTestId('base-url-input'), 'https://api.example.com');
    await user.type(screen.getByTestId('model-input'), 'gpt-4o-mini');

    const snippet = snippetText();
    expect(snippet).toContain('FY_OFFLINE_MODE=0');
    // 单价未声明时网关按 PriceUnknown 阻断，片段不得把用户带进这个坑
    expect(snippet).toContain('FY_MODEL_PRICE_OVERRIDES=');
  });

  it('纯本机 ollama 的片段保持在离线模式（不外传数据）', async () => {
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('base-url-input')).toHaveValue(''));

    const snippet = snippetText();
    expect(snippet).not.toContain('FY_OFFLINE_MODE=0');
    expect(snippet).toContain('FY_OFFLINE_MODE=1');
  });

  it('系统配置状态卡仍然渲染（回归）', async () => {
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('系统配置状态')).toBeInTheDocument());
    expect(screen.getByText('未配置（付费调用关闭）')).toBeInTheDocument();
  });

  it('D06：保存到服务端调 saveCredentials，成功后清空密钥框并显示状态', async () => {
    const user = userEvent.setup();
    vi.mocked(modelsApi.saveCredentials).mockResolvedValue({
      configured: true, provider: 'openai_compat', base_url: 'https://api.example.com/v1',
      model: 'gpt-4o-mini', has_api_key: true, storage: 'hub_fernet', persist_restart: true,
    });
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('provider-select')).toHaveValue('ollama'));

    await user.selectOptions(screen.getByTestId('provider-select'), 'openai_compat');
    await user.type(screen.getByTestId('base-url-input'), 'https://api.example.com/v1');
    await user.type(screen.getByTestId('api-key-input'), 'sk-test-123');
    await user.click(screen.getByTestId('save-credentials'));

    await waitFor(() =>
      expect(modelsApi.saveCredentials).toHaveBeenCalledWith({
        provider: 'openai_compat',
        base_url: expect.stringContaining('https://api.example.com/v1'),
        api_key: expect.stringContaining('sk-test-123'),
        model: expect.anything(),
      }),
    );
    // 密钥框已清空（secret 不在 DOM state 久留），状态行显示已存。
    await waitFor(() =>
      expect(screen.getByTestId('api-key-input')).toHaveValue(''),
    );
    expect(screen.getByTestId('credentials-status')).toHaveTextContent('服务端已存');
    expect(screen.getByTestId('credentials-status')).toHaveTextContent('密钥已加密保存');
  });
});
