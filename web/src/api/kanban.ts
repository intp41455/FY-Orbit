// 任务看板 API 客户端（A-任务看板-01～13 前端面）
//
// 范式照 `dslCanvas.ts`：类型从后端契约派生，**不在前端另抄一份枚举**。
// 看板列 id（todo/doing/blocked/done）与后端 `services/kanban.py::KANBAN_COLUMNS`
// 逐字对齐——后端是列的唯一真源，前端只消费。
import { request } from './client';

/** 看板四列，顺序即渲染顺序。与后端 KANBAN_COLUMNS 一致。 */
export const KANBAN_COLUMNS = ['todo', 'doing', 'blocked', 'done'] as const;
export type KanbanColumnId = (typeof KANBAN_COLUMNS)[number];

/** 列的中文标题与状态语义色令牌。色值一律走 var(--ui-st-*)，此处只给令牌名。 */
export const COLUMN_META: Record<KanbanColumnId, { label: string; token: string }> = {
  todo: { label: '待办', token: '--ui-ink-3' },
  doing: { label: '进行中', token: '--ui-st-running' },
  blocked: { label: '阻塞', token: '--ui-st-blocked' },
  done: { label: '完成', token: '--ui-st-complete' },
};

/**
 * 红带只由后端两类真实信号点亮，前端**不能**自行推导。
 * 前端只负责把它画出来（强红 = --ui-st-failed）。
 */
export interface RedBandReason {
  kind: 'interruption_open' | 'budget_threshold';
  detail: string;
  count?: number;
  since?: string | null;
  percent?: number;
  threshold_percent?: number;
}

export interface RedBandDetail {
  reasons: RedBandReason[];
  since: string | null;
  duration_minutes: number | null;
  escalated: boolean;
  escalation_hours: number;
}

export interface KanbanCard {
  id: string;
  goal: string;
  status: string;
  stage: string;
  domain: string;
  mode: string;
  critical: boolean;
  weight: number;
  /** 本行自身进度 0-100（NULL 未开始时回落到状态机，故恒为数字） */
  own_progress: number;
  /** 按子任务权重加权后的进度（需求 03）。无子任务时等于 own_progress。 */
  weighted_progress: number;
  progress_source: 'subtasks' | 'self';
  subtask_count: number;
  done_subtask_count: number;
  depends_on: string[];
  blocks: string[];
  planned_start: string | null;
  planned_end: string | null;
  /** false = 未排期，甘特据此显示「未排期」，不编造日期 */
  scheduled: boolean;
  blocked_reason: string | null;
  blocked_since: string | null;
  red_band: boolean;
  red_band_detail: RedBandDetail | null;
  created_at: string | null;
  updated_at: string | null;
  deadline: string | null;
}

export interface KanbanColumn {
  id: KanbanColumnId;
  cards: KanbanCard[];
  count: number;
}

export interface BoardSummary {
  total_cards: number;
  weighted_progress: number;
  red_band_count: number;
  /** cancelled 不上板，但必须被看见 */
  cancelled_count: number;
  escalation_hours: number;
}

export interface BoardResponse {
  columns: KanbanColumn[];
  summary: BoardSummary;
}

export interface BurnPoint {
  date: string;
  remaining_weight: number;
  completed_weight: number;
  /** false = 当天无完成事件，前端连线即可，不补点 */
  sampled: boolean;
}

export interface BurndownResponse {
  days: number;
  initial_weight: number;
  ideal: Array<{ date: string; remaining_weight: number }>;
  actual: BurnPoint[];
  sampled_days: number;
  /** true = 样本不足，UI 必须显示「数据不足」而不是画一条假线 */
  partial: boolean;
  method: string;
}

export interface TaskEventView {
  id: string;
  kind: string;
  from_status: string | null;
  to_status: string | null;
  detail: Record<string, unknown> | null;
  created_at: string | null;
}

export interface TaskDetailResponse {
  task: KanbanCard;
  subtasks: Array<{
    id: string;
    goal: string;
    status: string;
    critical: boolean;
    weight: number;
    own_progress: number;
    blocked_reason: string | null;
  }>;
  checklist: Array<{ id: string; goal: string; done: boolean; critical: boolean }>;
  critical_items: Array<{ id: string; goal: string; status: string }>;
  dependencies: {
    blocked_by: Array<{ task_id: string; satisfied: boolean }>;
    blocks: Array<{ task_id: string }>;
  };
  history: TaskEventView[];
}

export type KanbanOperation = 'pause' | 'resume' | 'terminate';

export interface OperationResult {
  task_id: string;
  status: string;
  previous_status?: string;
  changed: boolean;
  /** terminate 重复点击时为 true（幂等，不是错误） */
  idempotent?: boolean;
  blocked_reason?: string | null;
  blocked_since?: string | null;
  allowed_operations?: string[];
}

export interface ApiErrorShape {
  code: string;
  message: string;
  status: number;
}

/** 统一把后端的 {code,message} 信封转成可判定类型，而不是靠字符串猜。 */
export function kanbanErrorCode(err: unknown): string {
  if (err && typeof err === 'object' && 'code' in err) {
    const code = (err as { code?: unknown }).code;
    if (typeof code === 'string') return code;
  }
  return 'unknown_error';
}

export function board(escalationHours?: number): Promise<BoardResponse> {
  const query = escalationHours === undefined ? undefined : { escalation_hours: escalationHours };
  return request<BoardResponse>('/api/kanban/board', { query });
}

export function boardBurndown(days = 14): Promise<BurndownResponse> {
  return request<BurndownResponse>('/api/kanban/board/burndown', { query: { days } });
}

export function taskDetail(taskId: string): Promise<TaskDetailResponse> {
  return request<TaskDetailResponse>(`/api/kanban/tasks/${encodeURIComponent(taskId)}`);
}

/**
 * 写进度/权重/关键事项。
 *
 * 🔴 字段**不传**与传 `null` 语义不同：后端用 UNSET 哨兵区分
 * 「不改」与「清空」。所以这里不能用 `|| undefined` 之类会把 null 吃掉的写法，
 * 必须原样把 key 交给 JSON。
 */
export function setProgress(
  taskId: string,
  patch: { progress_percent?: number | null; weight?: number | null; critical?: boolean | null },
): Promise<KanbanCard> {
  return request<KanbanCard>(`/api/kanban/tasks/${encodeURIComponent(taskId)}/progress`, {
    method: 'PUT',
    body: patch,
  });
}

export function setPlan(
  taskId: string,
  plan: { planned_start?: string | null; planned_end?: string | null },
): Promise<KanbanCard> {
  return request<KanbanCard>(`/api/kanban/tasks/${encodeURIComponent(taskId)}/plan`, {
    method: 'PUT',
    body: plan,
  });
}

export function addDependency(taskId: string, dependsOnTaskId: string): Promise<unknown> {
  return request(`/api/kanban/tasks/${encodeURIComponent(taskId)}/dependencies`, {
    method: 'POST',
    body: { depends_on_task_id: dependsOnTaskId },
  });
}

export function removeDependency(taskId: string, dependsOnTaskId: string): Promise<unknown> {
  return request(
    `/api/kanban/tasks/${encodeURIComponent(taskId)}/dependencies/${encodeURIComponent(
      dependsOnTaskId,
    )}`,
    { method: 'DELETE' },
  );
}

export function runOperation(
  taskId: string,
  operation: KanbanOperation,
  reason = '',
): Promise<OperationResult> {
  return request<OperationResult>(
    `/api/kanban/tasks/${encodeURIComponent(taskId)}/${operation}`,
    { method: 'POST', body: { reason } },
  );
}

export function startTask(taskId: string, reason = ''): Promise<OperationResult> {
  return request<OperationResult>(`/api/kanban/tasks/${encodeURIComponent(taskId)}/start`, {
    method: 'POST',
    body: { reason },
  });
}