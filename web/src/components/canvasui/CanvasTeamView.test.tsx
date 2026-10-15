import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/teams', () => ({
  teamsApi: {
    catalog: vi.fn(), list: vi.fn(), get: vi.fn(), events: vi.fn(),
    create: vi.fn(), start: vi.fn(), update: vi.fn(), resolveBinding: vi.fn(),
    setMemberBinding: vi.fn(), control: vi.fn(),
  },
}));
vi.mock('../../hooks/useAutosave', () => ({ useBase: () => {} }));

import { teamsApi } from '../../api/teams';
import { CanvasTeamView } from './CanvasTeamView';

const api = teamsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const CATALOG = {
  templates: [{ id: 't-eng', name: '工程团队', mode: 'system_managed', members: ['a'] }],
  models: [],
  hosts: [],
  real_model_configured: false,
};

function summary(id: string, name: string) {
  return {
    id, name, mode: 'system_managed', state: 'draft', version: 1,
    plan_version: 1, root_task_id: null, member_roles: [],
  };
}

describe('CanvasTeamView 同名团队页签', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom 无 matchMedia：按宽屏桩（抽屉浮层形态，不影响页签断言）。
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: () => ({
        matches: false, addEventListener: () => {}, removeEventListener: () => {},
      }),
    });
    api.catalog.mockResolvedValue(CATALOG);
    api.get.mockRejectedValue(new Error('no team'));
    api.events.mockResolvedValue({ items: [], next_cursor: 0 });
  });

  it('重名页签后缀 id 前 6 位区分，不重名保持干净', async () => {
    api.list.mockResolvedValue({
      items: [
        summary('abcdef123456', '工程团队'),
        summary('123456abcdef', '工程团队'),
        summary('zzzzzz000000', '研究团队'),
      ],
      count: 3,
    });
    render(<CanvasTeamView />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /工程团队 · abcdef/ })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /工程团队 · 123456/ })).toBeInTheDocument();
    // 不重名的不加后缀。
    const solo = screen.getByRole('button', { name: /^研究团队/ });
    expect(solo.textContent).not.toContain('·');
  });

  it('图例按钮可开关状态图例（非摆设）', async () => {
    const snap = {
      team: {
        id: 't-legend', name: '图例团队', mode: 'system_managed', state: 'draft',
        version: 1, plan_version: 1, root_task_id: null, canvas_instance_id: null,
        coordinator_role: 'coordinator',
        default_binding: {}, budget_ref: {}, permission_ref: {},
        members: [{ role: 'coordinator', title: '协调者', agent_host: 'find_yourself', depends_on: [] }],
        last_change_reason: '', last_changed_by: '', real_model_configured: false,
        started_at: null,
      },
      members: [],
      events: [],
      validation: {
        team_id: 't-legend', version: 1, state: 'draft', can_start: true,
        blockers: [], warnings: [], real_model_configured: false,
      },
    };
    api.list.mockResolvedValue({
      items: [{ ...summary('t-legend', '图例团队') }],
      count: 1,
    });
    api.get.mockResolvedValue(snap);
    render(<CanvasTeamView />);
    const btn = await screen.findByRole('button', { name: '图例' });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(screen.getByText('已完成')).toBeInTheDocument();
    });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(screen.queryByText('已完成')).not.toBeInTheDocument();
    });
  });
});
