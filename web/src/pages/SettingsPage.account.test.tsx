import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SettingsPage } from './SettingsPage';
import { dataApi } from '../api/data';
import { modelsApi } from '../api/models';
import { authApi } from '../api/auth';
import { useOptionalAuth, type AuthState } from '../auth/AuthContext';
import type { AccountInfo } from '../api/types';

vi.mock('../api/data', () => ({
  dataApi: { settings: vi.fn(), export: vi.fn() },
}));

vi.mock('../api/models', () => ({
  modelsApi: { catalog: vi.fn(), healthCheck: vi.fn() },
}));

vi.mock('../api/auth', () => ({
  authApi: { account: vi.fn(), upgradeGuest: vi.fn() },
}));

vi.mock('../auth/AuthContext', () => ({
  useOptionalAuth: vi.fn(),
}));

const useAuthMock = vi.mocked(useOptionalAuth);
const account = vi.mocked(authApi.account);
const upgradeGuest = vi.mocked(authApi.upgradeGuest);

function authState(over: Partial<AuthState> = {}): AuthState {
  return {
    owner: { sub: 'own_guest_1', authenticated: true },
    loading: false,
    error: null,
    isGuest: true,
    plan: 'free',
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
    upgradeGuest: vi.fn(),
    ...over,
  };
}

const GUEST: AccountInfo = {
  email: 'guest-4f2a@local',
  is_guest: true,
  plan: 'free',
  display_name: '',
  status: 'guest',
  payment_enabled: false,
};

const EMPTY_CATALOG = {
  catalog_version: 'catalog-test',
  primary_provider_id: 'ollama',
  model_name: 'qwen2.5:7b',
  configured: false,
  config_error: '',
  route_errors: [],
  chain: [],
  providers: [],
  models: [],
} as never;

beforeEach(() => {
  window.localStorage.clear();
  useAuthMock.mockReturnValue(authState());
  account.mockResolvedValue(GUEST);
  upgradeGuest.mockReset();
  vi.mocked(dataApi.settings).mockResolvedValue({
    model_configured: false,
    oidc_configured: false,
    local_dev_token_allowed: true,
    data_domains: ['personal'],
  } as never);
  vi.mocked(modelsApi.catalog).mockResolvedValue(EMPTY_CATALOG);
  vi.mocked(modelsApi.healthCheck).mockResolvedValue({
    checked_at: '2026-10-04T00:00:00+00:00',
    primary_provider_id: 'ollama',
    config_error: '',
    probe_timeout_seconds: 4,
    checks: [],
    hint: '',
  } as never);
});

describe('设置页账号卡 · 账号三层（W8）', () => {
  it('展示游客身份、真实邮箱与 free 会员位', async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId('account-tier')).toHaveTextContent('游客'));
    expect(screen.getByTestId('account-email')).toHaveTextContent('guest-4f2a@local');
    expect(screen.getByTestId('account-plan')).toHaveTextContent('Free');
  });

  it('诚实标注付费通道未开通，且不出现任何“立即开通”类按钮', async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId('account-payment')).toBeInTheDocument());
    expect(screen.getByTestId('account-payment')).toHaveTextContent('未开通（v1 不接支付，会员位仅预留）');
    // v1 has no payment channel: no upsell affordance may exist.
    expect(screen.queryByRole('button', { name: /立即开通|开通会员|升级会员|购买/ })).not.toBeInTheDocument();
  });

  it('已注册账号不再渲染升级表单', async () => {
    useAuthMock.mockReturnValue(authState({ isGuest: false, plan: 'pro' }));
    account.mockResolvedValue({ ...GUEST, is_guest: false, plan: 'pro', email: 'jia@example.com', status: 'active' });

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId('account-tier')).toHaveTextContent('已注册'));
    expect(screen.getByTestId('account-plan')).toHaveTextContent('Pro（会员位）');
    expect(screen.queryByTestId('upgrade-submit')).not.toBeInTheDocument();
  });

  it('未勾选隐私政策同意时不发请求，并说明原因', async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId('upgrade-submit')).toBeInTheDocument());

    await user.type(screen.getByTestId('upgrade-email'), 'jia@example.com');
    await user.type(screen.getByTestId('upgrade-password'), 'correct-horse');
    await user.click(screen.getByTestId('upgrade-submit'));

    await waitFor(() => expect(screen.getByTestId('upgrade-error')).toHaveTextContent('同意隐私政策'));
    expect(upgradeGuest).not.toHaveBeenCalled();
  });

  it('升级成功后明确告知原数据保留（就地升级，不换账号）', async () => {
    const user = userEvent.setup();
    const upgrade = vi.fn().mockResolvedValue(undefined);
    useAuthMock.mockReturnValue(authState({ upgradeGuest: upgrade }));
    account.mockResolvedValue({ ...GUEST, is_guest: false, plan: 'free', email: 'jia@example.com', status: 'active' });

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId('upgrade-submit')).toBeInTheDocument());

    await user.type(screen.getByTestId('upgrade-email'), 'jia@example.com');
    await user.type(screen.getByTestId('upgrade-password'), 'correct-horse');
    await user.click(screen.getByTestId('upgrade-consent'));
    await user.click(screen.getByTestId('upgrade-submit'));

    await waitFor(() => expect(screen.getByTestId('upgrade-msg')).toHaveTextContent('所有数据都保留在同一个账号下'));
    expect(upgrade).toHaveBeenCalledWith({
      email: 'jia@example.com',
      password: 'correct-horse',
      consent_accepted: true,
    });
    // Password field must not linger in the DOM after a successful write.
    expect(screen.getByTestId('upgrade-password')).toHaveValue('');
  });

  it('升级失败展示后端返回的真实原因，不伪装成成功', async () => {
    const user = userEvent.setup();
    const upgrade = vi.fn().mockRejectedValue(
      Object.assign(new Error('邮箱已被占用'), { status: 409 }),
    );
    useAuthMock.mockReturnValue(authState({ upgradeGuest: upgrade }));

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId('upgrade-submit')).toBeInTheDocument());

    await user.type(screen.getByTestId('upgrade-email'), 'taken@example.com');
    await user.type(screen.getByTestId('upgrade-password'), 'correct-horse');
    await user.click(screen.getByTestId('upgrade-consent'));
    await user.click(screen.getByTestId('upgrade-submit'));

    await waitFor(() => expect(screen.getByTestId('upgrade-error')).toHaveTextContent('邮箱已被占用'));
    expect(screen.queryByTestId('upgrade-msg')).not.toBeInTheDocument();
  });

  // Regression guard: AccountCard originally called useAuth(), which throws
  // outside an AuthProvider and took the whole settings page down with it —
  // breaking the pre-existing W4 model-access tests. A page section must
  // degrade, not blank the page.
  it('缺少 AuthProvider 时账号卡降级提示，其余设置分区照常渲染', async () => {
    useAuthMock.mockReturnValue(null);

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByTestId('account-no-provider')).toBeInTheDocument());
    expect(screen.getByTestId('account-no-provider')).toHaveTextContent('未接入会话上下文');
    // The W4 card on the same page must still work.
    await waitFor(() => expect(screen.getByTestId('model-access-card')).toBeInTheDocument());
    expect(screen.getByText('系统配置状态')).toBeInTheDocument();
  });
});
