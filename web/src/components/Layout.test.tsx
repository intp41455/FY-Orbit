import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const authState: {
  owner: { sub: string; authenticated: boolean };
  loading: boolean;
  error: string | null;
  isGuest: boolean;
  plan: string;
  account: null | { email: string; display_name: string };
  login: ReturnType<typeof vi.fn>;
  loginWithToken: ReturnType<typeof vi.fn>;
  logout: ReturnType<typeof vi.fn>;
  refresh: ReturnType<typeof vi.fn>;
  upgradeGuest: ReturnType<typeof vi.fn>;
  register: ReturnType<typeof vi.fn>;
} = {
  owner: { sub: 'abc123', authenticated: true },
  loading: false,
  error: null as string | null,
  isGuest: false,
  plan: 'free',
  account: null as null | { email: string; display_name: string },
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refresh: vi.fn(),
  upgradeGuest: vi.fn(),
  register: vi.fn(),
};

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => authState,
  useOptionalAuth: () => null,
}));
vi.mock('./NotificationBell', () => ({ NotificationBell: () => null }));
vi.mock('./pet/DeskPetCompanion', () => ({ DeskPetCompanion: () => null }));
vi.mock('./ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./ui')>();
  return { ...actual, OfflineBadge: () => null };
});

import { Layout } from './Layout';

function renderLayout() {
  render(
    <MemoryRouter initialEntries={['/workbench']}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/workbench" element={<div>workbench</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('Layout 侧栏直达', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    authState.owner = { sub: 'abc123', authenticated: true };
    authState.isGuest = false;
    authState.account = null;
  });

  it('权限与沙箱隔离直达桌面自动化卡，预算与用量直达用量仪表页', () => {
    renderLayout();
    const perm = screen.getByRole('link', { name: /权限与沙箱隔离/ });
    const budget = screen.getByRole('link', { name: /预算与用量/ });
    expect(perm.getAttribute('href')).toBe('/settings#automation-permissions');
    expect(budget.getAttribute('href')).toBe('/observability');
  });
});

describe('Layout 身份统一（I-13 R2/R8）', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    authState.owner = { sub: 'abc123', authenticated: true };
    authState.isGuest = false;
    authState.account = null;
  });

  it('游客显示 guest-短号，title 与可见文本一致（不裸露完整 uuid）', () => {
    authState.owner = { sub: 'f24dee78d67846f090cc8deae71eeaea', authenticated: true };
    authState.isGuest = true;
    renderLayout();
    const badge = screen.getByTestId('sidebar-owner');
    expect(badge.textContent).toBe('guest-f24dee78');
    expect(badge.getAttribute('title')).toBe('guest-f24dee78');
    expect(badge.getAttribute('aria-label')).toBe('guest-f24dee78');
  });

  it('登录用户优先昵称，否则脱敏 email', () => {
    authState.account = { email: 'jia@example.com', display_name: 'Jia' };
    renderLayout();
    expect(screen.getByTestId('sidebar-owner').textContent).toBe('Jia');

    authState.account = { email: 'jia@example.com', display_name: '' };
    renderLayout();
    expect(screen.getAllByTestId('sidebar-owner').pop()?.textContent).toBe('j***@example.com');
  });
});

describe('Layout 登出二次确认（I-13 Q4）', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    authState.owner = { sub: 'abc123', authenticated: true };
    authState.isGuest = true;
    authState.account = null;
  });

  it('点登出先弹确认框，确认后才登出；取消不登出', () => {
    renderLayout();
    fireEvent.click(screen.getByRole('button', { name: '登出' }));
    expect(screen.getByText('退出并清除本地数据？')).toBeInTheDocument();
    expect(authState.logout).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(authState.logout).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '登出' }));
    fireEvent.click(screen.getByRole('button', { name: '退出并清除' }));
    expect(authState.logout).toHaveBeenCalledTimes(1);
  });
});
