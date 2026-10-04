import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, configure } from '@testing-library/react';

// 并行全量跑（47 文件）时默认 1s 断言预算不够——主控纠错 2026-10-04
configure({ asyncUtilTimeout: 5000 });
import userEvent from '@testing-library/user-event';
import { TeamDesigner } from './TeamDesigner';
import type {
  TeamCatalog,
  TeamSnapshot,
} from '../../api/teams';

vi.mock('../../api/teams', () => ({
  teamsApi: {
    catalog: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    validate: vi.fn(),
    start: vi.fn(),
    update: vi.fn(),
    resolveBinding: vi.fn(),
    setMemberBinding: vi.fn(),
    setRoleBinding: vi.fn(),
    updateGoal: vi.fn(),
    control: vi.fn(),
    reserveBudget: vi.fn(),
    reportResult: vi.fn(),
    events: vi.fn(),
  },
}));

import { teamsApi } from '../../api/teams';

const catalog: TeamCatalog = {
  catalog_version: 'catalog-test',
  real_model_configured: false,
  templates: [
    { id: 'engineering', name: '工程交付 · 规划 / 编码 / 审查', mode: 'system_managed', members: ['coordinator', 'implementer'] },
  ],
  hosts: [
    {
      agent_host: 'find_yourself', provider_id: 'local-synthetic',
      capabilities: {
        spawn: 'verified', events: 'verified', pause: 'verified', resume: 'verified',
        reassign: 'verified', rework: 'verified', cancel: 'verified',
        switch_model: 'verified', usage: 'verified', checkpoint: 'verified',
      },
      supports_per_member_model: true, usage_metering: 'verified',
      probe_source: 'self_managed', reason: '本服务自管', disabled_operations: [],
    },
    {
      agent_host: 'external_a2a', provider_id: '',
      capabilities: {
        spawn: 'unknown', events: 'verified', pause: 'unknown', resume: 'unknown',
        reassign: 'unknown', rework: 'unknown', cancel: 'unknown',
        switch_model: 'unsupported', usage: 'unsupported', checkpoint: 'unknown',
      },
      supports_per_member_model: false, usage_metering: 'unsupported',
      probe_source: 'static_registry', reason: '逐成员模型覆盖：待核验；本预览禁用。',
      disabled_operations: ['spawn', 'pause', 'switch_model'],
    },
  ],
  providers: [
    {
      provider_id: 'local-synthetic', name: '本地确定性提供方（合成）',
      credential_configured: true, credential_ref: 'env:FY_MODEL_API_KEY',
      synthetic: true, endpoint_ref: 'inprocess://mock-deterministic',
      note: '仅用于合成验证', models: ['mock-deterministic'],
    },
  ],
  models: [
    {
      provider_id: 'local-synthetic', model_id: 'mock-deterministic',
      credential_configured: true, credential_ref: 'env:FY_MODEL_API_KEY',
      pricing_status: 'known', context_window: 16384, synthetic: true,
      supports_tools: false, supports_structured_output: true, supports_streaming: false,
      max_output_tokens: 4096, endpoint_ref: 'inprocess://mock-deterministic',
      note: '确定性本地提供方',
    },
  ],
};

function member(over: Partial<TeamSnapshot['members'][number]>) {
  return {
    agent_instance_id: `agt-${over.role}`,
    role: 'implementer', title: '编码专家', agent_host: 'find_yourself',
    provider_id: 'local-synthetic', session_id: `team-t1-implementer-abc`,
    independent_session: true, state: 'running' as const, blocked_reason: '',
    requested_model: 'mock-deterministic', effective_model: '未执行',
    effective_confidence: 'not_executed', inherited_from: 'team',
    credential_ref: 'env:FY_MODEL_API_KEY', credential_configured: true,
    depends_on: ['coordinator'], run_batch: 1, current_goal: '实现脱敏',
    plan_version: 1, subtask_id: 'sub-1', budget_reserved_usd: 0.01,
    budget_spent_usd: 0, version: 1,
    control: { disabled_operations: [], supports_per_member_model: true },
    capability_recorded: true, updated_at: null,
    ...over,
  };
}

const snapshot: TeamSnapshot = {
  team: {
    id: 'team-t1', name: '工程团队', mode: 'system_managed', state: 'running',
    version: 1, plan_version: 1, root_task_id: 'root-team-t1', canvas_instance_id: null,
    coordinator_role: 'coordinator', default_binding: { model_id: 'mock-deterministic' },
    budget_ref: { root_budget_usd: 0.5, member_reserve_cap_usd: 0.05 },
    permission_ref: {}, members: [{ role: 'coordinator' }, { role: 'implementer' }],
    last_change_reason: 'acceptance', last_changed_by: 'owner-1',
    real_model_configured: false, started_at: null,
  },
  members: [
    member({ role: 'coordinator', agent_instance_id: 'agt-coordinator', session_id: 'team-t1-coordinator-zz' }),
    member({}),
  ],
  events: [
    { seq: 1, event_type: 'team.started', task_id: null, agent_instance_id: null,
      run_batch: null, source: 'service', details: {}, evidence_refs: [],
      created_at: '2026-10-02T00:00:00Z' },
  ],
  validation: {
    team_id: 'team-t1', version: 1, state: 'running', can_start: true,
    blockers: [], warnings: [], real_model_configured: false,
  },
};

/** The member node is a button; the inspector also shows the same title. */
async function clickMemberNode(name: string) {
  const btn = await screen.findByRole('button', { name: new RegExp(name) }, { timeout: 5000 });
  await userEvent.setup().click(btn);
  return btn;
}

function setup(over: Partial<TeamSnapshot> = {}) {
  const snap = { ...snapshot, ...over };
  vi.mocked(teamsApi.catalog).mockResolvedValue(catalog);
  vi.mocked(teamsApi.list).mockResolvedValue({
    items: [{ id: 'team-t1', name: '工程团队', mode: 'system_managed', state: 'running',
      version: 1, plan_version: 1, root_task_id: 'root-team-t1',
      member_roles: ['coordinator', 'implementer'] }],
    count: 1,
  });
  vi.mocked(teamsApi.get).mockResolvedValue(snap);
  vi.mocked(teamsApi.events).mockResolvedValue({ items: snap.events, count: 1, next_cursor: 1 });
  vi.mocked(teamsApi.resolveBinding).mockResolvedValue({
    role: 'implementer', requested_model: 'mock-deterministic',
    requested_provider: 'local-synthetic', inherited_from: 'team',
    chain: [{ scope: 'team', model_id: 'mock-deterministic' }],
    params: {},
  });
}

describe('TeamDesigner (19 号团队画布)', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders each member with its own independent session', async () => {
    setup();
    render(<TeamDesigner />);
    expect(await screen.findByText('工程团队', {}, { timeout: 5000 })).toBeInTheDocument();
    // Both member nodes render as focusable buttons.
    const nodes = screen.getAllByRole('button', { name: /implementer|coordinator/ });
    expect(nodes.length).toBeGreaterThanOrEqual(2);
    await waitFor(() => {
      expect(teamsApi.resolveBinding).toHaveBeenCalledWith('team-t1', expect.any(String));
    });
  });

  it('shows 未执行 rather than a guessed effective model before a batch runs', async () => {
    setup();
    render(<TeamDesigner />);
    await screen.findByText('工程团队', {}, { timeout: 5000 });
    expect(screen.getAllByText(/实际：/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/未执行/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/gpt-/)).not.toBeInTheDocument();
  });

  it('is honest that real model calls are BLOCKED_EXTERNAL', async () => {
    setup();
    render(<TeamDesigner />);
    await screen.findByText('工程团队', {}, { timeout: 5000 });
    expect(screen.getByText(/真实模型未配置 · BLOCKED_EXTERNAL/)).toBeInTheDocument();
    expect(screen.getByText(/真实模型往返为 BLOCKED_EXTERNAL/)).toBeInTheDocument();
  });

  it('renders validation blockers instead of hiding them', async () => {
    setup({
      validation: {
        ...snapshot.validation,
        can_start: false,
        blockers: [{ code: 'model_unresolved', role: 'implementer', message: '成员模型未解析' }],
      },
    });
    render(<TeamDesigner />);
    await screen.findByText('工程团队', {}, { timeout: 5000 });
    expect(screen.getByText(/成员模型未解析/)).toBeInTheDocument();
  });

  it('disables per-member model control for a host that cannot support it', async () => {
    setup({
      members: [
        member({ role: 'coordinator', agent_instance_id: 'agt-c', session_id: 's-c' }),
        member({
          agent_host: 'external_a2a',
          control: { disabled_operations: ['switch_model'], supports_per_member_model: false },
        }),
      ],
    });
    render(<TeamDesigner />);
    await clickMemberNode('implementer');
    await waitFor(() => {
      expect(screen.getByLabelText(/请求模型（节点覆盖）/)).toBeDisabled();
    });
    expect(screen.getByText(/逐成员模型覆盖：宿主 external_a2a 不支持/)).toBeInTheDocument();
  });

  it('reports the BLOCKED_EXTERNAL switch control as disabled, not faked', async () => {
    setup({
      members: [
        member({ role: 'coordinator', agent_instance_id: 'agt-c', session_id: 's-c' }),
        member({
          agent_host: 'external_a2a',
          control: { disabled_operations: ['switch_model'], supports_per_member_model: false },
        }),
      ],
    });
    render(<TeamDesigner />);
    await clickMemberNode('implementer');
    await waitFor(() => {
      expect(screen.getByLabelText(/运行中切换模型/)).toBeDisabled();
    });
    // Nothing was attempted.
    expect(teamsApi.control).not.toHaveBeenCalled();
  });

  it('shows the inheritance source for the selected node', async () => {
    setup();
    render(<TeamDesigner />);
    await clickMemberNode('implementer');
    await waitFor(() => {
      expect(screen.getByText(/继承来源：继承团队默认/)).toBeInTheDocument();
    });
  });

  it('offers no model that the catalog does not list', async () => {
    setup();
    render(<TeamDesigner />);
    await screen.findByText('工程团队', {}, { timeout: 5000 });
    const opts = Array.from(
      document.querySelectorAll('select#node-model option') as NodeListOf<HTMLOptionElement>,
    ).map((o) => o.textContent ?? '');
    expect(opts.join('|')).toContain('mock-deterministic');
    expect(opts.join('|')).not.toContain('gpt-');
  });
});