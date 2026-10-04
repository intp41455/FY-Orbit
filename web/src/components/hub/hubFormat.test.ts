import { describe, expect, it } from 'vitest';
import {
  capabilityTags,
  isInvocable,
  isMasked,
  kindLabel,
  stateText,
  stateTone,
} from './hubFormat';
import { healthTone } from './HubHealthBadge';
import type { HubConnection } from '../../api/hub';

function conn(patch: Partial<HubConnection> = {}): HubConnection {
  return {
    id: 'c1',
    name: 'Ollama',
    kind: 'openai_chat',
    group: 'ai',
    preset_id: 'ollama',
    icon: '🦙',
    description: '',
    state: 'active',
    config: {},
    secret_fields: [],
    capabilities: [],
    has_manifest: false,
    params: null,
    preference: 0,
    health: { ok: null, checked_at: null, latency_ms: null, detail: '' },
    version: 1,
    created_at: null,
    updated_at: null,
    ...patch,
  };
}

describe('hubFormat', () => {
  it('健康三态：ok / false / 从未探活(null) 互不混淆', () => {
    expect(healthTone({ ok: true, checked_at: null, latency_ms: 5, detail: '' })).toBe('ok');
    expect(healthTone({ ok: false, checked_at: null, latency_ms: null, detail: '连接被拒' })).toBe('fail');
    // null（未探活）绝不能被当成「可用」
    expect(healthTone({ ok: null, checked_at: null, latency_ms: null, detail: '' })).toBe('unknown');
    expect(healthTone(null)).toBe('unknown');
  });

  it('状态文案与色调映射：缺凭证/停用不算可用', () => {
    expect(stateText('active')).toBe('已启用');
    expect(stateText('needs_credentials')).toBe('缺凭证');
    expect(stateText('unknown_state')).toBe('unknown_state');
    expect(stateTone('active')).toBe('ok');
    expect(stateTone('error')).toBe('fail');
    expect(stateTone('needs_credentials')).toBe('warn');
    expect(stateTone('disabled')).toBe('idle');
  });

  it('缺凭证与停用的连接不可调用', () => {
    expect(isInvocable(conn())).toBe(true);
    expect(isInvocable(conn({ state: 'needs_credentials' }))).toBe(false);
    expect(isInvocable(conn({ state: 'disabled' }))).toBe(false);
  });

  it('掩码识别：后端只回掩码，前端据此拒绝当作明文展示', () => {
    // 后端 mask_secret 的两种产出都要识别
    expect(isMasked('****')).toBe(true);
    expect(isMasked('****abcd')).toBe(true);
    expect(isMasked('sk-****ef')).toBe(true);
    expect(isMasked('sk-abc')).toBe(false);
    expect(isMasked(42)).toBe(false);
  });

  it('能力标签折叠为 +N', () => {
    const many = conn({
      capabilities: Array.from({ length: 8 }, (_, i) => ({ name: `cap-${i}` })),
    });
    const { shown, extra } = capabilityTags(many, 6);
    expect(shown).toHaveLength(6);
    expect(extra).toBe(2);
    expect(capabilityTags(conn()).extra).toBe(0);
  });

  it('类型标签中文化，未知类型原样返回（不伪造）', () => {
    expect(kindLabel('mcp_server')).toBe('MCP Server');
    expect(kindLabel('knowledge_source')).toBe('知识源');
    expect(kindLabel('brand_new_kind')).toBe('brand_new_kind');
  });
});
