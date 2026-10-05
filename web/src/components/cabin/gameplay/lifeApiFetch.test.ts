import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createSave,
  save as putSave,
  deleteSave,
  act,
  meta,
  lifeApi,
  isConflictError,
  ApiError,
  type LifeSnapshot,
} from './lifeApi';
import fixture from './__fixtures__/lifeSave.sample.json';

describe('2a · lifeApi HTTP 客户端契约测试 (六端点对齐与错误抛出)', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('A1/A2/A3: fetchSnapshot 发送 GET /api/cabin/life/save 并支持玩家坐标 query', async () => {
    const mockSnapshot: LifeSnapshot = {
      save: fixture.save as any,
      hud_line: '第1天 上午 晴 · 0 金币',
      npcs: fixture.npc_rows as any,
      shop: fixture.shop_rows as any,
      craft: fixture.craft_rows as any,
      gather: fixture.gather_rows as any,
      version: 1,
    };

    let requestedUrl = '';
    let requestedMethod = '';
    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      requestedUrl = url;
      requestedMethod = init?.method ?? 'GET';
      return new Response(JSON.stringify(mockSnapshot), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const res = await lifeApi.fetchSnapshot([10, 20]);
    expect(requestedMethod).toBe('GET');
    expect(requestedUrl).toContain('/api/cabin/life/save');
    expect(requestedUrl).toContain('player_x=10');
    expect(requestedUrl).toContain('player_y=20');
    expect(res.version).toBe(1);
    expect(res.hud_line).toBe('第1天 上午 晴 · 0 金币');
    expect(res.save.owner).toBe(mockSnapshot.save.owner);
    expect(res.npcs).toHaveLength(mockSnapshot.npcs.length);
  });

  it('A1/A2/A3: createSave 发送 POST /api/cabin/life/save 带正确 body', async () => {
    let capturedBody: any = null;
    let capturedMethod = '';
    let capturedUrl = '';

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedUrl = url;
      capturedMethod = init?.method ?? 'GET';
      capturedBody = init?.body ? JSON.parse(init.body as string) : null;
      return new Response(
        JSON.stringify({
          save: fixture.save,
          hud_line: '第1天 上午 晴 · 0 金币',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });

    const res = await createSave('ink', 1);
    expect(capturedUrl).toBe('/api/cabin/life/save');
    expect(capturedMethod).toBe('POST');
    expect(capturedBody).toEqual({ theme: 'ink', day: 1 });
    expect(res.hud_line).toBeTruthy();
  });

  it('A1/A2/A3: save 发送 PUT /api/cabin/life/save 提交偏好与 expected_version', async () => {
    let capturedBody: any = null;
    let capturedMethod = '';
    let capturedUrl = '';

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedUrl = url;
      capturedMethod = init?.method ?? 'GET';
      capturedBody = init?.body ? JSON.parse(init.body as string) : null;
      return new Response(
        JSON.stringify({
          save: { ...fixture.save, version: 2 },
          hud_line: '第1天 上午 晴 · 0 金币',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });

    const res = await putSave({ settings: { active_theme: 'magic' }, expected_version: 1 });
    expect(capturedUrl).toBe('/api/cabin/life/save');
    expect(capturedMethod).toBe('PUT');
    expect(capturedBody).toEqual({ settings: { active_theme: 'magic' }, expected_version: 1 });
    expect(res.save.version).toBe(2);
  });

  it('A1/A2/A3: deleteSave 发送 DELETE /api/cabin/life/save', async () => {
    let capturedMethod = '';
    let capturedUrl = '';

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedUrl = url;
      capturedMethod = init?.method ?? 'GET';
      return new Response(JSON.stringify({ deleted: true, owner: 'u123' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const res = await deleteSave();
    expect(capturedUrl).toBe('/api/cabin/life/save');
    expect(capturedMethod).toBe('DELETE');
    expect(res.deleted).toBe(true);
  });

  it('A1/A2/A3: act 发送 POST /api/cabin/life/action 动作通道', async () => {
    let capturedBody: any = null;
    let capturedMethod = '';
    let capturedUrl = '';

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedUrl = url;
      capturedMethod = init?.method ?? 'GET';
      capturedBody = init?.body ? JSON.parse(init.body as string) : null;
      return new Response(
        JSON.stringify({
          action: 'gather',
          save: fixture.save,
          hud_line: '第1天 上午 晴 · 0 金币',
          receipt: { node_id: 'herb_patch', gathered: true },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });

    const res = await act('gather', { node_id: 'herb_patch' });
    expect(capturedUrl).toBe('/api/cabin/life/action');
    expect(capturedMethod).toBe('POST');
    expect(capturedBody).toEqual({ action: 'gather', node_id: 'herb_patch' });
    expect(res.action).toBe('gather');
  });

  it('A1/A2/A3: meta 发送 GET /api/cabin/life/meta 获取主题与动作元数据', async () => {
    let capturedMethod = '';
    let capturedUrl = '';

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedUrl = url;
      capturedMethod = init?.method ?? 'GET';
      return new Response(
        JSON.stringify({
          themes: [{ id: 'forest', label: '老林子' }],
          actions: ['gather', 'craft'],
          writable_settings: ['active_theme'],
          max_hearts: 10,
          max_gifts_per_day: 1,
          daily_node_limit: 3,
          daily_reset_minute: 360,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });

    const m = await meta();
    expect(capturedUrl).toBe('/api/cabin/life/meta');
    expect(capturedMethod).toBe('GET');
    expect(m.actions).toContain('gather');
    expect(m.max_hearts).toBe(10);
  });

  it('A4: 非 2xx 响应必须抛出 ApiError，绝不静默返回 undefined 或伪造成功', async () => {
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          error: {
            code: 'life_unknown_action',
            message: '未知 action flying',
          },
        }),
        { status: 422, headers: { 'Content-Type': 'application/json' } },
      );
    });

    await expect(act('flying')).rejects.toThrow('未知 action flying');
    await expect(act('flying')).rejects.toBeInstanceOf(ApiError);
  });

  it('A5: 409 / 412 乐观锁与版本冲突由 isConflictError 准确识别', async () => {
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          error: {
            code: 'life_version_conflict',
            message: '存档版本已变（当前 2，你基于 1），请重新读取',
          },
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      );
    });

    try {
      await putSave({ expected_version: 1 });
      expect.fail('应抛出冲突错误');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
      expect(isConflictError(err)).toBe(true);
    }
  });

  it('A6: snapshot 数据类型字段与 persistence 序列化结构逐字对齐', async () => {
    const s: LifeSnapshot = {
      save: fixture.save as any,
      hud_line: '第1天 上午 晴 · 0 金币',
      npcs: fixture.npc_rows as any,
      shop: fixture.shop_rows as any,
      craft: fixture.craft_rows as any,
      gather: fixture.gather_rows as any,
      version: 1,
    };

    expect(s.save).toHaveProperty('owner');
    expect(s.save).toHaveProperty('theme');
    expect(s.save).toHaveProperty('clock');
    expect(s.save).toHaveProperty('weather');
    expect(s.save).toHaveProperty('bag');
    expect(s.save).toHaveProperty('coins');
    expect(s.save).toHaveProperty('skill_exp');
    expect(s.save).toHaveProperty('affinity');
    expect(s.save).toHaveProperty('gifts_today');
    expect(s.save).toHaveProperty('shop');
    expect(s.save).toHaveProperty('quest_log');
    expect(s.save).toHaveProperty('gather_counts');
    expect(s.save).toHaveProperty('version');
    expect(s).toHaveProperty('hud_line');
    expect(s).toHaveProperty('npcs');
    expect(s).toHaveProperty('shop');
    expect(s).toHaveProperty('craft');
    expect(s).toHaveProperty('gather');
    expect(s).toHaveProperty('version');
  });
});
