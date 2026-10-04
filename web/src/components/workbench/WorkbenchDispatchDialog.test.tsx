/**
 * P1 交互双件 · 任务二测试：WorkbenchDispatchDialog
 * 覆盖：对话框开合、两条真实通道的提交 payload、失败展示错误 envelope 原文。
 * 网络层全部 mock（vi.mock），组件自身不发明成功状态。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WorkbenchDispatchDialog } from './WorkbenchDispatchDialog';
import { ApiError } from '../../api/client';

vi.mock('../../api/teams', () => ({
  teamsApi: {
    list: vi.fn(), get: vi.fn(), start: vi.fn(), executeMember: vi.fn(), events: vi.fn(),
  },
}));
vi.mock('../../api/tasks', () => ({
  tasksApi: { create: vi.fn(), get: vi.fn(), cancel: vi.fn() },
}));

import { teamsApi } from '../../api/teams';
import { tasksApi } from '../../api/tasks';
import type { TaskSummary } from '../../api/types';
import type { TeamSnapshot } from '../../api/teams';

const CTX = { path: 'src/app.ts', snippet: 'const a = 1;', line: 12 };

const TEAM = {
  id: 'team-1',
  name: 'dev-team',
  mode: 'product_native' as const,
  state: 'running',
  version: 3,
  plan_version: 1,
  root_task_id: null,
  member_roles: ['planner', 'coder'],
};

function member(over: Record<string, unknown> = {}) {
  return {
    agent_instance_id: 'ai-1',
    role: 'coder',
    title: '工程师',
    agent_host: 'local',
    provider_id: 'mock-provider',
    session_id: 'sess-1',
    independent_session: true,
    state: 'running',
    blocked_reason: '',
    requested_model: 'mock-deterministic',
    effective_model: '未执行',
    effective_confidence: 'exact',
    inherited_from: 'default',
    credential_ref: 'cred/main',
    credential_configured: true,
    depends_on: [],
    run_batch: 3,
    current_goal: '',
    plan_version: 1,
    subtask_id: null,
    budget_reserved_usd: 0,
    budget_spent_usd: 0,
    version: 2,
    control: { disabled_operations: [], supports_per_member_model: true },
    capability_recorded: true,
    updated_at: null,
    ...over,
  };
}

function snap(state: string, version = 3): TeamSnapshot {
  return {
    team: {
      id: 'team-1', name: 'dev-team', mode: 'product_native', state, version,
      plan_version: 1, root_task_id: null, canvas_instance_id: null,
      coordinator_role: 'planner', default_binding: {}, budget_ref: {},
      permission_ref: {}, members: [], last_change_reason: '', last_changed_by: '',
      real_model_configured: true, started_at: null,
    },
    members: [
      member({ role: 'planner', title: '规划者', state: 'blocked' }),
      member(),
    ],
    events: [],
    validation: {
      team_id: 'team-1', version, state, can_start: true,
      blockers: [], warnings: [], real_model_configured: true,
    },
  } as unknown as TeamSnapshot;
}

const EXEC = {
  role: 'coder',
  session_id: 'sess-9',
  text: '已完成改写',
  requested_model: 'mock-deterministic',
  effective_model: 'mock-deterministic',
  effective_confidence: 'exact',
  usage: { input_tokens: 10, output_tokens: 20 },
  settled_usd: '0.000000',
  run_batch: 4,
};

const TASK: TaskSummary = {
  id: 'task-9', root_id: null, parent_id: null, goal: 'x', mode: 'engineering',
  state: 'queued', stage: 'requirements', depth: 0, steps: 0, max_steps: 8,
  idempotency_key: 'idem-12345678',
  created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  // 默认链路 mock：list → 一个 running 团队；get → 成员快照。各用例按需覆盖。
  vi.mocked(teamsApi.list).mockResolvedValue({ items: [TEAM], count: 1 });
  vi.mocked(teamsApi.get).mockResolvedValue(snap('running'));
});

describe('WorkbenchDispatchDialog 开合', () => {
  it('渲染对话框与目标代码上下文，关闭按钮触发 onClose', async () => {
    vi.mocked(teamsApi.list).mockResolvedValue({ items: [TEAM], count: 1 });
    const onClose = vi.fn();
    render(<WorkbenchDispatchDialog context={CTX} onClose={onClose} />);

    expect(await screen.findByRole('dialog', { name: '派发给 Agent' })).toBeInTheDocument();
    const ctxBox = await screen.findByTestId('wb-dispatch-context');
    expect(ctxBox).toHaveTextContent('src/app.ts');
    expect(ctxBox).toHaveTextContent('第 12 行');
    expect(ctxBox).toHaveTextContent('const a = 1;');

    await userEvent.click(screen.getByTestId('wb-dispatch-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('WorkbenchDispatchDialog 提交 payload', () => {
  it('任务管线通道：payload 含需求+片段引用+所选模式，回执展示真实 task_id', async () => {
    vi.mocked(teamsApi.list).mockResolvedValue({ items: [], count: 0 });
    vi.mocked(tasksApi.create).mockResolvedValue(TASK);
    vi.mocked(tasksApi.get).mockResolvedValue(TASK);
    const user = userEvent.setup();
    render(<WorkbenchDispatchDialog context={CTX} onClose={vi.fn()} />);

    // 无团队时如实提示，任务通道仍可用
    expect(await screen.findByTestId('wb-dispatch-no-team')).toBeInTheDocument();
    await user.selectOptions(screen.getByTestId('wb-dispatch-executor-kind'), 'task');
    await user.selectOptions(screen.getByTestId('wb-dispatch-mode'), 'engineering');
    await user.type(screen.getByTestId('wb-dispatch-requirement'), '把这个函数改成异步');
    await user.click(screen.getByTestId('wb-dispatch-submit'));

    await waitFor(() => expect(tasksApi.create).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(tasksApi.create).mock.calls[0][0];
    expect(payload.mode).toBe('engineering');
    expect(payload.goal).toContain('把这个函数改成异步');
    expect(payload.goal).toContain('src/app.ts 第 12 行');
    expect(payload.goal).toContain('const a = 1;');
    expect(payload.idempotency_key.length).toBeGreaterThanOrEqual(8);

    expect(await screen.findByTestId('wb-dispatch-receipt')).toHaveTextContent('task-9');
  });

  it('成员通道：draft 团队先 start 冻结绑定，再 executeMember 到所选角色', async () => {
    const draft = { ...TEAM, state: 'draft', version: 1 };
    vi.mocked(teamsApi.list).mockResolvedValue({ items: [draft], count: 1 });
    vi.mocked(teamsApi.get).mockResolvedValue(snap('draft', 1));
    vi.mocked(teamsApi.start).mockResolvedValue(snap('running'));
    vi.mocked(teamsApi.executeMember).mockResolvedValue(EXEC);
    const user = userEvent.setup();
    render(<WorkbenchDispatchDialog context={CTX} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId('wb-dispatch-team')).toHaveValue('team-1'));
    const roleSel = screen.getByTestId('wb-dispatch-role');
    await waitFor(() => expect(roleSel).toBeEnabled());
    await user.selectOptions(roleSel, 'coder');
    await user.type(screen.getByTestId('wb-dispatch-requirement'), '给函数加重试');
    await user.click(screen.getByTestId('wb-dispatch-submit'));

    await waitFor(() => expect(teamsApi.executeMember).toHaveBeenCalledTimes(1));
    expect(teamsApi.start).toHaveBeenCalledWith('team-1', 1);
    expect(teamsApi.executeMember).toHaveBeenCalledWith(
      'team-1',
      expect.objectContaining({
        role: 'coder',
        prompt: expect.stringContaining('const a = 1;'),
      }),
    );
    const receipt = screen.getByTestId('wb-dispatch-receipt');
    expect(receipt).toHaveTextContent('run_batch：4');
    expect(receipt).toHaveTextContent('mock-deterministic');
  });

  it('失败时展示错误 envelope 原文（code/message），不显示成功回执', async () => {
    vi.mocked(teamsApi.list).mockResolvedValue({ items: [TEAM], count: 1 });
    vi.mocked(teamsApi.get).mockResolvedValue(snap('running'));
    vi.mocked(teamsApi.executeMember).mockRejectedValue(
      new ApiError(
        409,
        { code: 'binding_not_frozen', message: 'Member has no frozen model binding', details: {} },
        'Request failed with status 409',
      ),
    );
    const user = userEvent.setup();
    render(<WorkbenchDispatchDialog context={CTX} onClose={vi.fn()} />);

    const roleSel = await screen.findByTestId('wb-dispatch-role');
    await waitFor(() => expect(roleSel).toBeEnabled());
    await user.selectOptions(roleSel, 'coder');
    await user.type(screen.getByTestId('wb-dispatch-requirement'), '改掉这个 bug');
    await user.click(screen.getByTestId('wb-dispatch-submit'));

    const err = await screen.findByTestId('wb-dispatch-error');
    expect(err).toHaveTextContent('binding_not_frozen');
    expect(err).toHaveTextContent('Member has no frozen model binding');
    expect(screen.queryByTestId('wb-dispatch-receipt')).not.toBeInTheDocument();
  });
});
