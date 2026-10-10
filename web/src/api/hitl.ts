/**
 * HITL (Human-in-the-Loop) API 客户端
 * 对接后端 /api/hitl/* 端点
 */

export interface HitlInterrupt {
  id: string;
  owner_id: string;
  execution_id: string;
  checkpoint: string;
  context: Record<string, unknown>;
  options: Array<{ value: string; label: string }>;
  status: 'pending' | 'approved' | 'rejected' | 'cancelled' | 'expired';
  decision: string | null;
  resolution: Record<string, unknown> | null;
  decided_by: string | null;
  reason: string;
  expires_at: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface InterruptBody {
  execution_id: string;
  checkpoint: string;
  context?: Record<string, unknown>;
  options: Array<string | { value: string; label: string }>;
  reason?: string;
  timeout_seconds?: number;
}

export interface DecisionBody {
  decision: string;
  resolution?: Record<string, unknown>;
  expected_version?: number;
}

export interface ListInterruptsResponse {
  count: number;
  items: HitlInterrupt[];
}

const BASE_URL = import.meta.env.VITE_API_BASE || '';

class HitlApi {
  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${BASE_URL}${path}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
  }

  /**
   * 列出所有待决中断（GET /api/hitl/interrupts）
   */
  async listPending(executionId?: string): Promise<ListInterruptsResponse> {
    const params = executionId ? `?execution_id=${encodeURIComponent(executionId)}` : '';
    return this.request<ListInterruptsResponse>(`/api/hitl/interrupts${params}`);
  }

  /**
   * 获取单个中断详情（GET /api/hitl/interrupts/{id}）
   */
  async getInterrupt(id: string): Promise<HitlInterrupt> {
    return this.request<HitlInterrupt>(`/api/hitl/interrupts/${id}`);
  }

  /**
   * 查询某执行是否处于暂停态（GET /api/hitl/executions/{execution_id}/interrupt）
   */
  async getExecutionStatus(executionId: string): Promise<HitlInterrupt | null> {
    try {
      return await this.request<HitlInterrupt>(
        `/api/hitl/executions/${encodeURIComponent(executionId)}/interrupt`
      );
    } catch {
      return null;
    }
  }

  /**
   * 提交决策（POST /api/hitl/interrupts/{id}/decision）
   */
  async decide(
    interruptId: string,
    body: DecisionBody
  ): Promise<HitlInterrupt> {
    return this.request<HitlInterrupt>(
      `/api/hitl/interrupts/${interruptId}/decision`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      }
    );
  }

  /**
   * 创建中断（POST /api/hitl/interrupts）——通常由后端调用
   */
  async createInterrupt(body: InterruptBody): Promise<HitlInterrupt> {
    return this.request<HitlInterrupt>('/api/hitl/interrupts', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }
}

export const hitlApi = new HitlApi();
