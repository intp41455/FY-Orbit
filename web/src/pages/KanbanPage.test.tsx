/**
 * 任务看板前端组件测试（A-任务看板-03 / 08 / 09 / 10 / 12 / 13）。
 *
 * 真调 API client 的 mock（不是把整页替换成 stub），因此断言的是**页面与后端
 * 契约的对接**：字段名、请求方法、错误码处理、拖拽落点的可达性判定。
 *
 * 与「不许 mock 数据」的关系：这里 mock 的是**网络层**，返回的是与后端
 * `services/kanban.py` 逐字一致的真实响应形状（见 `boardFixture`）。
 * 没有任何一处编造后端不会返回的字段来让测试通过。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { KanbanPage } from './KanbanPage';
import * as api from '../api/kanban';
import type { BoardResponse, BurndownResponse, KanbanCard, TaskDetailResponse } from '../api/kanban';

/* ------------------------------------------------------------------ */
/* fixtures：字段名与后端 services/kanban.py 的返回逐字一致              */
/* ------------------------------------------------------------------ */
function card(over: Partial<KanbanCard> = {}): KanbanCard {
  return {
    id: 't1',
    goal: '把看板做完',
    status: 'queued',
    stage: 'requirements',
    domain: 'work',
    mode: 'engineering',
    critical: false,
    weight: 1,
    own_progress: 0,
    weighted_progress: 0,
    progress_source: 'self',
    subtask_count: 0,
    done_subtask_count: 0,
    depends_on: [],
    blocks: [],
    planned_start: null,
    planned_end: null,
    scheduled: false,
    blocked_reason: null,
    blocked_since: null,
    red_band: false,
    red_band_detail: null,
    created_at: '2026-07-01T09:00:00+00:00',
    updated_at: '2026-07-02T09:00:00+00:00',
    deadline: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

function boardFixture(cards: KanbanCard[] = [card()]): BoardResponse {
  const columns = (['todo', 'doing', 'blocked', 'done'] as const).map((id) => ({
    id,
    cards: cards.filter((c) => {
      if (id === 'todo') return c.status === 'queued';
      if (id === 'doing') return c.status === 'running';
      if (id === 'blocked') return ['waiting_input', 'waiting_approval', 'failed'].includes(c.status);
      return c.status === 'completed';
    }),
    count: cards.filter((c) => {
      if (id === 'todo') return c.status === 'queued';
      if (id === 'doing') return c.status === 'running';
      if (id === 'blocked') return ['waiting_input', 'waiting_approval', 'failed'].includes(c.status);
      return c.status === 'completed';
    }).length,
  }));
  return {
    columns,
    summary: {
      total_cards: columns.reduce((n, c) => n + c.count, 0),
      weighted_progress: 42,
      red_band_count: cards.filter((c) => c.red_band).length,
      cancelled_count: 2,
      escalation_hours: 6,
    },
  };
}

const burnFixture: BurndownResponse = {
  days: 5,
  initial_weight: 3,
  ideal: [
    { date: '2026-07-01', remaining_weight: 3 },
    { date: '2026-07-02', remaining_weight: 2.25 },
    { date: '2026-07-03', remaining_weight: 1.5 },
    { date: '2026-07-04', remaining_weight: 0.75 },
    { date: '2026-07-05', remaining_weight: 0 },
  ],
  actual: [
    { date: '2026-07-01', remaining_weight: 3, completed_weight: 0, sampled: false },
    { date: '2026-07-02', remaining_weight: 3, completed_weight: 0, sampled: false },
    { date: '2026-07-03', remaining_weight: 2, completed_weight: 1, sampled: true },
    { date: '2026-07-04', remaining_weight: 2, completed_weight: 1, sampled: true },
    { date: '2026-07-05', remaining_weight: 2, completed_weight: 1, sampled: true },
  ],
  sampled_days: 3,
  partial: false,
  method: 'remaining = sum(weight of top-level tasks not completed as of that day)',
};

function detailFixture(over: Partial<TaskDetailResponse['task']> = {}): TaskDetailResponse {
  return {
    task: card({ id: 't1', ...over }),
    subtasks: [
      {
        id: 't1-a', goal: '写接口', status: 'completed', critical: false,
        weight: 1, own_progress: 100, blocked_reason: null,
      },
      {
        id: 't1-b', goal: '写页面', status: 'queued', critical: true,
        weight: 5, own_progress: 40, blocked_reason: null,
      },
    ],
    checklist: [
      { id: 't1-a', goal: '写接口', done: true, critical: false },
      { id: 't1-b', goal: '写页面', done: false, critical: true },
    ],
    critical_items: [{ id: 't1-b', goal: '写页面', status: 'queued' }],
    dependencies: { blocked_by: [{ task_id: 'up', satisfied: false }], blocks: [] },
    history: [
      {
        id: 'e1', kind: 'progress', from_status: null, to_status: null,
        detail: { changed: { progress_percent: 40 } }, created_at: '2026-07-02T10:00:00+00:00',
      },
    ],
  };
}

/* ------------------------------------------------------------------ */
beforeEach(() => {
  vi.spyOn(api, 'board').mockResolvedValue(boardFixture());
  vi.spyOn(api, 'boardBurndown').mockResolvedValue(burnFixture);
  vi.spyOn(api, 'taskDetail').mockResolvedValue(detailFixture());
  vi.spyOn(api, 'runOperation').mockResolvedValue({ task_id: 't1', status: 'waiting_input', changed: true });
  vi.spyOn(api, 'startTask').mockResolvedValue({ task_id: 't1', status: 'running', changed: true });
  vi.spyOn(api, 'setProgress').mockResolvedValue(card());
});

afterEach(() => {
  vi.restoreAllMocks();
});

/* ------------------------------ 基础渲染 ------------------------------ */
describe('看板页基础', () => {
  it('渲染四列与汇总，并把 cancelled 单独显示而不是从板上消失', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    for (const id of ['todo', 'doing', 'blocked', 'done']) {
      expect(screen.getByTestId(`kb-col-${id}`)).toBeInTheDocument();
    }
    const summary = screen.getByTestId('kb-summary');
    expect(summary).toHaveAttribute('data-total', '1');
    // cancelled_count=2 但 total_cards=1：前者不在板上，必须被单独看见
    expect(screen.getByTestId('kb-cancelled-chip')).toHaveTextContent('2');
    expect(screen.getByTestId('kb-cancelled-chip')).not.toBeNull();
  });

  it('空看板显示诚实空状态，不用假卡片填坑', async () => {
    vi.mocked(api.board).mockResolvedValue(boardFixture([]));
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    expect(screen.getByTestId('kb-empty-todo')).toBeInTheDocument();
    expect(screen.getByTestId('kb-empty-done')).toBeInTheDocument();
    expect(screen.queryByTestId('kb-card-t1')).toBeNull();
  });
});

/* ------------------------------ 03 Donut ------------------------------ */
describe('加权进度 Donut（需求 03）', () => {
  it('卡片角落渲染环形图并把加权百分比写进 data-percent', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([card({ weighted_progress: 45, progress_source: 'subtasks', subtask_count: 5, done_subtask_count: 4 })]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const donut = screen.getByTestId('kb-donut');
    expect(donut).toHaveAttribute('data-percent', '45');
    expect(donut).toHaveAttribute('aria-label', expect.stringContaining('45%'));
    // 用了 stroke-dasharray 画弧，没有引入任何图表库
    expect(donut.querySelector('circle[stroke-dasharray]')).not.toBeNull();
  });

  it('0% 时不画弧（避免画出一个假的 0% 起点），但数字仍显示', async () => {
    vi.mocked(api.board).mockResolvedValue(boardFixture([card({ weighted_progress: 0 })]));
    const { container } = render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const donut = screen.getByTestId('kb-donut');
    expect(donut).toHaveAttribute('data-percent', '0');
    expect(donut.querySelector('circle[stroke-dasharray]')).toBeNull();
    expect(container.querySelector('.kb-donut__num')?.textContent).toBe('0');
  });

  it('子任务来源的卡片标明「加权」，自身进度的卡片不冒充加权', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({ id: 'w', weighted_progress: 45, progress_source: 'subtasks' }),
        card({ id: 's', weighted_progress: 30, progress_source: 'self' }),
      ]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    expect(within(screen.getByTestId('kb-card-w')).getByText(/加权/)).toBeInTheDocument();
    expect(within(screen.getByTestId('kb-card-s')).queryByText(/加权/)).toBeNull();
  });
});

/* ---------------------------- 08 拖拽改状态 ---------------------------- */
describe('看板拖拽（需求 08：拖拽改变任务状态）', () => {
  it('卡片带 draggable，落「进行中」列会调 start', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const el = screen.getByTestId('kb-card-t1');
    expect(el).toHaveAttribute('draggable', 'true');

    const col = screen.getByTestId('kb-col-doing');
    fireEvent.dragStart(el);
    fireEvent.dragOver(col);
    fireEvent.drop(col);

    await waitFor(() => expect(api.startTask).toHaveBeenCalledWith('t1'));
  });

  it('落「阻塞」列会调 pause', async () => {
    vi.mocked(api.board).mockResolvedValue(boardFixture([card({ status: 'running' })]));
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const el = screen.getByTestId('kb-card-t1');
    const col = screen.getByTestId('kb-col-blocked');
    fireEvent.dragStart(el);
    fireEvent.dragOver(col);
    fireEvent.drop(col);
    await waitFor(() => expect(api.runOperation).toHaveBeenCalledWith('t1', 'pause', ''));
  });

  it('落回原列什么都不做（不产生无谓请求）', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const el = screen.getByTestId('kb-card-t1');
    const col = screen.getByTestId('kb-col-todo');
    fireEvent.dragStart(el);
    fireEvent.drop(col);
    await waitFor(() => expect(screen.getByTestId('kb-board')).toBeInTheDocument());
    expect(api.startTask).not.toHaveBeenCalled();
    expect(api.runOperation).not.toHaveBeenCalled();
  });

  it('「完成」列不可拖入：状态只能由引擎判定', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const el = screen.getByTestId('kb-card-t1');
    const col = screen.getByTestId('kb-col-done');
    fireEvent.dragStart(el);
    fireEvent.drop(col);
    await waitFor(() => expect(screen.getByTestId('kb-error')).toBeInTheDocument());
    expect(screen.getByTestId('kb-error')).toHaveTextContent('column_not_reachable');
    expect(api.startTask).not.toHaveBeenCalled();
  });

  it('依赖门控 409 会把 code 显示出来，而不是静默失败', async () => {
    vi.mocked(api.startTask).mockRejectedValue(
      Object.assign(new Error('blocked'), { code: 'dependency_blocking' }),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const el = screen.getByTestId('kb-card-t1');
    const col = screen.getByTestId('kb-col-doing');
    fireEvent.dragStart(el);
    fireEvent.drop(col);
    await waitFor(() =>
      expect(screen.getByTestId('kb-error')).toHaveTextContent('dependency_blocking'),
    );
  });
});

/* ------------------------------ 09 列表视图 ------------------------------ */
describe('列表视图（需求 09）', () => {
  it('切换到列表后同源数据排成表，优先级排序把关键任务排前面', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({ id: 'plain', goal: '普通', critical: false, weighted_progress: 90 }),
        card({ id: 'key', goal: '关键', critical: true, weighted_progress: 10 }),
      ]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-list'));
    const rows = screen.getByTestId('kb-list').querySelectorAll<HTMLElement>('tbody tr');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('关键')).toBeInTheDocument();
  });

  it('可按更新时间/截止/进度排序', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({ id: 'a', goal: 'A', updated_at: '2026-07-01T00:00:00+00:00' }),
        card({ id: 'b', goal: 'B', updated_at: '2026-07-09T00:00:00+00:00' }),
      ]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-list'));
    fireEvent.click(screen.getByTestId('kb-sort-updated'));
    const first = screen.getByTestId('kb-list').querySelectorAll<HTMLElement>('tbody tr')[0];
    expect(within(first).getByText('B')).toBeInTheDocument();
  });

  it('没有截止时间的排在最后，不假装它最紧急', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({ id: 'none', goal: '无截止', deadline: null }),
        card({ id: 'soon', goal: '快到期', deadline: '2026-07-05T00:00:00+00:00' }),
      ]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-list'));
    fireEvent.click(screen.getByTestId('kb-sort-deadline'));
    const rows = screen.getByTestId('kb-list').querySelectorAll<HTMLElement>('tbody tr');
    expect(within(rows[0]).getByText('快到期')).toBeInTheDocument();
    expect(within(rows[1]).getByText('未设置')).toBeInTheDocument();
  });
});

/* ------------------------------ 10 详情抽屉 ------------------------------ */
describe('详情抽屉（需求 10）', () => {
  it('点卡片打开详情，含子任务清单 / 关键事项 / 依赖 / 历史', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(within(screen.getByTestId('kb-card-t1')).getByRole('button'));
    await screen.findByTestId('kb-detail');
    const checklist = screen.getByTestId('kb-detail-checklist');
    expect(within(checklist).getByText('写接口')).toBeInTheDocument();
    expect(within(checklist).getByText('写页面')).toBeInTheDocument();
    expect(screen.getByTestId('kb-detail-critical')).toHaveTextContent('写页面');
    expect(screen.getByTestId('kb-detail-blockedby')).toHaveTextContent('up（未完成）');
    expect(screen.getByTestId('kb-detail-history')).toHaveTextContent('progress');
  });

  it('没有历史时显示诚实空态', async () => {
    vi.mocked(api.taskDetail).mockResolvedValue({
      ...detailFixture(),
      history: [],
      subtasks: [],
      checklist: [],
      critical_items: [],
      dependencies: { blocked_by: [], blocks: [] },
    });
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(within(screen.getByTestId('kb-card-t1')).getByRole('button'));
    await screen.findByTestId('kb-detail');
    expect(screen.getByTestId('kb-detail-nohistory')).toBeInTheDocument();
    expect(screen.getByTestId('kb-detail-empty')).toBeInTheDocument();
  });

  it('操作按钮按当前状态启用：queued 只给启动/终止', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(within(screen.getByTestId('kb-card-t1')).getByRole('button'));
    await screen.findByTestId('kb-detail');
    expect(screen.getByTestId('kb-detail-start')).not.toBeDisabled();
    expect(screen.getByTestId('kb-detail-pause')).toBeDisabled();
    expect(screen.getByTestId('kb-detail-resume')).toBeDisabled();
    expect(screen.getByTestId('kb-detail-terminate')).not.toBeDisabled();
  });

  it('点恢复会调 resume 并刷新', async () => {
    vi.mocked(api.taskDetail).mockResolvedValue(detailFixture({ status: 'waiting_input' }));
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(within(screen.getByTestId('kb-card-t1')).getByRole('button'));
    await screen.findByTestId('kb-detail');
    const resume = screen.getByTestId('kb-detail-resume');
    expect(resume).not.toBeDisabled();
    fireEvent.click(resume);
    await waitFor(() => expect(api.runOperation).toHaveBeenCalledWith('t1', 'resume', ''));
  });
});

/* ------------------------------ 11 红带 ------------------------------ */
describe('阻塞红带（需求 11）', () => {
  it('无红带时不渲染红带区', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    expect(screen.queryByTestId('kb-redband')).toBeNull();
    expect(screen.queryByTestId('kb-redband-chip')).toBeNull();
  });

  it('有红带时顶部常驻，并给出原因/时长/依赖/升级标记', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({
          id: 'stuck',
          goal: '卡住的任务',
          status: 'waiting_input',
          red_band: true,
          blocked_reason: '等法务回复',
          depends_on: ['up1'],
          red_band_detail: {
            reasons: [
              { kind: 'interruption_open', detail: 'SSE 流被对端关闭', count: 2 },
              { kind: 'budget_threshold', detail: '90.0% of the per-task budget', percent: 90 },
            ],
            since: '2026-07-01T09:00:00+00:00',
            duration_minutes: 540,
            escalated: true,
            escalation_hours: 6,
          },
        }),
      ]),
    );
    render(<KanbanPage />);
    const band = await screen.findByTestId('kb-redband');
    expect(band).toHaveTextContent('SSE 流被对端关闭');
    expect(band).toHaveTextContent('90.0% of the per-task budget');
    expect(band).toHaveTextContent('等法务回复');
    expect(band).toHaveTextContent('已 9 小时');
    expect(band).toHaveTextContent('依赖 1 个前置');
    expect(band).toHaveTextContent('已升级');
    expect(screen.getByTestId('kb-redband-chip')).toBeInTheDocument();
  });

  it('红带卡片带强红边线类名与 data 标记', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([card({ status: 'waiting_input', red_band: true, red_band_detail: { reasons: [], since: null, duration_minutes: null, escalated: false, escalation_hours: 6 } })]),
    );
    const { container } = render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const el = screen.getByTestId('kb-card-t1');
    expect(el).toHaveAttribute('data-red-band', 'true');
    expect(el.className).toContain('kb-card--red');
    expect(container.querySelector('.kb-redband')).not.toBeNull();
  });
});

/* ------------------------------ 13 甘特 ------------------------------ */
describe('甘特式时间线（需求 13）', () => {
  it('未排期的行显示「未排期」而不是消失或编造日期', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-timeline'));
    const gantt = screen.getByTestId('kb-gantt');
    expect(gantt).toHaveTextContent('未排期');
    // 虚线占位（stroke-dasharray），实心条才是已排期
    expect(gantt.querySelector('rect[stroke-dasharray]')).not.toBeNull();
  });

  it('已排期的行画出实心条，不再显示未排期', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({
          id: 'planned',
          scheduled: true,
          planned_start: '2026-07-01T09:00:00+00:00',
          planned_end: '2026-07-05T17:00:00+00:00',
        }),
      ]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-timeline'));
    const row = screen.getByTestId('kb-gantt-row-planned');
    const rect = row.querySelector('rect')!;
    expect(rect.getAttribute('stroke-dasharray')).toBeNull();
    expect(Number(rect.getAttribute('width'))).toBeGreaterThan(3);
  });

  it('只有起点没有终点也算未排期（画不出长度，不编终点）', async () => {
    vi.mocked(api.board).mockResolvedValue(
      boardFixture([
        card({ scheduled: true, planned_start: '2026-07-01T09:00:00+00:00', planned_end: null }),
      ]),
    );
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-timeline'));
    expect(screen.getByTestId('kb-gantt')).toHaveTextContent('未排期');
  });
});

/* ------------------------------ 12 燃尽 ------------------------------ */
describe('燃尽图（需求 12）', () => {
  it('有采样点时画理想线（虚线）与实际线（实线）', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-timeline'));
    const chart = await screen.findByTestId('kb-burndown');
    expect(chart).toHaveAttribute('data-partial', 'false');
    expect(screen.getByTestId('kb-burndown-ideal')).toHaveAttribute('stroke-dasharray');
    expect(screen.getByTestId('kb-burndown-actual')).not.toBeNull();
  });

  it('样本不足时不画实际线，明确显示「数据不足」而不是一条假线', async () => {
    vi.mocked(api.boardBurndown).mockResolvedValue({
      ...burnFixture,
      partial: true,
      sampled_days: 0,
      actual: burnFixture.actual.map((p) => ({ ...p, sampled: false })),
    });
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-timeline'));
    const chart = await screen.findByTestId('kb-burndown');
    expect(chart).toHaveAttribute('data-partial', 'true');
    expect(screen.getByTestId('kb-burndown-no-data')).toHaveTextContent('数据不足');
    expect(screen.queryByTestId('kb-burndown-actual')).toBeNull();
    // 理想线仍在（它是参考斜坡，与有没有数据无关）
    expect(screen.getByTestId('kb-burndown-ideal')).not.toBeNull();
  });

  it('燃尽接口失败时显示「不可用」，不假装有图', async () => {
    vi.mocked(api.boardBurndown).mockRejectedValue(new Error('boom'));
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    fireEvent.click(screen.getByTestId('kb-tab-timeline'));
    await waitFor(() => expect(screen.getByText('燃尽数据不可用')).toBeInTheDocument());
  });
});

/* ------------------------------ 视图语义 ------------------------------ */
describe('视图切换语义', () => {
  it('用 role=tablist/tab 而不是 button（e2e 曾因此翻车）', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    const tablist = screen.getByRole('tablist', { name: '看板视图切换' });
    expect(tablist).toBeInTheDocument();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['看板', '列表', '时间线']);
    expect(screen.getByTestId('kb-tab-board')).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByTestId('kb-tab-list'));
    expect(screen.getByTestId('kb-tab-list')).toHaveAttribute('aria-selected', 'true');
  });

  it('每个 tab 的 aria-controls 指向该 tab 激活时真实渲染的面板', async () => {
    render(<KanbanPage />);
    await screen.findByTestId('kb-board');
    // 面板是按激活条件渲染的，所以逐个激活再核对指向。
    // 注意面板的 data-testid 是 kb-board/kb-list/kb-timeline，
    // 而 id 才是 aria-controls 契约里的 kb-panel-*。
    for (const id of ['board', 'list', 'timeline']) {
      fireEvent.click(screen.getByTestId(`kb-tab-${id}`));
      const controls = screen.getByTestId(`kb-tab-${id}`).getAttribute('aria-controls');
      expect(controls).toBe(`kb-panel-${id}`);
      const panel = document.getElementById(controls as string);
      expect(panel).not.toBeNull();
      expect(panel).toHaveAttribute('role', 'tabpanel');
      expect(panel).toHaveAttribute('aria-labelledby', `kb-tab-${id}`);
    }
  });
});