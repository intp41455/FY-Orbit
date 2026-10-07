import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../api/archiveFork', () => ({
  fetchTimeline: vi.fn(),
  listForks: vi.fn(),
  createFork: vi.fn(),
  discardFork: vi.fn(),
  compareFork: vi.fn(),
}));

import { fetchTimeline, listForks, createFork, compareFork } from '../api/archiveFork';
import { TimelinePage } from './TimelinePage';
import { ArchiveTimeline } from '../components/timeline/ArchiveTimeline';
import { ForkCompare } from '../components/timeline/ForkCompare';
import { ForkForm } from '../components/timeline/ForkForm';

const EVENTS = [
  {
    kind: 'fork' as const,
    at: '2026-10-01T10:00:00+08:00',
    fork_id: 'f1',
    thread_id: 'orig--fork-aaa',
    parent_thread_id: 'orig',
    parent_checkpoint_id: 'cp-1',
    label: '低温重跑',
    state: 'active',
    overrides: { temperature: 0.2 },
  },
  {
    kind: 'discard' as const,
    at: '2026-10-01T11:00:00+08:00',
    fork_id: 'f1',
    thread_id: 'orig--fork-aaa',
    reason: '方案作废',
  },
];

const FORKS = [
  {
    fork_id: 'f1',
    source_thread_id: 'orig',
    source_checkpoint_id: 'cp-1',
    new_thread_id: 'orig--fork-aaa',
    overrides: { temperature: 0.2 },
    snapshot_id: '',
    session_key: '',
    label: '低温重跑',
    state: 'active' as const,
    created_at: '2026-10-01T10:00:00+08:00',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchTimeline).mockResolvedValue({ events: EVENTS, count: EVENTS.length });
  vi.mocked(listForks).mockResolvedValue({ forks: FORKS });
});

describe('ArchiveTimeline（A-存档回溯-06）', () => {
  it('renders events in the given order without re-sorting', () => {
    render(<ArchiveTimeline events={EVENTS} />);
    const items = screen.getAllByTestId(/^timeline-event-/);
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('orig--fork-aaa');
  });

  it('shows an honest empty state', () => {
    render(<ArchiveTimeline events={[]} />);
    expect(screen.queryByTestId('archive-timeline')).toBeNull();
    expect(screen.getByText(/还没有存档分支记录/)).toBeInTheDocument();
  });

  it('invokes onSelectFork when a fork event is clicked', async () => {
    const onSelect = vi.fn();
    render(<ArchiveTimeline events={EVENTS} onSelectFork={onSelect} />);
    // 可点的是 <li> 内的按钮（li 只是轴点容器）
    const li = screen.getByTestId('timeline-event-fork');
    await userEvent.click(li.querySelector('button') as HTMLButtonElement);
    expect(onSelect).toHaveBeenCalledWith('f1');
  });
});

describe('ForkCompare（A-存档回溯-05）', () => {
  it('renders an honest empty state when no result', () => {
    render(<ForkCompare result={null} />);
    expect(screen.getByText(/选一个分支并点「对比」/)).toBeInTheDocument();
  });

  it('trusts the backend identical flag', () => {
    render(
      <ForkCompare
        result={{
          left_label: 'a',
          right_label: 'b',
          identical: true,
          summary: { total_keys: 1, same: 1, changed: 0, only_left: 0, only_right: 0 },
          same: ['x'],
          only_left: [],
          only_right: [],
          changed: [],
        }}
      />,
    );
    expect(screen.getByTestId('compare-identical')).toHaveTextContent('完全一致');
  });

  it('lists changed keys with before/after', () => {
    render(
      <ForkCompare
        result={{
          left_label: 'a',
          right_label: 'b',
          identical: false,
          summary: { total_keys: 1, same: 0, changed: 1, only_left: 0, only_right: 0 },
          same: [],
          only_left: [],
          only_right: [],
          changed: [{ key: 'score', left: 0.8, right: 0.9 }],
        }}
      />,
    );
    expect(screen.getByTestId('compare-changed')).toHaveTextContent('score');
    expect(screen.getByTestId('compare-changed')).toHaveTextContent('0.8');
    expect(screen.getByTestId('compare-changed')).toHaveTextContent('0.9');
  });
});

describe('ForkForm（A-存档回溯-04）', () => {
  it('rejects empty thread id', async () => {
    const onSubmit = vi.fn();
    render(<ForkForm onSubmit={onSubmit} />);
    await userEvent.click(screen.getByTestId('fork-submit'));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId('fork-form-error')).toHaveTextContent('源 thread 不能为空');
  });

  it('rejects malformed overrides JSON', async () => {
    const onSubmit = vi.fn();
    render(<ForkForm defaultThreadId="t1" onSubmit={onSubmit} />);
    await userEvent.clear(screen.getByTestId('fork-overrides-input'));
    await userEvent.type(screen.getByTestId('fork-overrides-input'), '{{bad');
    await userEvent.click(screen.getByTestId('fork-submit'));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId('fork-form-error')).toBeInTheDocument();
  });

  it('submits parsed overrides', async () => {
    const onSubmit = vi.fn();
    render(<ForkForm defaultThreadId="t1" onSubmit={onSubmit} />);
    await userEvent.clear(screen.getByTestId('fork-overrides-input'));
    await userEvent.type(screen.getByTestId('fork-overrides-input'), '{{"temperature":0.2}');
    await userEvent.click(screen.getByTestId('fork-submit'));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        source_thread_id: 't1',
        overrides: { temperature: 0.2 },
      }),
    );
  });
});

describe('TimelinePage（三条款集成）', () => {
  it('loads timeline and forks on mount', async () => {
    render(<TimelinePage />);
    await waitFor(() => {
      expect(screen.getByTestId('archive-timeline')).toBeInTheDocument();
    });
    expect(screen.getByTestId('timeline-event-fork')).toBeInTheDocument();
    expect(screen.getByTestId('fork-form')).toBeInTheDocument();
  });

  it('shows an honest error when loading fails', async () => {
    vi.mocked(fetchTimeline).mockRejectedValue(new Error('down'));
    vi.mocked(listForks).mockRejectedValue(new Error('down'));
    render(<TimelinePage />);
    await waitFor(() => {
      expect(screen.getByTestId('timeline-error')).toBeInTheDocument();
    });
  });

  it('creates a fork via the form', async () => {
    vi.mocked(createFork).mockResolvedValue({
      status: 'ok',
      fork: FORKS[0],
    });
    render(<TimelinePage />);
    await waitFor(() => expect(screen.getByTestId('fork-form')).toBeInTheDocument());

    await userEvent.type(screen.getByTestId('fork-thread-input'), 'orig');
    await userEvent.click(screen.getByTestId('fork-submit'));

    await waitFor(() => {
      expect(createFork).toHaveBeenCalledWith(
        expect.objectContaining({ source_thread_id: 'orig' }),
      );
    });
  });

  it('compares a selected fork', async () => {
    vi.mocked(compareFork).mockResolvedValue({
      left_label: 'orig',
      right_label: 'orig--fork-aaa',
      identical: false,
      summary: { total_keys: 1, same: 0, changed: 1, only_left: 0, only_right: 0 },
      same: [],
      only_left: [],
      only_right: [],
      changed: [{ key: 'score', left: 1, right: 2 }],
    });
    render(<TimelinePage />);
    await waitFor(() => expect(screen.getByTestId('fork-select-f1')).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('fork-select-f1'));
    await waitFor(() => {
      expect(compareFork).toHaveBeenCalledWith('f1');
    });
  });
});
