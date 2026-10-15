import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/automation', () => ({
  fetchAutomationPermissions: vi.fn(),
  putAutomationPermissions: vi.fn(),
}));

import {
  fetchAutomationPermissions,
  putAutomationPermissions,
} from '../../api/automation';
import { AutomationPermissionCard } from './AutomationPermissionCard';

const fetchMock = fetchAutomationPermissions as unknown as ReturnType<typeof vi.fn>;
const putMock = putAutomationPermissions as unknown as ReturnType<typeof vi.fn>;

const VIEW = {
  mode: 'readonly',
  expires_at: null,
  ttl_remaining_seconds: null,
  max_ttl_seconds: 600,
  modes: ['off', 'readonly', 'safe', 'full'],
  honesty_note: '默认关闭',
};

describe('AutomationPermissionCard 关闭档', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockResolvedValue({ ...VIEW });
  });

  it('点“关闭”即 PUT off 并提示已关闭（关闭键不是摆设）', async () => {
    putMock.mockResolvedValue({ ...VIEW, mode: 'off' });
    render(<AutomationPermissionCard />);
    const radio = await screen.findByTestId('automation-mode-off');
    fireEvent.click(radio);
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith('off', undefined),
    );
    expect((await screen.findByTestId('automation-msg')).textContent).toContain('已关闭');
  });

  it('PUT 失败时如实报错（不静默吞掉）', async () => {
    putMock.mockRejectedValue(new Error('403 forbidden'));
    render(<AutomationPermissionCard />);
    fireEvent.click(await screen.findByTestId('automation-mode-off'));
    expect((await screen.findByTestId('automation-error')).textContent).toContain('403');
  });

  it('重复点已选中的档位给明确反馈且不发请求', async () => {
    fetchMock.mockResolvedValue({ ...VIEW, mode: 'off' });
    render(<AutomationPermissionCard />);
    fireEvent.click(await screen.findByTestId('automation-mode-off'));
    expect((await screen.findByTestId('automation-msg')).textContent).toContain('无需切换');
    expect(putMock).not.toHaveBeenCalled();
  });
});
