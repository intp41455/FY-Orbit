import { request } from './client';
import type { CreateTaskInput, TaskSummary } from './types';

export const tasksApi = {
  create: (body: CreateTaskInput) =>
    request<TaskSummary>('/api/tasks', {
      method: 'POST',
      body,
      idempotencyKey: body.idempotency_key,
    }),
  get: (id: string) => request<TaskSummary>(`/api/tasks/${id}`),
  cancel: (id: string) =>
    request<TaskSummary>(`/api/tasks/${id}/cancel`, { method: 'POST' }),
};
