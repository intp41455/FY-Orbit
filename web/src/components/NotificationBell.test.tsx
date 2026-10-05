/**
 * Tests: 第 7 批 P8 前端通知订阅与红点组件 (NotificationBell)
 *
 * 判据覆盖：
 * X1: 前端真有订阅者（由静态与集成 grep 验证）
 * X2: 收到推送红点变化：mock 推送 → 断言红点从 A (2) 变 B (5)
 * X3: 正文不来自推送：断言渲染的评论正文由既有评论 API 拉取，推送载荷中绝无正文
 * X4: 收到 gap 帧触发全量重新拉取
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

class FakeEventSource {
  static last: FakeEventSource | null = null;
  closed = false;
  listeners: Record<string, ((ev: unknown) => void)[]> = {};
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, cb: (ev: unknown) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  emit(type: string, data: unknown) {
    this.listeners[type]?.forEach((cb) => cb({ data: JSON.stringify(data) }));
  }
  close() {
    this.closed = true;
  }
}

vi.mock('../api/notifications', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/notifications')>();
  return {
    ...actual,
    notificationsApi: {
      unreadCount: vi.fn(),
      list: vi.fn(),
      markRead: vi.fn(),
      fetchCommentBody: vi.fn(),
    },
  };
});

import { notificationsApi } from '../api/notifications';
import { NotificationBell } from './NotificationBell';

const api = notificationsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  (globalThis as Record<string, unknown>).EventSource = FakeEventSource;
  FakeEventSource.last = null;
  api.unreadCount.mockResolvedValue(2);
  api.list.mockResolvedValue({
    count: 1,
    items: [
      {
        id: 'nt-001',
        owner_id: 'user-1',
        kind: 'mention',
        record_kind: 'memory',
        record_id: 'm-100',
        comment_id: 'cm-200',
        author_id: 'alice',
        summary: 'alice 在 memory 的评论中提到了你',
        read_at: null,
        created_at: '2026-10-05T00:00:00Z',
        version: 1,
      },
    ],
  });
  api.markRead.mockResolvedValue({ id: 'nt-001', read_at: '2026-10-05T00:01:00Z' });
  api.fetchCommentBody.mockResolvedValue('这是走既有评论 API 拉取到的正文内容');
});

afterEach(() => {
  vi.clearAllMocks();
  delete (globalThis as Record<string, unknown>).EventSource;
});

describe('NotificationBell 前端订阅与红点交互', () => {
  it('X2: 初始红点为 2，收到推送后红点从 2 变 5', async () => {
    render(<NotificationBell />);

    // 初始值 A = 2
    await waitFor(() => {
      const badge = screen.getByTestId('notification-badge');
      expect(badge.textContent).toBe('2');
    });

    const es = FakeEventSource.last;
    expect(es).toBeDefined();
    expect(es?.url).toContain('/api/collaboration/notifications/events');

    // mock 推送下发，unread_count 变为 5
    es?.emit('notification', {
      kind: 'mention',
      record_kind: 'memory',
      record_id: 'm-100',
      notification_id: 'nt-002',
      comment_id: 'cm-201',
      unread_count: 5,
      summary: 'bob 提到了你',
    });

    // 断言红点从 2 变 B = 5（严禁只断言渲染成功）
    await waitFor(() => {
      const badge = screen.getByTestId('notification-badge');
      expect(badge.textContent).toBe('5');
    });
  });

  it('X3: 展开通知后拉取正文必须另走既有评论 API，推送载荷中绝无评论正文', async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);

    // 点击打开面板
    const btn = screen.getByTestId('notification-bell-btn');
    await user.click(btn);

    // 列表中展示定位串 summary，不含任何评论正文
    await waitFor(() => {
      expect(screen.getByText('alice 在 memory 的评论中提到了你')).toBeInTheDocument();
    });

    // 点击该条通知查看详情
    const item = screen.getByTestId('notification-item-nt-001');
    await user.click(item);

    // 验证调用了独立评论接口 fetchCommentBody，且正文来自该接口
    expect(api.fetchCommentBody).toHaveBeenCalledWith('memory', 'm-100', 'cm-200');
    await waitFor(() => {
      expect(screen.getByTestId('notification-comment-body')).toHaveTextContent(
        '这是走既有评论 API 拉取到的正文内容',
      );
    });
  });

  it('X4: 收到 gap 帧触发全量重新拉取', async () => {
    api.unreadCount.mockResolvedValueOnce(2).mockResolvedValueOnce(9);
    render(<NotificationBell />);

    await waitFor(() => {
      expect(screen.getByTestId('notification-badge').textContent).toBe('2');
    });

    const es = FakeEventSource.last!;
    // 触发 gap 事件
    es.emit('gap', {});

    // 触发全量重新拉取，未读数更新为 9
    await waitFor(() => {
      expect(screen.getByTestId('notification-badge').textContent).toBe('9');
    });
  });
});
