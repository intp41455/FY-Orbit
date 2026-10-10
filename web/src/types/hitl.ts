/**
 * HITL 相关类型定义
 */

export interface HitlOption {
  value: string;
  label: string;
}

export interface HitlContext {
  // DSL 执行上下文
  dsl_digest?: string;
  node_id?: string;
  node_type?: string;
  action?: string;
  // 任务上下文
  task_description?: string;
  proposed_changes?: Record<string, unknown>;
  // 文件操作上下文
  file_path?: string;
  file_operation?: 'create' | 'update' | 'delete';
  // 其他任意上下文
  [key: string]: unknown;
}

export interface HitlDecision {
  interruptId: string;
  decision: 'approved' | 'rejected' | 'cancelled' | 'timeout';
  resolution?: Record<string, unknown>;
  timestamp: string;
  decidedBy?: string;
}

export interface HitlQueueItem {
  id: string;
  executionId: string;
  checkpoint: string;
  context: HitlContext;
  options: HitlOption[];
  reason: string;
  expiresAt: string | null;
  createdAt: string;
  version: number;
  status: 'pending' | 'processing';
}

export type HitlStatus = 'pending' | 'approved' | 'rejected' | 'cancelled' | 'expired';
