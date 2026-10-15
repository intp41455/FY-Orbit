/**
 * LoginPage 三问题回归：
 * 1. 无多余 BaseBound 包裹（登录页无可保存内容，状态指示器是噪音）；
 * 2. 口令框默认留空（不把有效密钥预填进 HTML）；
 * 3. 有注册入口（接通后端 POST /auth/register）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { LoginPage } from './LoginPage';

const mockAuth = {
  owner: null,
  loading: false,
  error: null,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  register: vi.fn(),
};

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

vi.mock('../brand', () => ({
  BRAND: { mark: 'X', productEn: 'Find Yourself', productZh: '星轨', slogan: 's', sloganSub: 'ss' },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockAuth.register.mockResolvedValue(undefined);
});

function renderPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe('LoginPage', () => {
  it('无 BaseBound 包裹', () => {
    const { container } = renderPage();
    expect(container.querySelector('[data-base-bound]')).toBeNull();
  });

  it('口令框默认留空且不泄露默认密钥', () => {
    renderPage();
    const input = screen.getByLabelText('本地访问口令') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input.placeholder).not.toContain('dev-token-secret');
    expect(document.body.innerHTML).not.toContain('dev-token-secret');
  });

  it('注册入口：填表勾选后调 register', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText('邮箱'), 'qa-new@test.local');
    await user.type(screen.getByLabelText(/密码/, { selector: 'input' }), 'QaRegis!2026#x');
    await user.click(screen.getByLabelText(/我已阅读并同意隐私政策/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
    await waitFor(() =>
      expect(mockAuth.register).toHaveBeenCalledWith({
        email: 'qa-new@test.local',
        password: 'QaRegis!2026#x',
        consent_accepted: true,
        display_name: undefined,
      }),
    );
  });

  it('未勾选同意时注册按钮保持禁用', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText('邮箱'), 'qa-new@test.local');
    await user.type(screen.getByLabelText(/密码/, { selector: 'input' }), 'QaRegis!2026#x');
    expect(screen.getByRole('button', { name: '注册并登录' })).toBeDisabled();
    expect(mockAuth.register).not.toHaveBeenCalled();
  });
});
