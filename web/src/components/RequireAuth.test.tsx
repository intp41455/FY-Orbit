import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RequireAuth, RequireAccount } from './RequireAuth';
import { useAuth, type AuthState } from '../auth/AuthContext';

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const useAuthMock = vi.mocked(useAuth);

function state(over: Partial<AuthState> = {}): AuthState {
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

function renderAuth(node: React.ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>);
}

beforeEach(() => {
  useAuthMock.mockReset();
});

describe('RequireAuth · 能不能进应用（W8）', () => {
  it('游客会话下直接放行工作台（免登录可用）', () => {
    useAuthMock.mockReturnValue(state({ isGuest: true }));
    renderAuth(<RequireAuth><span data-testid="workbench">工作台</span></RequireAuth>);
    expect(screen.getByTestId('workbench')).toBeInTheDocument();
  });

  it('后端不可达时如实报错并给重试，绝不静默放行到白屏', () => {
    useAuthMock.mockReturnValue(state({ owner: null, error: '无法连接后端以创建本地游客会话，请检查服务是否在运行。' }));
    renderAuth(<RequireAuth><span data-testid="workbench">工作台</span></RequireAuth>);
    expect(screen.queryByTestId('workbench')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('无法连接后端');
    expect(screen.getByRole('button', { name: '重试连接' })).toBeInTheDocument();
  });

  it('无会话且无游客时说明原因，而不是跳一个救不了的登录页', () => {
    useAuthMock.mockReturnValue(state({ owner: null, isGuest: false, error: null }));
    renderAuth(<RequireAuth><span data-testid="workbench">工作台</span></RequireAuth>);
    expect(screen.queryByTestId('workbench')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('无法自动创建本地游客会话');
  });
});

describe('RequireAccount · 增值入口门禁（W8）', () => {
  it('游客被拦下并引导去设置页升级（不进/登录）', () => {
    useAuthMock.mockReturnValue(state({ isGuest: true }));
    renderAuth(
      <RequireAccount>
        <span data-testid="cloud-sync">云同步</span>
      </RequireAccount>,
    );
    expect(screen.queryByTestId('cloud-sync')).not.toBeInTheDocument();
    const gate = screen.getByTestId('require-account-guest');
    expect(gate).toHaveTextContent('该功能需要正式账号');
    // Must state the data-retention promise — that is why upgrading is safe.
    expect(gate).toHaveTextContent('升级后你现在产生的所有数据都会保留');
    expect(gate.querySelector('a')).toHaveAttribute('href', '/settings#account');
  });

  it('已注册账号（free 也会员位）直接放行增值入口', () => {
    useAuthMock.mockReturnValue(state({ isGuest: false, plan: 'free' }));
    renderAuth(
      <RequireAccount>
        <span data-testid="cloud-sync">云同步</span>
      </RequireAccount>,
    );
    expect(screen.getByTestId('cloud-sync')).toBeInTheDocument();
    expect(screen.queryByTestId('require-account-guest')).not.toBeInTheDocument();
  });

  it('自定义标题/说明会被渲染（供各入口写清为何要账号）', () => {
    useAuthMock.mockReturnValue(state({ isGuest: true }));
    renderAuth(
      <RequireAccount title="社区需要账号" description="社区内容对所有人可见，发布需注册。">
        <span data-testid="community">社区</span>
      </RequireAccount>,
    );
    expect(screen.getByTestId('require-account-guest')).toHaveTextContent('社区内容对所有人可见，发布需注册。');
  });

  it('门禁在 loading 期间显示校验态而不是闪现受保护内容', () => {
    useAuthMock.mockReturnValue(state({ loading: true }));
    renderAuth(
      <RequireAccount>
        <span data-testid="cloud-sync">云同步</span>
      </RequireAccount>,
    );
    expect(screen.queryByTestId('cloud-sync')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('正在校验账号');
  });
});
