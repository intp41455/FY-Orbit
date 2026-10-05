import { useEffect, useState } from 'react';
import {
  notificationsApi,
  subscribeNotifications,
  type NotificationItem,
  type NotificationPushPayload,
} from '../api/notifications';

export function NotificationBell() {
  const [unreadCount, setUnreadCount] = useState<number>(0);
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [activeComment, setActiveComment] = useState<{ id: string; body: string } | null>(null);
  const [loadingBody, setLoadingBody] = useState<boolean>(false);

  // 1. 初始化拉取未读数
  useEffect(() => {
    let active = true;
    void notificationsApi.unreadCount().then((count) => {
      if (active) setUnreadCount(count);
    }).catch(() => {
      // 网络离线时容错
    });
    return () => {
      active = false;
    };
  }, []);

  // 2. SSE 订阅推送（/api/collaboration/notifications/events）
  useEffect(() => {
    const unsubscribe = subscribeNotifications({
      onPush: (payload: NotificationPushPayload) => {
        // 核心判据 X2：收到推送按 unread_count 刷红点
        if (typeof payload.unread_count === 'number') {
          setUnreadCount(payload.unread_count);
        }
      },
      onGap: () => {
        // 收到 gap 帧触发全量重新拉取
        void notificationsApi.unreadCount().then(setUnreadCount);
        if (isOpen) {
          void notificationsApi.list().then((res) => setNotifications(res.items));
        }
      },
    });

    return () => {
      unsubscribe();
    };
  }, [isOpen]);

  // 打开面板时拉取通知列表
  const toggleOpen = async () => {
    const next = !isOpen;
    setIsOpen(next);
    if (next) {
      try {
        const res = await notificationsApi.list();
        setNotifications(res.items);
      } catch {
        // ignore
      }
    } else {
      setActiveComment(null);
    }
  };

  // 核心判据 X3：正文绝不来自推送，必须另走既有评论 API 抓取
  const handleViewDetail = async (item: NotificationItem) => {
    setLoadingBody(true);
    try {
      const body = await notificationsApi.fetchCommentBody(
        item.record_kind,
        item.record_id,
        item.comment_id,
      );
      setActiveComment({ id: item.id, body });
      if (!item.read_at) {
        await notificationsApi.markRead(item.id);
        setUnreadCount((c) => Math.max(0, c - 1));
      }
    } finally {
      setLoadingBody(false);
    }
  };

  return (
    <div className="notification-bell-container" style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        className="ghost icon-btn"
        aria-label="通知中心"
        data-testid="notification-bell-btn"
        onClick={() => void toggleOpen()}
        style={{ position: 'relative', fontSize: '1.1rem', cursor: 'pointer', background: 'none', border: 'none' }}
      >
        <span aria-hidden="true">🔔</span>
        {unreadCount > 0 && (
          <span
            className="notification-badge"
            data-testid="notification-badge"
            style={{
              position: 'absolute',
              top: '-4px',
              right: '-6px',
              backgroundColor: '#ef4444',
              color: '#fff',
              fontSize: '0.7rem',
              fontWeight: 700,
              padding: '1px 5px',
              borderRadius: '9999px',
              minWidth: '16px',
              textAlign: 'center',
            }}
          >
            {unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div
          className="notification-popover"
          data-testid="notification-popover"
          style={{
            position: 'absolute',
            bottom: '40px',
            left: '0',
            width: '280px',
            maxHeight: '360px',
            overflowY: 'auto',
            background: 'var(--bg-elevated, #1e293b)',
            border: '1px solid var(--border, #334155)',
            borderRadius: '8px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            padding: '10px',
            zIndex: 1000,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
            <strong style={{ fontSize: '0.85rem' }}>通知</strong>
            <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>未读: {unreadCount}</span>
          </div>

          {notifications.length === 0 ? (
            <div style={{ fontSize: '0.8rem', color: '#94a3b8', padding: '12px 0', textAlign: 'center' }}>
              暂无新通知
            </div>
          ) : (
            <div className="notification-list" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {notifications.map((n) => (
                <div
                  key={n.id}
                  data-testid={`notification-item-${n.id}`}
                  onClick={() => void handleViewDetail(n)}
                  style={{
                    padding: '6px 8px',
                    borderRadius: '4px',
                    background: n.read_at ? 'transparent' : 'rgba(59, 130, 246, 0.1)',
                    cursor: 'pointer',
                    fontSize: '0.8rem',
                  }}
                >
                  <div className="notification-summary" data-testid="notification-summary">
                    {n.summary}
                  </div>
                  {activeComment?.id === n.id && (
                    <div
                      className="notification-comment-detail"
                      data-testid="notification-comment-body"
                      style={{ marginTop: '4px', padding: '4px 6px', background: 'rgba(0,0,0,0.2)', borderRadius: '4px' }}
                    >
                      {loadingBody ? '加载正文中...' : activeComment.body}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
