import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CanvasPage } from './CanvasPage';
import type {
  CanvasInstance,
  CanvasSnapshot,
  CanvasTemplate,
  ConnectorProbe,
} from '../api/canvas';

vi.mock('../api/canvas', () => ({
  canvasApi: {
    templates: vi.fn(),
    connectors: vi.fn(),
    listInstances: vi.fn(),
    getInstance: vi.fn(),
    getSnapshot: vi.fn(),
    getEvents: vi.fn(),
    createInstance: vi.fn(),
    dispatchSubtask: vi.fn(),
    recordHandoff: vi.fn(),
  },
}));

import { canvasApi } from '../api/canvas';

const mockTemplates: CanvasTemplate[] = [
  {
    id: 'personal',
    name: '私人事务模板',
    domain: 'personal',
    center: 'Hermes',
    workers: ['WorkBuddy', '豆包'],
    description: '处理个人规划与生活事务',
    default_budget: 0.5,
    max_depth: 3,
  },
  {
    id: 'work',
    name: '工作工程模板',
    domain: 'work',
    center: 'Codex',
    workers: ['OpenCode', 'Pi agent'],
    description: '处理需求与研发工程',
    default_budget: 2.0,
    max_depth: 4,
  },
];

const mockConnectors: ConnectorProbe[] = [
  {
    name: 'Codex',
    protocol: 'Internal Agent SDK',
    role: 'orchestrator',
    stage: '本机握手通过',
    healthy: true,
    binary_path: 'IDE_BUILTIN',
    blocking_reason: null,
    domains: ['work', 'personal'],
  },
  {
    name: 'Hermes',
    protocol: 'ACP / TUI JSON-RPC',
    role: 'orchestrator',
    stage: '合成任务往返',
    healthy: true,
    binary_path: 'C:\\Users\\intpj\\AppData\\Local\\hermes\\bin\\hermes.cmd',
    blocking_reason: null,
    domains: ['personal'],
  },
  {
    name: 'OpenCode',
    protocol: 'OpenCode CLI / ACP',
    role: 'worker',
    stage: '仅设计',
    healthy: false,
    binary_path: null,
    blocking_reason: '未检测到 opencode 二进制',
    domains: ['work'],
  },
];

const mockInstance: CanvasInstance = {
  id: 'canv-1',
  owner_id: 'user-1',
  project_name: '个人探索项目',
  domain: 'personal',
  template_id: 'personal',
  orchestrator_id: 'Hermes',
  state: 'active',
  config: {},
  created_at: new Date().toISOString(),
};

const mockSnapshot: CanvasSnapshot = {
  instance: mockInstance,
  connectors: mockConnectors,
  dispatches: [
    {
      id: 'disp-1',
      subtask_id: 'sub-1',
      orchestrator_id: 'Hermes',
      worker_id: 'WorkBuddy',
      goal: '整理日程数据',
      state: 'pending_adapter',
      budget_slice: 0.1,
      created_at: new Date().toISOString(),
    },
  ],
  handoffs: [
    {
      id: 'hnd-1',
      stage: 'planning',
      from: 'Hermes',
      to: 'WorkBuddy',
      completed_items: ['需求已明确拆解'],
      created_at: new Date().toISOString(),
    },
  ],
  events: [
    {
      seq: 1,
      event_type: 'canvas.instance.created',
      task_id: null,
      agent_id: null,
      details: { project: '个人探索项目' },
      created_at: new Date().toISOString(),
    },
  ],
};

describe('CanvasPage (05 功能规格)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders canvas topology, orchestrator, and real connector probe stages', async () => {
    vi.mocked(canvasApi.templates).mockResolvedValue({ items: mockTemplates });
    vi.mocked(canvasApi.connectors).mockResolvedValue({ items: mockConnectors });
    vi.mocked(canvasApi.listInstances).mockResolvedValue({ items: [mockInstance], count: 1 });
    vi.mocked(canvasApi.getSnapshot).mockResolvedValue(mockSnapshot);

    render(<CanvasPage />);

    expect(await screen.findByText('协作拓扑: 个人探索项目')).toBeInTheDocument();
    expect(screen.getByText('使用者 (Owner)')).toBeInTheDocument();
    expect(screen.getByText('主控协同中心 (Center)')).toBeInTheDocument();
    expect(screen.getByText('整理日程数据')).toBeInTheDocument();
    expect(screen.getByText(/待接入 \/ 计划派发 \(pending_adapter\)/)).toBeInTheDocument();
    expect(screen.getByText('需求已明确拆解')).toBeInTheDocument();
    expect(screen.getByText('本机握手通过')).toBeInTheDocument();
    expect(screen.getByText(/未检测到 opencode 二进制/)).toBeInTheDocument();
  });

  it('allows user to dispatch a subtask within budget limit', async () => {
    vi.mocked(canvasApi.templates).mockResolvedValue({ items: mockTemplates });
    vi.mocked(canvasApi.connectors).mockResolvedValue({ items: mockConnectors });
    vi.mocked(canvasApi.listInstances).mockResolvedValue({ items: [mockInstance], count: 1 });
    vi.mocked(canvasApi.getSnapshot).mockResolvedValue(mockSnapshot);
    vi.mocked(canvasApi.dispatchSubtask).mockResolvedValue({
      id: 'disp-2',
      subtask_id: 'sub-2',
      orchestrator_id: 'Hermes',
      worker_id: 'WorkBuddy',
      goal: '新增日程同步',
      state: 'pending_adapter',
      budget_slice: 0.2,
      created_at: new Date().toISOString(),
    });

    const user = userEvent.setup();
    render(<CanvasPage />);

    const openDispatchBtn = await screen.findByRole('button', { name: '派发子任务' });
    await user.click(openDispatchBtn);

    const goalInput = await screen.findByLabelText(/目标描述/);
    await user.type(goalInput, '新增日程同步');

    const submitBtn = screen.getByRole('button', { name: '确认派发' });
    const form = submitBtn.closest('form');
    expect(form).not.toBeNull();
    if (form) fireEvent.submit(form);

    await waitFor(() => {
      expect(canvasApi.dispatchSubtask).toHaveBeenCalledWith('canv-1', expect.objectContaining({
        goal: '新增日程同步',
      }));
    });
  });

  it('rejects subtask dispatch exceeding 0.50 budget', async () => {
    vi.mocked(canvasApi.templates).mockResolvedValue({ items: mockTemplates });
    vi.mocked(canvasApi.connectors).mockResolvedValue({ items: mockConnectors });
    vi.mocked(canvasApi.listInstances).mockResolvedValue({ items: [mockInstance], count: 1 });
    vi.mocked(canvasApi.getSnapshot).mockResolvedValue(mockSnapshot);

    const user = userEvent.setup();
    render(<CanvasPage />);

    const openDispatchBtn = await screen.findByRole('button', { name: '派发子任务' });
    await user.click(openDispatchBtn);

    const budgetInput = await screen.findByLabelText(/预算切片/);
    await user.clear(budgetInput);
    await user.type(budgetInput, '0.80');

    const goalInput = await screen.findByLabelText(/目标描述/);
    await user.type(goalInput, '超出预算任务');

    const submitBtn = screen.getByRole('button', { name: '确认派发' });
    const form = submitBtn.closest('form');
    expect(form).not.toBeNull();
    if (form) fireEvent.submit(form);

    expect(await screen.findByText(/单次派发预算切片最高不可超过 \$0\.50/)).toBeInTheDocument();
    expect(canvasApi.dispatchSubtask).not.toHaveBeenCalled();
  });
});
