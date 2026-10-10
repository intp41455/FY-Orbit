/**
 * HITL 待决队列面板组件
 * 显示所有待用户决策的中断
 */

import React, { useState } from 'react';
import { useHitlQueue } from '../hooks/useHitlQueue';
import { HitlDecisionPanel } from './HitlDecisionPanel';
import type { HitlQueueItem, HitlOption } from '../types/hitl';
import './HitlQueuePanel.css';

interface HitlQueuePanelProps {
  /** 是否启用 SSE 实时推送 */
  enableSSE?: boolean;
  /** 筛选特定 execution_id */
  executionId?: string;
  /** 自定义样式 */
  className?: string;
}

export function HitlQueuePanel({
  enableSSE = true,
  executionId,
  className = '',
}: HitlQueuePanelProps): React.ReactElement {
  const { items, loading, error, refresh, sseConnected } = useHitlQueue({
    enableSSE,
    executionId,
  });

  const [selectedItem, setSelectedItem] = useState<HitlQueueItem | null>(null);

  if (loading) {
    return (
      <div className={`hitl-queue-panel ${className}`}>
        <div className="hitl-queue-panel__loading">加载中...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`hitl-queue-panel ${className}`}>
        <div className="hitl-queue-panel__error">
          <span>⚠️ {error}</span>
          <button onClick={refresh}>重试</button>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className={`hitl-queue-panel ${className}`}>
        <div className="hitl-queue-panel__empty">
          <div className="hitl-queue-panel__empty-icon">✓</div>
          <div className="hitl-queue-panel__empty-text">暂无待决任务</div>
          <div className="hitl-queue-panel__connection">
            <span
              className={`hitl-queue-panel__status ${
                sseConnected ? 'hitl-queue-panel__status--connected' : ''
              }`}
            />
            {sseConnected ? '实时同步已连接' : '实时同步未连接'}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`hitl-queue-panel ${className}`}>
      <div className="hitl-queue-panel__header">
        <h3 className="hitl-queue-panel__title">
          待决任务
          <span className="hitl-queue-panel__count">{items.length}</span>
        </h3>
        <div className="hitl-queue-panel__controls">
          <span
            className={`hitl-queue-panel__status ${
              sseConnected ? 'hitl-queue-panel__status--connected' : ''
            }`}
            title={sseConnected ? '实时同步已连接' : '实时同步未连接'}
          />
          <button
            className="hitl-queue-panel__refresh"
            onClick={refresh}
            title="刷新"
          >
            ↻
          </button>
        </div>
      </div>

      <div className="hitl-queue-panel__list">
        {items.map((item) => (
          <HitlQueueItemCard
            key={item.id}
            item={item}
            onClick={() => setSelectedItem(item)}
          />
        ))}
      </div>

      {selectedItem && (
        <HitlDecisionPanel
          item={selectedItem}
          onClose={() => setSelectedItem(null)}
        />
      )}
    </div>
  );
}

interface HitlQueueItemCardProps {
  item: HitlQueueItem;
  onClick: () => void;
}

function HitlQueueItemCard({ item, onClick }: HitlQueueItemCardProps): React.ReactElement {
  const createdAt = new Date(item.createdAt);
  const timeAgo = formatTimeAgo(createdAt);

  const isExpiring = item.expiresAt && isExpiringSoon(item.expiresAt);

  return (
    <div
      className={`hitl-queue-item ${isExpiring ? 'hitl-queue-item--expiring' : ''}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
    >
      <div className="hitl-queue-item__header">
        <span className="hitl-queue-item__checkpoint">{item.checkpoint}</span>
        <span className="hitl-queue-item__time">{timeAgo}</span>
      </div>

      <div className="hitl-queue-item__context">
        {renderContext(item.context)}
      </div>

      <div className="hitl-queue-item__options">
        {item.options.map((opt: HitlOption) => (
          <span key={opt.value} className="hitl-queue-item__option">
            {opt.label}
          </span>
        ))}
      </div>

      {isExpiring && (
        <div className="hitl-queue-item__expiry">
          ⚠️ 即将过期
        </div>
      )}
    </div>
  );
}

function renderContext(context: Record<string, unknown>): React.ReactNode {
  if (!context || Object.keys(context).length === 0) {
    return null;
  }

  const parts: string[] = [];

  if (context.task_description) {
    parts.push(String(context.task_description));
  }

  if (context.action) {
    parts.push(`操作: ${context.action}`);
  }

  if (context.file_path) {
    parts.push(`文件: ${context.file_path}`);
  }

  if (context.node_type) {
    parts.push(`节点: ${context.node_type}`);
  }

  return parts.length > 0 ? parts.join(' · ') : null;
}

function formatTimeAgo(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);

  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function isExpiringSoon(expiresAt: string): boolean {
  const expiry = new Date(expiresAt);
  const now = Date.now();
  const remaining = expiry.getTime() - now;
  // 5 分钟内过期
  return remaining > 0 && remaining < 5 * 60 * 1000;
}
