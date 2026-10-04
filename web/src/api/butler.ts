// Typed client for the personal-space butler (数码小屋管家) API.
// Mirrors api/routes/butler.py. Honesty contract: without a configured model
// the dialogue endpoint answers 503 model_not_configured — callers must fall
// back to the labelled local pool (预生成台词池), never present pool lines as
// model output. See components/cabin/cabinDialogueProvider.ts.
import { request } from './client';

const BASE = '/api/butler';

export interface ButlerStatus {
  model_configured: boolean;
}

export interface ButlerDialogueBody {
  speaker: 'person' | 'pet';
  personality: string;
  /** 非敏感场景信息（用户名/宠物名/房屋/背景）；画像与私人记忆数据禁止放入。 */
  context?: Record<string, unknown>;
}

export interface ButlerDialogueResponse {
  line: string;
  source: 'model';
  model: string;
}

export const butlerApi = {
  status: () => request<ButlerStatus>(`${BASE}/status`),

  dialogue: (body: ButlerDialogueBody) =>
    request<ButlerDialogueResponse>(`${BASE}/dialogue`, { method: 'POST', body }),
};
