import { request } from './client';
import type {
  ConversationDetail,
  ConversationSummary,
  Message,
  PostMessageInput,
} from './types';

export const conversationsApi = {
  // Real backend returns a bare JSON array, not { conversations: [...] }.
  list: () => request<ConversationSummary[]>('/api/conversations'),
  create: (body: { title?: string; domain?: string; mode?: string }) =>
    request<ConversationSummary>('/api/conversations', { method: 'POST', body }),
  get: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),
  messages: (id: string) =>
    request<Message[]>(`/api/conversations/${id}/messages`),
  postMessage: (id: string, body: PostMessageInput) =>
    request<Message>(`/api/conversations/${id}/messages`, {
      method: 'POST',
      body,
      idempotencyKey: body.client_message_id,
    }),
  // BUG #4: generate an assistant reply for the conversation's latest user
  // message. Returns the persisted assistant Message (with metadata). The
  // backend reads the last user message when body is empty; passing the text
  // explicitly keeps the call self-contained.
  reply: (id: string, body: { message?: string } = {}, signal?: AbortSignal) =>
    request<Message>(`/api/conversations/${id}/reply`, {
      method: 'POST',
      body,
      signal,
    }),
  // P3-22: 后端软删（deleted_at），幂等；跨 owner 一律 404。
  remove: (id: string) =>
    request<{ id: string; deleted: boolean }>(`/api/conversations/${id}`, {
      method: 'DELETE',
    }),
};
