/**
 * P15 档案库页面测试（A-三重模式-03 / A-上下文持久化-02/03）。
 *
 * 只测界面契约：
 *   - 档案必须显示「哪些是空的、为什么」（不能静默留白）；
 *   - 简报截断时**必须**显示截断提示（后端截断不能被前端吃掉）；
 *   - 复盘能一键沉淀为知识模板；
 *   - 企业模式的「没被映射到的字段」必须逐条列出（不静默丢弃）；
 *   - 权限复用声明必须出现在页面上。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../api/dossier', () => ({
  fetchArchive: vi.fn(),
  fetchBriefing: vi.fn(),
  fetchRetrospective: vi.fn(),
  distill: vi.fn(),
  listKnowledge: vi.fn(),
  knowledgeDetail: vi.fn(),
  enterpriseCatalog: vi.fn(),
  enterpriseAdapt: vi.fn(),
  enterpriseOnboarding: vi.fn(),
  registerMapping: vi.fn(),
}));

import * as api from '../api/dossier';
import { DossierPage } from './DossierPage';

const archive = {
  task_id: 'task-1',
  objective: {
    goal: '把产品资料变成三条小红书文案',
    domain: 'personal', mode: 'listen', strategy: 'auto',
    status: 'running', stage: 'execution', progress_percent: 40,
    weight: 1, critical: false, deadline: '2027-01-01T00:00:00Z',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
    blocked_reason: null, blocked_since: null,
    planned_start: null, planned_end: null,
  },
  milestones: [],
  roster: { teams: [], members: [] },
  decisions: [],
  artifacts: [],
  dependencies: { blocked_by: [], blocks: [], subtasks: [] },
  change_log: [],
  failure: null,
  notes: ['该任务未绑定团队或成员实例，分工表为空'],
  sources: { objective: 'tasks', roster: 'team_definitions + agent_instances' },
  generated_at: '2026-10-07T00:00:00Z',
};

const briefing = {
  task_id: 'task-1',
  briefing: '# 任务档案简报 · task-1\n\n## 一句话目标\n\n把产品资料变成三条小红书文案\n',
  chars: 200, limit: 200, truncated: true,
  archive_endpoint: '/api/dossier/tasks/task-1/archive',
  note: '读这一段即可接手',
};

const retro = {
  task_id: 'task-1',
  report: '# 任务复盘\n\n## 经验条目\n\n- [baseline] 直接复用即可\n',
  timeline: [], decisions: [],
  metrics: { change_events: 3, decision_count: 1, member_count: 2, team_count: 0,
             attempt_versions: 1, span_minutes: 60 },
  lessons: [{ kind: 'blocker', text: '出现过阻塞：上游没就绪' }],
  generated_at: '2026-10-07T00:00:00Z',
};

beforeEach(() => {
  vi.mocked(api.fetchArchive).mockResolvedValue(archive as never);
  vi.mocked(api.fetchBriefing).mockResolvedValue(briefing as never);
  vi.mocked(api.fetchRetrospective).mockResolvedValue(retro as never);
  vi.mocked(api.listKnowledge).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.enterpriseCatalog).mockResolvedValue({
    spec_version: '1.0.0',
    adapters: [
      { target: 'generic', label: '通用（直连字段名）', mappings: [], notes: [] },
      { target: 'spring-ai-chatclient', label: 'Spring AI ChatClient', mappings: [], notes: [] },
    ],
    governance: {
      permission_source: 'find_yourself.services.grant（既有授权数据面）',
      identity_source: 'find_yourself.services.auth',
      single_loop_kernel: true,
    },
    custom_mappings: [],
  } as never);
  vi.mocked(api.enterpriseAdapt).mockResolvedValue({
    target: 'generic',
    spec: { agent: { id: 'contract-reviewer' } },
    mapped: [{ from: 'name', to: 'agent.id', via: 'identity' }],
    unmapped: ['corpRule.level'],
    warnings: ['1 个外部字段没有现成映射，已原样保留在 extensions.unmapped，未丢弃'],
    executed: false,
    note: '产出的是可运行规格',
  } as never);
  vi.mocked(api.enterpriseOnboarding).mockResolvedValue({
    target: 'generic', markdown: '# 企业接入文档 · 通用', mappings: [],
    governance: {},
  } as never);
});

async function loadArchive() {
  render(<DossierPage />);
  await userEvent.type(screen.getByLabelText('任务 id'), 'task-1');
  await userEvent.click(screen.getByRole('button', { name: /翻档案/ }));
  // 目标会同时出现在档案卡与简报里，用 findAll 断言「至少出现一次」
  await waitFor(() =>
    expect(screen.getAllByText(/把产品资料变成三条小红书文案/).length).toBeGreaterThan(0),
  );
}

describe('任务档案（A-上下文持久化-02）', () => {
  it('加载档案后显示目标与「为什么是空的」说明', async () => {
    await loadArchive();
    expect(screen.getByText('40%')).toBeInTheDocument();
    expect(screen.getByText('该任务未绑定团队或成员实例，分工表为空')).toBeInTheDocument();
    expect(screen.getByText(/审计链上没有与本任务相关的帧/)).toBeInTheDocument();
    expect(screen.getByText(/尚无尝试记录/)).toBeInTheDocument();
  });

  it('简报被截断时必须显示截断提示与完整档案入口', async () => {
    await loadArchive();
    const notice = screen.getByText(/简报已截断/);
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveTextContent(/200\/200/);
    expect(notice).toHaveTextContent(/archive/);
  });
});

describe('复盘与沉淀（A-上下文持久化-03）', () => {
  it('显示复盘指标与经验条目，并能沉淀为知识模板', async () => {
    vi.mocked(api.distill).mockResolvedValue({
      knowledge_id: 'k1', title: '文案经验模板', path: '.runtime/dossier/k1.md',
      source_task_id: 'task-1', lessons: [], report: '',
    } as never);
    await loadArchive();
    expect(screen.getByText('变更事件')).toBeInTheDocument();
    expect(screen.getByText('出现过阻塞：上游没就绪')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /沉淀为知识模板/ }));
    await waitFor(() => expect(api.distill).toHaveBeenCalledWith('task-1'));
    await waitFor(() => expect(screen.getByText(/已沉淀为知识模板/)).toBeInTheDocument());
  });
});

describe('企业模式（A-三重模式-03）', () => {
  it('声明权限复用，并逐条列出没被映射到的字段', async () => {
    render(<DossierPage />);
    await userEvent.click(screen.getByRole('button', { name: '企业模式' }));
    await waitFor(() => expect(screen.getByText(/不提供第二套多租户/)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /用示例定义试转换/ }));
    await waitFor(() => expect(api.enterpriseAdapt).toHaveBeenCalled());
    expect(await screen.findByText('corpRule.level')).toBeInTheDocument();
    expect(screen.getByText(/未丢弃/)).toBeInTheDocument();
  });

  it('知识沉淀页在空态下给出去哪儿创建的指引', async () => {
    render(<DossierPage />);
    await userEvent.click(screen.getByRole('button', { name: '知识沉淀' }));
    await waitFor(() =>
      expect(screen.getByText(/还没有沉淀过知识模板/)).toBeInTheDocument(),
    );
  });
});
