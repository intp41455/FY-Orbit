/**
 * HITL 待决队列 Hook
 * 管理待决中断列表和 SSE 实时订阅
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { hitlApi, HitlInterrupt, ListInterruptsResponse } from '../api/hitl';
import type { HitlQueueItem, HitlDecision } from '../types/hitl';

const SSE_RECONNECT_DELAY = 3000; // 重连延迟（ms）
const POLL_INTERVAL = 10000; // 轮询间隔（ms，当 SSE 不可用时）

interface UseHitlQueueOptions {
  /** 是否启用 SSE 实时推送 */
  enableSSE?: boolean;
  /** 轮询间隔（ms） */
  pollInterval?: number;
  /** 筛选特定 execution_id */
  executionId?: string;
}

interface UseHitlQueueResult {
  /** 待决中断列表 */
  items: HitlQueueItem[];
  /** 是否加载中 */
  loading: boolean;
  /** 错误信息 */
  error: string | null;
  /** 刷新列表 */
  refresh: () => Promise<void>;
  /** 提交决策 */
  decide: (interruptId: string, decision: HitlDecision) => Promise<HitlInterrupt>;
  /** SSE 连接状态 */
  sseConnected: boolean;
}

function mapToQueueItem(interrupt: HitlInterrupt): HitlQueueItem {
  return {
    id: interrupt.id,
    executionId: interrupt.execution_id,
    checkpoint: interrupt.checkpoint,
    context: interrupt.context as Record<string, unknown>,
    options: interrupt.options,
    reason: interrupt.reason,
    expiresAt: interrupt.expires_at,
    createdAt: interrupt.created_at,
    version: interrupt.version,
    status: 'pending',
  };
}

export function useHitlQueue(options: UseHitlQueueOptions = {}): UseHitlQueueResult {
  const {
    enableSSE = true,
    pollInterval = POLL_INTERVAL,
    executionId,
  } = options;

  const [items, setItems] = useState<HitlQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sseConnected, setSseConnected] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 加载待决中断列表
  const loadPending = useCallback(async () => {
    try {
      const response: ListInterruptsResponse = await hitlApi.listPending(executionId);
      setItems(response.items.map(mapToQueueItem));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load pending interrupts');
    } finally {
      setLoading(false);
    }
  }, [executionId]);

  // 提交决策
  const decide = useCallback(
    async (interruptId: string, decision: HitlDecision): Promise<HitlInterrupt> => {
      const result = await hitlApi.decide(interruptId, {
        decision: decision.decision,
        resolution: decision.resolution,
      });

      // 决策成功后从列表移除
      setItems((prev) => prev.filter((item) => item.id !== interruptId));

      return result;
    },
    []
  );

  // SSE 实时订阅
  useEffect(() => {
    if (!enableSSE) return;

    let eventSource: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      try {
        const sseUrl = executionId
          ? `/api/hitl/events?execution_id=${encodeURIComponent(executionId)}`
          : '/api/hitl/events';

        eventSource = new EventSource(sseUrl);
        eventSourceRef.current = eventSource;

        eventSource.onopen = () => {
          setSseConnected(true);
          setError(null);
        };

        eventSource.addEventListener('interrupt_created', (event) => {
          try {
            const interrupt: HitlInterrupt = JSON.parse(event.data);
            setItems((prev) => {
              // 避免重复添加
              if (prev.some((item) => item.id === interrupt.id)) {
                return prev;
              }
              return [...prev, mapToQueueItem(interrupt)];
            });
          } catch {
            console.error('Failed to parse interrupt_created event');
          }
        });

        eventSource.addEventListener('interrupt_decided', (event) => {
          try {
            const data: { id: string } = JSON.parse(event.data);
            setItems((prev) => prev.filter((item) => item.id !== data.id));
          } catch {
            console.error('Failed to parse interrupt_decided event');
          }
        });

        eventSource.addEventListener('interrupt_expired', (event) => {
          try {
            const data: { id: string } = JSON.parse(event.data);
            setItems((prev) => prev.filter((item) => item.id !== data.id));
          } catch {
            console.error('Failed to parse interrupt_expired event');
          }
        });

        eventSource.onerror = () => {
          setSseConnected(false);
          eventSource?.close();
          eventSourceRef.current = null;

          // 重连
          reconnectTimer = setTimeout(connect, SSE_RECONNECT_DELAY);
        };
      } catch (err) {
        setError(err instanceof Error ? err.message : 'SSE connection failed');
        setSseConnected(false);
      }
    };

    connect();

    // 初始加载
    loadPending();

    // 备用轮询（当 SSE 不可用时）
    if (pollInterval > 0) {
      pollIntervalRef.current = setInterval(loadPending, pollInterval);
    }

    return () => {
      eventSource?.close();
      eventSourceRef.current = null;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [enableSSE, executionId, loadPending, pollInterval]);

  return {
    items,
    loading,
    error,
    refresh: loadPending,
    decide,
    sseConnected,
  };
}
