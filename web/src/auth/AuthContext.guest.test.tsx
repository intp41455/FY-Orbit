import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AuthProvider, useAuth, type AuthState } from './AuthContext';
import { authApi } from '../api/auth';
import { ApiError } from '../api/client';
import type { GuestResponse, MeResponse } from '../api/types';

vi.mock('../api/auth', () => ({
  authApi: {
    me: vi.fn(),
    createGuest: vi.fn(),
    reclaimGuest: vi.fn(),
    upgradeGuest: vi.fn(),
    logout: vi.fn(),
    localDevToken: vi.fn(),
    account: vi.fn(),
  },
}));

const me = vi.mocked(authApi.me);
const createGuest = vi.mocked(authApi.createGuest);
const reclaimGuest = vi.mocked(authApi.reclaimGuest);
const upgradeGuest = vi.mocked(authApi.upgradeGuest);
const account = vi.mocked(authApi.account);

function guestPayload(over: Partial<GuestResponse> = {}): GuestResponse {
  return {
    subject_type: 'owner',
    status: 'guest',
    owner_id: 'own_guest_1',
    is_guest: true,
    plan: 'free',
    email: 'guest-4f2a@local',
    display_name: '',
    csrf_token: 'csrf-guest-1',
    ...over,
  };
}

function mePayload(over: Partial<MeResponse> = {}): MeResponse {
  return {
    subject_type: 'owner',
    owner_id: 'own_guest_1',
    service_id: '',
    service_kind: '',
    csrf_token: 'csrf-1',
    is_guest: true,
    plan: 'free',
    ...over,
  };
}

/** Probe that mirrors the session state into the DOM for assertions. */
function Probe() {
  const s: AuthState = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(s.loading)}</span>
      <span data-testid="owner">{s.owner ? s.owner.sub : 'none'}</span>
      <span data-testid="is-guest">{String(s.isGuest)}</span>
      <span data-testid="plan">{s.plan}</span>
      <span data-testid="account-email">{s.account ? s.account.email : 'none'}</span>
      <span data-testid="error">{s.error ?? ''}</span>
      <button type="button" data-testid="do-upgrade" onClick={() => {
        void s.upgradeGuest({
          email: 'jia@example.com',
          password: 'correct-horse',
          consent_accepted: true,
        });
      }}>升级</button>
    </div>
  );
}

function renderProvider() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  me.mockReset();
  createGuest.mockReset();
  reclaimGuest.mockReset();
  upgradeGuest.mockReset();
  account.mockReset();
  account.mockResolvedValue({
    email: 'guest-4f2a@local', is_guest: true, plan: 'free',
    display_name: '', status: 'guest', payment_enabled: false,
  });
});

describe('AuthContext · 游客自动登录（W8）', () => {
  it('me() 返回 401 时静默建游客会话，绝不跳登录页', async () => {
    me.mockRejectedValue(new ApiError(401, null, 'unauthorized'));
    createGuest.mockResolvedValue(guestPayload());

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'));
    expect(screen.getByTestId('owner')).toHaveTextContent('own_guest_1');
    expect(screen.getByTestId('is-guest')).toHaveTextContent('true');
    expect(screen.getByTestId('plan')).toHaveTextContent('free');
    expect(screen.getByTestId('error')).toHaveTextContent('');
    expect(createGuest).toHaveBeenCalledTimes(1);
  });

  it('me() 返回 403 同样走游客路径（未登录在本地是常态，不是错误）', async () => {
    me.mockRejectedValue(new ApiError(403, null, 'forbidden'));
    createGuest.mockResolvedValue(guestPayload());

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('is-guest')).toHaveTextContent('true'));
    expect(screen.getByTestId('error')).toHaveTextContent('');
  });

  it('已有游客会话时 me() 直接回报 is_guest/plan，不重复建号', async () => {
    me.mockResolvedValue(mePayload());
    createGuest.mockResolvedValue(guestPayload());

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('is-guest')).toHaveTextContent('true'));
    expect(screen.getByTestId('plan')).toHaveTextContent('free');
    expect(createGuest).not.toHaveBeenCalled();
  });

  it('游客被服务端拒绝时如实报错，不用假会话顶替', async () => {
    me.mockRejectedValue(new ApiError(401, null, 'unauthorized'));
    createGuest.mockRejectedValue(new ApiError(403, null, 'guest disabled'));

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('error')).not.toHaveTextContent(''));
    expect(screen.getByTestId('error')).toHaveTextContent('此环境未开放游客模式');
    expect(screen.getByTestId('owner')).toHaveTextContent('none');
    expect(screen.getByTestId('is-guest')).toHaveTextContent('false');
  });

  it('后端完全不可达时给出连接类错误（区别于「未登录」）', async () => {
    me.mockRejectedValue(new ApiError(500, null, 'boom'));

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('error')).not.toHaveTextContent(''));
    expect(screen.getByTestId('error')).toHaveTextContent('Could not reach the backend');
    expect(createGuest).not.toHaveBeenCalled();
  });

  it('升级成功后同一 owner 变为已注册，is_guest 翻假、plan 保持 free', async () => {
    me.mockResolvedValue(mePayload());
    upgradeGuest.mockResolvedValue(
      guestPayload({ is_guest: false, plan: 'free', email: 'jia@example.com', status: 'active' }),
    );

    renderProvider();
    await waitFor(() => expect(screen.getByTestId('is-guest')).toHaveTextContent('true'));

    screen.getByTestId('do-upgrade').click();

    await waitFor(() => expect(screen.getByTestId('is-guest')).toHaveTextContent('false'));
    // Same id in place: this is what "升级保数据" rests on.
    expect(screen.getByTestId('owner')).toHaveTextContent('own_guest_1');
    expect(upgradeGuest).toHaveBeenCalledWith({
      email: 'jia@example.com',
      password: 'correct-horse',
      consent_accepted: true,
    });
  });

  // Regression guard for a real W8 defect: the guest endpoints originally
  // omitted `subject_type`, so applyIdentity() silently produced owner=null and
  // guest bootstrap was a no-op in the browser. If someone drops the field from
  // GuestResponse again, this fails instead of shipping a broken免登录 path.
  it('游客端点响应必须带 subject_type，否则游客会话建不起来', async () => {
    me.mockRejectedValue(new ApiError(401, null, 'unauthorized'));
    createGuest.mockResolvedValue(
      // @ts-expect-error deliberately malformed — mirrors the shipped bug
      { status: 'guest', owner_id: 'own_x', is_guest: true, plan: 'free', email: '', display_name: '', csrf_token: '' },
    );

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'));
    expect(screen.getByTestId('owner')).toHaveTextContent('none');
  });

  it('R1.4：有访客标记时先找回原访客，不新建（同 id 不丢数据）', async () => {
    window.localStorage.setItem('fy.guest_id', 'own_old_guest');
    me.mockRejectedValue(new ApiError(401, null, 'unauthorized'));
    reclaimGuest.mockResolvedValue(guestPayload({ owner_id: 'own_old_guest' }));

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('owner')).toHaveTextContent('own_old_guest'));
    expect(reclaimGuest).toHaveBeenCalledWith('own_old_guest');
    expect(createGuest).not.toHaveBeenCalled();
  });

  it('R1.4：找回失败才新建；新建后写下访客标记', async () => {
    me.mockRejectedValue(new ApiError(401, null, 'unauthorized'));
    reclaimGuest.mockRejectedValue(new ApiError(404, null, 'gone'));
    window.localStorage.setItem('fy.guest_id', 'own_dead_guest');
    createGuest.mockResolvedValue(guestPayload({ owner_id: 'own_new_guest' }));

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('owner')).toHaveTextContent('own_new_guest'));
    expect(window.localStorage.getItem('fy.guest_id')).toBe('own_new_guest');
  });

  it('R2/R11：登录后拉取账号读模型供展示层使用', async () => {
    me.mockRejectedValue(new ApiError(401, null, 'unauthorized'));
    createGuest.mockResolvedValue(guestPayload());

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('account-email')).toHaveTextContent('guest-4f2a@local'));
    expect(account).toHaveBeenCalled();
  });
});
