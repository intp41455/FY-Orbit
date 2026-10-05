/**
 * P8 · 通知推送与列表 API
 *
 * 铁律：
 * 1. 推送层绝不搬运评论正文（通知不存正文，只存 summary 定位串与元数据）；
 * 2. 正文必须另走既有评论 API；
 * 3. EventSource("/api/collaboration/notifications/events") 订阅，按 unread_count 刷新红点；
 * 4. 收到 gap 帧触发全量重拉。
 */
import { request } from './client';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export interface NotificationPushPayload {
  kind: string;
  record_kind: string;
  record_id: string;
  notification_id: string;
  comment_id: string;
  unread_count: number;
  summary: string;
  gap?: boolean;
}

export interface NotificationItem {
  id: string;
  owner_id: string;
  kind: string;
  record_kind: string;
  record_id: string;
  comment_id: string;
  author_id: string;
  summary: string;
  read_at: string | null;
  created_at: string;
  version: number;
}

export interface NotificationListResponse {
  count: number;
  items: NotificationItem[];
  limit?: number;
  offset?: number;
}

export const notificationsApi = {
  async list(limit = 50, offset = 0): Promise<NotificationListResponse> {
    return request<NotificationListResponse>(`/api/collaboration/notifications?limit=${limit}&offset=${offset}`);
  },

  async unreadCount(): Promise<number> {
    const res = await request<{ unread: number }>('/api/collaboration/notifications/unread-count');
    return res.unread;
  },

  async markRead(notificationId: string): Promise<NotificationItem> {
    return request<NotificationItem>(`/api/collaboration/notifications/${notificationId}/read`, {
      method: 'POST',
    });
  },

  /**
   * 铁律：拉正文必须另走既有评论 API，绝不从推送事件中提取正文
   */
  async fetchCommentBody(recordKind: string, recordId: string, commentId: string): Promise<string> {
    const res = await request<{ items: Array<{ id: string; body: string }> }>(
      `/api/collaboration/comments?record_kind=${recordKind}&record_id=${recordId}`
    );
    const match = res.items.find((c) => c.id === commentId);
    return match?.body ?? '';
  },
};

export interface NotificationStreamHandlers {
  onPush: (payload: NotificationPushPayload) => void;
  onGap?: () => void;
  onError?: (err: Event) => void;
  onOpen?: () => void;
}

export function subscribeNotifications(handlers: NotificationStreamHandlers): () => void {
  const url = `${API_BASE}/api/collaboration/notifications/events`;
  const es = new EventSource(url, { withCredentials: true });

  es.addEventListener('notification', (ev: MessageEvent) => {
    try {
      const data = JSON.parse(ev.data) as NotificationPushPayload;
      handlers.onPush(data);
    } catch {
      // JSON 解析失败则忽略
    }
  });

  es.addEventListener('gap', () => {
    handlers.onGap?.();
  });

  es.onerror = (err) => {
    handlers.onError?.(err);
  };

  es.onopen = () => {
    handlers.onOpen?.();
  };

  return () => {
    es.close();
  };
}
