/**
 * P9 · 点哪评哪（A-点哪评哪-05/06/07/08/09/10 + A-路线-03）后端通道。
 *
 * 对应 /api/review 下的会话/意见/热刷新/导出/双路线端点。
 * 意见落库（不再是 localStorage-only）——写入以服务端为准。
 */
import { request } from './client';

export type ReviewMode_ = 'dom' | 'region' | 'freehand';
export type ReviewRoute = 'whitebox' | 'computer_use';
export type NoteState = 'open' | 'resolved' | 'dismissed';
export type RefreshState = 'idle' | 'refreshing' | 'applied' | 'failed';

/** A-点哪评哪-06：归一化圈选区域（0~1，相对视口）。 */
export interface NormalizedRegion {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A-点哪评哪-07：一笔画（点集 + 颜色 + 粗细，坐标同归一化）。 */
export interface Stroke {
  points: Array<{ x: number; y: number }>;
  color: string;
  width: number;
}

export interface ReviewNoteView {
  id: string;
  session_id: string;
  page: string;
  mode: ReviewMode_;
  tag: string;
  element_id: string;
  element_class: string;
  text: string;
  selector: string;
  dom_path: string[];
  region: Partial<NormalizedRegion>;
  strokes: Stroke[];
  audio_ref: string;
  audio_transcript: string;
  note: string;
  /** A-点哪评哪-08：真写标记（二十来字符） */
  marker: string;
  /** A-点哪评哪-09：短代码（会话内唯一） */
  short_code: string;
  /** A-点哪评哪-10：DOM 指纹与上次指纹 */
  dom_digest: string;
  prev_dom_digest: string;
  /** A-点哪评哪-10：结构是否真的变了（后端算好，前端不重算） */
  changed: boolean;
  state: NoteState;
  applied_at: string | null;
  created_at: string;
}

export interface ReviewSessionSummary {
  session_id: string;
  page: string;
  route: ReviewRoute;
  iteration: number;
  refresh_state: RefreshState;
  refresh_note: string;
  refreshed_at: string | null;
  total: number;
  open: number;
  resolved: number;
  dismissed: number;
}

export interface RouteDecision {
  route: ReviewRoute;
  reason: string;
  is_fallback: boolean;
  payload: Record<string, unknown>;
}

/** 开一次评审会话（A-路线-03 显式标记白盒 / Computer Use 兜底）。 */
export function openReviewSession(input: {
  page: string;
  title?: string;
  route?: ReviewRoute;
}): Promise<{ status: string; session: { id: string; page: string; route: ReviewRoute; iteration: number } }> {
  return request<{ status: string; session: { id: string; page: string; route: ReviewRoute; iteration: number } }>(
    '/api/review/sessions',
    { method: 'POST', body: input },
  );
}

/** 会话汇总（含计数与热刷新状态）。 */
export function fetchReviewSession(
  sessionId: string,
): Promise<{ status: string; summary: ReviewSessionSummary }> {
  return request<{ status: string; summary: ReviewSessionSummary }>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}`,
  );
}

/** 新增一条意见（dom / region / freehand 三选一，几何由后端强校验）。 */
export function addReviewNote(
  sessionId: string,
  note: {
    page?: string;
    mode: ReviewMode_;
    tag?: string;
    element_id?: string;
    element_class?: string;
    text?: string;
    selector?: string;
    dom_path?: string[];
    region?: Partial<NormalizedRegion>;
    strokes?: Stroke[];
    audio_ref?: string;
    audio_transcript?: string;
    note: string;
  },
): Promise<{ status: string; note: ReviewNoteView }> {
  return request<{ status: string; note: ReviewNoteView }>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/notes`,
    {
    method: 'POST',
    body: note,
  });
}

/** 列意见（可按状态过滤）。 */
export function listReviewNotes(
  sessionId: string,
  state?: NoteState,
): Promise<{ status: string; notes: ReviewNoteView[]; count: number }> {
  const q = state ? `?state=${encodeURIComponent(state)}` : '';
  return request<{ status: string; notes: ReviewNoteView[]; count: number }>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/notes${q}`,
  );
}

/** 改意见正文 / 状态（`resolved` 会落 applied_at）。 */
export function patchReviewNote(
  noteId: string,
  patch: { note?: string; state?: NoteState },
): Promise<{ status: string; note: ReviewNoteView }> {
  return request<{ status: string; note: ReviewNoteView }>(
    `/api/review/notes/${encodeURIComponent(noteId)}`,
    {
    method: 'PATCH',
    body: patch,
  });
}

/** 删除意见。 */
export function deleteReviewNote(noteId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/review/notes/${encodeURIComponent(noteId)}`, {
    method: 'DELETE',
  });
}

/** A-点哪评哪-05：记录「改完自动刷新预览」的结果。 */
export function markReviewRefreshed(
  sessionId: string,
  input: { state: RefreshState; note?: string },
): Promise<{
  status: string;
  session: { id: string; iteration: number; refresh_state: RefreshState; refresh_note: string };
}> {
  return request<{
    status: string;
    session: { id: string; iteration: number; refresh_state: RefreshState; refresh_note: string };
  }>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/refresh`,
    {
    method: 'POST',
    body: input,
  });
}

/** 导出给执行 Agent 的 Markdown（`compact=true` 走 TOKEN 优化形态）。 */
export function exportReviewNotes(
  sessionId: string,
  compact = false,
): Promise<{ status: string; markdown: string; compact: boolean }> {
  return request<{ status: string; markdown: string; compact: boolean }>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/export?compact=${compact ? 'true' : 'false'}`,
  );
}

/** A-路线-03：决定白盒 / Computer Use 兜底（纯决策，无副作用）。 */
export function decideReviewRoute(input: {
  mode?: ReviewMode_;
  selector?: string;
  dom_path?: string[];
  tag?: string;
  region?: Partial<NormalizedRegion>;
  viewport?: { w: number; h: number };
  dom_reachable?: boolean;
  fallback_reason?: string;
}): Promise<{ status: string; decision: RouteDecision }> {
  return request<{ status: string; decision: RouteDecision }>('/api/review/route', {
    method: 'POST',
    body: input,
  });
}
