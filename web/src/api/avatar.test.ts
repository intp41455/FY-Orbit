// W11 前端 API 客户端单测 —— 端点接线 + 诚实语义。
//
// 重点不是"能不能调通"，而是**错误语义不许被前端吞掉**：
//  - 404（还没生成过）是空态，不是故障；页面必须能区分；
//  - 4xx 的后端错误码必须能取到并展示，不许 catch 之后 return 空对象；
//  - 请求体绝不含 owner_id（owner 只从认证层取，客户端传了会被 422）。

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  SHARE_BADGE_FIELDS,
  avatarErrorCode,
  confirmAvatar,
  createShareCard,
  generateAvatar,
  getHouseAvatar,
  getMyAvatar,
  isAvatarNotFound,
} from './avatar';

const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>();

function mockOnce(body: unknown, status = 200) {
  return fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1)!;
  return [String(call[0]), call[1] ?? {}];
}

describe('W11 · avatarApi 端点接线', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    document.querySelectorAll('meta[name="csrf-token"]').forEach((el) => el.remove());
  });

  it('生成走 POST /api/avatar/generate', async () => {
    mockOnce({});
    await generateAvatar({ mbti: 'INFJ', bazi_element: '木' });
    const [url, init] = lastCall();
    expect(url).toBe('/api/avatar/generate');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      portrait: { mbti: 'INFJ', bazi_element: '木' },
      overrides: null,
    });
  });

  it('生成带微调时 overrides 被送进请求体', async () => {
    mockOnce({});
    await generateAvatar({ mbti: 'INFJ' }, { hair_style: 'bob', hue_shift: 2 });
    const [, init] = lastCall();
    expect(JSON.parse(String(init.body)).overrides).toEqual({ hair_style: 'bob', hue_shift: 2 });
  });

  it('绝不发送 owner_id（owner 只从认证层取）', async () => {
    mockOnce({});
    // 故意多传一个 owner_id：类型上它不在 PortraitInput 里，这里用 as any 模拟
    // 客户端 bug，确认我们**没有**自动附加任何身份字段。
    await generateAvatar({ mbti: 'INFJ', ...( { owner_id: 'someone-else' } as object) } as never);
    const [, init] = lastCall();
    expect(String(init.body)).not.toContain('owner_id');
  });

  it('确认走 PUT /api/avatar/confirm，字段是下划线命名', async () => {
    mockOnce({});
    await confirmAvatar({
      expectedVersion: 3,
      likenessScore: 8,
      likenessNote: '挺像的',
      isHouseAvatar: true,
    });
    const [url, init] = lastCall();
    expect(url).toBe('/api/avatar/confirm');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({
      expected_version: 3,
      likeness_score: 8,
      likeness_note: '挺像的',
      is_house_avatar: true,
    });
  });

  it('确认时缺省的自评字段显式发 null（不是 undefined 被后端当缺省）', async () => {
    mockOnce({});
    await confirmAvatar({ expectedVersion: 1 });
    const [, init] = lastCall();
    const body = JSON.parse(String(init.body));
    expect(body.likeness_score).toBeNull();
    expect(body.likeness_note).toBeNull();
  });

  it('读当前档案走 GET /api/avatar/me', async () => {
    mockOnce({});
    await getMyAvatar();
    const [url, init] = lastCall();
    expect(url).toBe('/api/avatar/me');
    expect(init.method ?? 'GET').toBe('GET');
  });

  it('分享卡走 POST /api/avatar/share-card，默认零勾选', async () => {
    mockOnce({});
    await createShareCard({ badges: [] });
    const [url, init] = lastCall();
    expect(url).toBe('/api/avatar/share-card');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body)).badges).toEqual([]);
  });

  it('分享卡只把勾选字段送上去', async () => {
    mockOnce({});
    await createShareCard({ badges: ['mbti', 'element'], displayName: '小腾子' });
    const [, init] = lastCall();
    const body = JSON.parse(String(init.body));
    expect(body.badges).toEqual(['mbti', 'element']);
    expect(body.display_name).toBe('小腾子');
  });

  it('小屋消费端点走 GET /api/avatar/house', async () => {
    mockOnce({});
    await getHouseAvatar();
    const [url] = lastCall();
    expect(url).toBe('/api/avatar/house');
  });
});

describe('W11 · 诚实错误语义', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    document.querySelectorAll('meta[name="csrf-token"]').forEach((el) => el.remove());
  });

  it('徽章白名单外字段由后端 422，前端抛 ApiError 而不是静默忽略', async () => {
    mockOnce(
      { error: { code: 'validation_failed', message: 'Badge fields not allowed', details: {} } },
      422,
    );
    // 白名单外的字段在 TS 上不可传，这里模拟运行时收到脏数据
    await expect(createShareCard({ badges: ['not_a_field' as never] })).rejects.toMatchObject({
      status: 422,
    });
  });

  it('404 被识别为「空态」而不是故障', async () => {
    mockOnce({ error: { code: 'not_found', message: 'no avatar yet', details: {} } }, 404);
    const err = await getMyAvatar().catch((e: unknown) => e);
    expect(isAvatarNotFound(err)).toBe(true);
  });

  it('小屋 404（还没有专属小人）同样走空态分支', async () => {
    mockOnce({ error: { code: 'not_found', message: 'no house avatar', details: {} } }, 404);
    const err = await getHouseAvatar().catch((e: unknown) => e);
    expect(isAvatarNotFound(err)).toBe(true);
  });

  it('401/403 不是空态（必须真的提示用户）', async () => {
    mockOnce({ error: { code: 'unauthenticated', message: 'login required', details: {} } }, 401);
    const err = await getMyAvatar().catch((e: unknown) => e);
    expect(isAvatarNotFound(err)).toBe(false);
  });

  it('409（乐观锁冲突）也不是空态', async () => {
    mockOnce({ error: { code: 'conflict', message: 'stale version', details: {} } }, 409);
    const err = await confirmAvatar({ expectedVersion: 1 }).catch((e: unknown) => e);
    expect(isAvatarNotFound(err)).toBe(false);
  });

  it('能取到后端错误码用于展示', async () => {
    mockOnce({ error: { code: 'avatar_not_confirmed', message: 'x', details: {} } }, 409);
    const err = await createShareCard({ badges: [] }).catch((e: unknown) => e);
    expect(avatarErrorCode(err)).toBe('avatar_not_confirmed');
  });

  it('取不到错误码时返回 null（页面据此降级为「未知错误」，不编一个码）', () => {
    expect(avatarErrorCode(new Error('boom'))).toBeNull();
    expect(avatarErrorCode(null)).toBeNull();
  });

  it('网络失败是 NetworkError，不是静默成功', async () => {
    fetchMock.mockRejectedValue(new TypeError('failed to fetch'));
    await expect(getMyAvatar()).rejects.toMatchObject({ kind: expect.stringMatching(/offline|failed/) });
  });
});

describe('W11 · 白名单与后端一致', () => {
  it('前端白名单是 7 项（与后端 SHARE_BADGE_FIELDS 对齐）', () => {
    expect([...SHARE_BADGE_FIELDS].sort()).toEqual(
      ['asc_sign', 'day_master', 'element', 'mbti', 'moon_sign', 'name', 'sun_sign'].sort(),
    );
  });
});