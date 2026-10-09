/**
 * P1 统一保存状态指示器（A-基座质保-12 / W2）与接入壳 `BaseBound`（W1）测试。
 *
 * 只测界面契约：
 *   ① 四态文案统一（唯一来源 = SAVE_STATE_LABELS / formatSavedAt），配色吃 data-state；
 *   ② 「详情」可点开，看得到上次保存时间 / 存储位置 / 占用空间 / 时间轴；
 *   ③ 失败态带可见提示、有重试、且**现场（排队改动）还在**；
 *   ④ 不阻断操作、不抢焦点（用户正在输入时状态变化不得把焦点抢走）；
 *   W4：降级（改存备用目录）必须在界面上说得出口。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  SAVE_STATE_LABELS,
  type SaveRecord,
  type StorageErrorReceipt,
} from '../../hooks/useAutosave';
import { SaveStatusIndicator } from './SaveStatusIndicator';
import { BaseBound } from './SaveStatusIndicator';

const AT = new Date(2026, 9, 7, 14, 3, 5).getTime();

const diskFull: StorageErrorReceipt = {
  code: 'disk_full',
  message: '无法保存：可用空间 1.0 KB < 需要 4.0 KB',
  hint: '磁盘已写满：请清理空间或换一个盘。改动仍在队列里排队，不会丢。已先改存到备用目录：/backup。',
  directory: '/backup',
  fallbackUsed: true,
  retryable: true,
  at: AT,
};

describe('SaveStatusIndicator（W2）', () => {
  it('四态文案统一，并且带 data-state 供样式取色', () => {
    const cases = [
      ['saving', SAVE_STATE_LABELS.saving],
      ['dirty', SAVE_STATE_LABELS.dirty],
      ['error', SAVE_STATE_LABELS.error],
    ] as const;
    for (const [state, label] of cases) {
      const { unmount, container } = render(
        <SaveStatusIndicator state={state} now={AT} />,
      );
      expect(screen.getByRole('status')).toHaveTextContent(label);
      expect(container.querySelector('.ui-save-status')?.getAttribute('data-state')).toBe(state);
      unmount();
    }
  });

  it('idle 态不得给出任何「已保存」承诺（回归：曾默认 saved 导致 21 页假绿灯）', () => {
    const { container } = render(<SaveStatusIndicator state="idle" now={AT} />);
    const label = screen.getByRole('status');
    expect(label).toHaveTextContent(SAVE_STATE_LABELS.idle);
    expect(label).not.toHaveTextContent('已保存');
    expect(container.querySelector('.ui-save-status')?.getAttribute('data-state')).toBe('idle');
  });

  it('BaseBound 在 idle 下也不得显示「已保存」', () => {
    render(
      <BaseBound surface="kanban" state="idle">
        <div>body</div>
      </BaseBound>,
    );
    const label = screen.getByRole('status');
    expect(label).toHaveTextContent(SAVE_STATE_LABELS.idle);
    expect(label).not.toHaveTextContent('已保存');
  });

  it('saved 态显示「已保存到 X 时刻」', () => {
    const { container } = render(
      <SaveStatusIndicator state="saved" savedAt={AT} now={AT} />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('已保存到 14:03');
    expect(container.querySelector('.ui-save-status')?.getAttribute('data-state')).toBe('saved');
  });

  it('详情默认收起；点开后能看到保存时间 / 存储位置 / 占用空间 / 时间轴', async () => {
    const history: SaveRecord[] = [
      { state: 'saved', at: AT - 60_000, label: '已保存到 14:02' },
      { state: 'saved', at: AT - 1_000, label: '已保存到 14:03' },
    ];
    render(
      <SaveStatusIndicator
        state="saved" savedAt={AT} location="/local/draft.json" bytes={2048}
        freeBytes={5 * 1024 * 1024} history={history} now={AT}
      />,
    );

    const toggle = screen.getByRole('button', { name: '详情' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('上次保存时间')).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('上次保存时间')).toBeInTheDocument();
    expect(screen.getByText('存储位置')).toBeInTheDocument();
    expect(screen.getByText('/local/draft.json')).toBeInTheDocument();
    expect(screen.getByText('占用空间')).toBeInTheDocument();
    expect(screen.getByText('2.0 KB')).toBeInTheDocument();
    expect(screen.getByText('5.0 MB')).toBeInTheDocument();
    expect(screen.getByText(/保存时间轴/)).toHaveTextContent(/14:03/);

    await userEvent.click(screen.getByRole('button', { name: '收起' }));
    expect(screen.queryByText('上次保存时间')).not.toBeInTheDocument();
  });

  it('占用空间拿不到时如实显示「未知」，不冒充 0', async () => {
    render(<SaveStatusIndicator state="saved" savedAt={AT} bytes={null} now={AT} />);
    await userEvent.click(screen.getByRole('button', { name: '详情' }));
    expect(screen.getAllByText('未知').length).toBeGreaterThanOrEqual(1);
  });

  it('失败态：可见提示 + 可照做的 hint + 重试，且现场（排队改动）还在', async () => {
    const onRetry = vi.fn();
    render(
      <SaveStatusIndicator
        state="error" savedAt={AT} now={AT} error={diskFull}
        pendingChanges={3} onRetry={onRetry} storage="degraded"
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/无法保存/);
    expect(alert).toHaveTextContent(/清理空间或换一个盘/);
    expect(screen.getByText('排队 3 处改动')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('W4 降级：即使已保存也把「改存到备用目录」说清楚', () => {
    render(
      <SaveStatusIndicator
        state="saved" savedAt={AT} now={AT} storage="degraded" error={diskFull}
      />,
    );
    expect(screen.getByTestId('save-degraded-badge')).toHaveTextContent('降级·备用目录');
    expect(screen.getByRole('alert')).toHaveTextContent(/\/backup/);
  });

  it('不抢焦点：状态变化时正在输入的控件仍是焦点所在', async () => {
    const { rerender } = render(
      <div>
        <textarea aria-label="草稿" />
        <SaveStatusIndicator state="saved" savedAt={AT} now={AT} />
      </div>,
    );
    const area = screen.getByLabelText('草稿');
    await userEvent.click(area);
    await userEvent.type(area, '写点东西');
    expect(document.activeElement).toBe(area);

    rerender(
      <div>
        <textarea aria-label="草稿" />
        <SaveStatusIndicator
          state="error" savedAt={AT} now={AT} error={diskFull} onRetry={() => {}}
        />
      </div>,
    );
    expect(document.activeElement).toBe(area);
    expect(document.querySelector('[autofocus]')).toBeNull();
  });

  it('不阻断操作：点指示器不影响外层既有交互（不 stopPropagation / 不 preventDefault）', async () => {
    const outer = vi.fn();
    render(
      <div onClick={outer}>
        <SaveStatusIndicator state="dirty" now={AT} />
      </div>,
    );
    await userEvent.click(screen.getByRole('button', { name: '详情' }));
    expect(outer).toHaveBeenCalledTimes(1);
  });
});

describe('BaseBound（W1 接入壳）', () => {
  it('包住内容并挂上接入声明，默认声明四项能力', () => {
    const { container } = render(
      <BaseBound surface="workbench" state="saved" savedAt={AT} now={AT}>
        <p>工作台内容</p>
      </BaseBound>,
    );
    const root = container.querySelector('[data-base-bound="true"]');
    expect(root).not.toBeNull();
    expect(root?.getAttribute('data-base-surface')).toBe('workbench');
    expect(root?.getAttribute('data-base-capabilities')).toBe(
      'realtime_save,audit_trail,local_first,error_receipt');
    expect(screen.getByText('工作台内容')).toBeInTheDocument();
    // 状态条统一在内容区上方
    expect(container.querySelector('.ui-base-bound__status')).not.toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('已保存到 14:03');
  });

  it('只声明一部分能力时，壳体如实反映（不补齐）', () => {
    const { container } = render(
      <BaseBound surface="reader" capabilities={['local_first']} state="saved">
        <p>只读视图</p>
      </BaseBound>,
    );
    expect(
      container.querySelector('[data-base-bound="true"]')?.getAttribute('data-base-capabilities'),
    ).toBe('local_first');
  });
});
